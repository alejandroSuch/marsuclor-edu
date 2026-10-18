#!/usr/bin/env python3
"""Validate content, prepare Quarto pages, and print rendered HTML to PDF."""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import shutil
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

import yaml
from podcast import parse_script, resolve_config, should_generate, spoken_text, topic_folder
from tts import (PiperSynthesizer, audio_path, build_audio, context as tts_context,
                 download_voice, model_path, play_audio, resolve_tts_config, resolve_voice)
from storage import StorageError, check_record, object_keys
from storage.local import LocalStorage
from storage.r2 import R2Storage
from podcast_publish import (manifest as write_podcast_manifest, resolve as resolve_podcast,
                             script_references, sync_one, verify_public_audio, write_local_provenance)
from podcast_web import local_preview, player_markup, published_audio

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content"
GENERATED = ROOT / "generated"
SITE = ROOT / "site"


def read_yaml(path: Path):
    with path.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def topics():
    return sorted(CONTENT.glob("*/*/*/topic.yml"))


def course_label(course: str, language: str) -> str:
    match = re.fullmatch(r"([1-4])-eso", course)
    if not match:
        raise ValueError(f"Curs no vàlid: {course}")
    number = int(match.group(1))
    if language == "es":
        return f"{number}.º de ESO"
    return f"{number}{ {1: 'r', 2: 'n', 3: 'r', 4: 't'}[number]} d'ESO"


def data_for(path: Path):
    folder = path.parent
    return (
        read_yaml(path),
        read_yaml(folder / "notes.yml"),
        read_yaml(folder / "exercises.yml"),
        read_yaml(folder / "flashcards.yml"),
    )


def optional_data(folder: Path):
    vocabulary = folder / "vocabulary.yml"
    diagrams = folder / "diagrams.yml"
    return (
        read_yaml(vocabulary) if vocabulary.exists() else None,
        read_yaml(diagrams) if diagrams.exists() else None,
    )


def pasapalabra_data(folder: Path):
    activity = folder / "pasapalabra.yml"
    return read_yaml(activity) if activity.exists() else None


def unaccented(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFD", value.upper()) if not unicodedata.combining(char))


def validate():
    config = read_yaml(ROOT / "config/subjects.yml")
    subjects = config["subjects"]
    errors = []
    for subject in subjects:
        try:
            resolve_config(config, subject, {})
            resolve_tts_config(config, subject, {})
        except ValueError as exc:
            errors.append(f"config/subjects.yml ({subject}): {exc}")
    if not topics():
        errors.append("No hi ha cap tema")
    for path in topics():
        label = str(path.relative_to(ROOT))
        try:
            topic, notes, exercises, cards = data_for(path)
            policy = resolve_config(config, path.parts[-4], topic)
            tts_settings = resolve_tts_config(config, path.parts[-4], topic)
            if topic.get("teaching_language") in tts_settings["voices"]:
                resolve_voice(tts_settings, topic["teaching_language"])
            script = path.parent / "podcast" / "script.md"
            if script.exists():
                parse_script(script, topic, policy)
            vocabulary, diagrams = optional_data(path.parent)
            pasapalabra = pasapalabra_data(path.parent)
            subject, course, slug = path.parts[-4:-1]
            if topic.get("subject") != subject or subject not in subjects:
                errors.append(f"{label}: assignatura inconsistent")
            if topic.get("course") != course:
                errors.append(f"{label}: curs inconsistent")
            if not re.fullmatch(r"[a-z0-9-]+", slug):
                errors.append(f"{label}: nom de tema no vàlid")
            course_label(course, topic.get("teaching_language", ""))
            expected = subjects.get(subject, {}).get("teaching_language")
            language = topic.get("teaching_language")
            if expected == "configurable":
                if language not in {"ca-valencia", "es", "fr", "en"}:
                    errors.append(f"{label}: indica una llengua d'ensenyament vàlida")
            elif language != expected:
                errors.append(f"{label}: llengua d'ensenyament incorrecta")
            if not topic.get("title") or not topic.get("intro"):
                errors.append(f"{label}: falten títol o introducció")
            objectives = {o["id"] for o in topic["objectives"]}
            if len(objectives) != len(topic["objectives"]) or not objectives:
                errors.append(f"{label}: objectius buits o repetits")
            if any(not objective.get("text") for objective in topic["objectives"]):
                errors.append(f"{label}: objectiu sense text")
            sources = {s["id"] for s in topic["sources"]}
            if len(sources) != len(topic["sources"]) or not sources:
                errors.append(f"{label}: fonts buides o repetides")
            for source in topic["sources"]:
                if not source.get("title") or not source.get("type"):
                    errors.append(f"{label}: font incompleta")
                if source["type"] == "external" and urlparse(source.get("url", "")).scheme != "https":
                    errors.append(f"{label}: font externa sense URL HTTPS")
            coverage = {"notes": set(), "exercises": set(), "cards": set()}
            groups = [("notes", notes.get("sections", [])), ("exercises", exercises.get("exercises", [])), ("cards", cards.get("cards", []))]
            for group_name, rows in groups:
                if not rows:
                    errors.append(f"{label}: {group_name} buit")
                ids = [row.get("id") for row in rows] if group_name != "notes" else []
                if ids and len(ids) != len(set(ids)):
                    errors.append(f"{label}: identificadors repetits en {group_name}")
                text_field = {"notes": "body", "exercises": "question", "cards": "front"}[group_name]
                texts = [re.sub(r"\s+", " ", str(row.get(text_field, ""))).strip().casefold() for row in rows]
                if len(texts) != len(set(texts)):
                    errors.append(f"{label}: contingut repetit en {group_name}")
                for row in rows:
                    objective_ids = set(row.get("objective_ids", []))
                    source_ids = set(row.get("source_ids", []))
                    if not objective_ids or not objective_ids <= objectives:
                        errors.append(f"{label}: objectius invàlids en {group_name}")
                    if not source_ids or not source_ids <= sources:
                        errors.append(f"{label}: fonts invàlides en {group_name}")
                    coverage[group_name] |= objective_ids
                    fields = {"notes": ["title", "body"], "exercises": ["question", "answer"], "cards": ["front", "back"]}[group_name]
                    if any(not row.get(field) for field in fields):
                        errors.append(f"{label}: text incomplet en {group_name}")
            for group_name, covered in coverage.items():
                missing = objectives - covered
                if missing:
                    errors.append(f"{label}: {group_name} no cobrix {', '.join(sorted(missing))}")
            if pasapalabra:
                pasapalabra_groups = pasapalabra.get("groups", [])
                letters = [group.get("letter") for group in pasapalabra_groups]
                if not pasapalabra.get("title") or not pasapalabra.get("intro") or not pasapalabra_groups:
                    errors.append(f"{label}: pasapalabra incomplet")
                if len(letters) != len(set(letters)):
                    errors.append(f"{label}: lletres repetides en pasapalabra")
                clue_ids = []
                for group in pasapalabra_groups:
                    letter = group.get("letter", "")
                    mode = group.get("mode", "starts")
                    if not re.fullmatch(r"[A-Z]", letter) or mode not in {"starts", "contains"} or not group.get("clues"):
                        errors.append(f"{label}: grup de pasapalabra invàlid")
                        continue
                    for clue in group["clues"]:
                        clue_ids.append(clue.get("id"))
                        answer = str(clue.get("answer", ""))
                        normalized = unaccented(answer)
                        if not answer or not clue.get("clue") or (mode == "starts" and not normalized.startswith(letter)) or (mode == "contains" and letter not in normalized):
                            errors.append(f"{label}: pista o resposta invàlida en {clue.get('id')}")
                        if not set(clue.get("objective_ids", [])) or not set(clue["objective_ids"]) <= objectives:
                            errors.append(f"{label}: objectius invàlids en pasapalabra")
                        if not set(clue.get("source_ids", [])) or not set(clue["source_ids"]) <= sources:
                            errors.append(f"{label}: fonts invàlides en pasapalabra")
                if len(clue_ids) != len(set(clue_ids)) or not all(clue_ids):
                    errors.append(f"{label}: identificadors de pasapalabra buits o repetits")
            vocabulary_ids = set()
            if topic.get("exam_vocabulary") and not vocabulary:
                errors.append(f"{label}: falta vocabulary.yml per al vocabulari de l'examen")
            if vocabulary:
                terms = vocabulary.get("terms", [])
                vocabulary_ids = {entry["id"] for entry in terms}
                if len(vocabulary_ids) != len(terms):
                    errors.append(f"{label}: identificadors de vocabulari repetits")
                exam_terms = {entry["term"] for entry in terms if entry.get("exam")}
                expected_terms = set(topic.get("exam_vocabulary", []))
                if exam_terms != expected_terms or len(expected_terms) != len(topic.get("exam_vocabulary", [])):
                    errors.append(f"{label}: el vocabulari de l'examen no coincideix amb la llista de la font")
                if len(terms) != len({entry["term"] for entry in terms}):
                    errors.append(f"{label}: termes de vocabulari repetits")
                for entry in terms:
                    if not entry.get("definition") or not entry.get("category"):
                        errors.append(f"{label}: definició o categoria absent")
                    if not entry.get("source_ids") or not set(entry["source_ids"]) <= sources:
                        errors.append(f"{label}: font de vocabulari invàlida")
                covered_terms = set().union(*(set(row.get("vocabulary_ids", [])) for row in exercises["exercises"]))
                missing_terms = {entry["id"] for entry in terms if entry.get("exam")} - covered_terms
                if missing_terms:
                    errors.append(f"{label}: exercicis sense {', '.join(sorted(missing_terms))}")
            diagram_ids = set()
            if diagrams:
                diagram_ids = {entry["id"] for entry in diagrams.get("diagrams", [])}
                if len(diagram_ids) != len(diagrams["diagrams"]):
                    errors.append(f"{label}: identificadors de diagrama repetits")
                for entry in diagrams["diagrams"]:
                    svg = path.parent / "diagrams" / entry["file"]
                    if not svg.is_file() or svg.suffix != ".svg":
                        errors.append(f"{label}: diagrama absent: {entry['file']}")
                    else:
                        ET.parse(svg)
                    if not entry.get("objective_ids") or not set(entry["objective_ids"]) <= objectives:
                        errors.append(f"{label}: objectius de diagrama invàlids")
                    if not entry.get("source_ids") or not set(entry["source_ids"]) <= sources:
                        errors.append(f"{label}: font de diagrama invàlida")
                    if not set(entry.get("vocabulary_ids", [])) <= vocabulary_ids:
                        errors.append(f"{label}: vocabulari de diagrama invàlid")
            for group_name, rows in groups:
                for row in rows:
                    if not set(row.get("vocabulary_ids", [])) <= vocabulary_ids:
                        errors.append(f"{label}: vocabulari invàlid en {group_name}")
                    refs = row.get("diagram_ids", []) + ([row["diagram_id"]] if row.get("diagram_id") else [])
                    if not set(refs) <= diagram_ids:
                        errors.append(f"{label}: diagrama invàlid en {group_name}")
                    if group_name == "exercises":
                        if not row.get("kind"):
                            errors.append(f"{label}: tipus d'exercici absent")
                        space = row.get("answer_space_mm", 30)
                        if not isinstance(space, int) or not 8 <= space <= 80:
                            errors.append(f"{label}: espai de resposta invàlid")
        except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError, ET.ParseError) as exc:
            errors.append(f"{label}: {exc}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Validats {len(topics())} temes: objectius, llengües i fonts coberts.")


def esc(value):
    return html.escape(str(value), quote=True)


def frontmatter(title: str, language: str | None = None, toc: bool | None = None) -> str:
    language_line = f"lang: {language}\n" if language else ""
    toc_line = f"toc: {str(toc).lower()}\n" if toc is not None else ""
    return f"---\ntitle: {json.dumps(title, ensure_ascii=False)}\n{language_line}{toc_line}---\n\n"


def natural_key(value: str):
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", value)
    )


UI = {
    "ca-valencia": {
        "podcast": "Pòdcast", "pause": "Pausa per a pensar", "transcription": "Transcripció", "chapters": "Capítols",
        "notes": "Apunts", "exercises": "Exercicis", "solutions": "Solucionari",
        "cards": "Targetes", "pasapalabra": "Pasapalabra", "objectives": "Objectius",
        "study_tips": "Com estudiar-ho", "self_check": "Comprovació final",
        "exercise": "Exercici", "prompt": "Enunciat",
        "answer": "Solució", "show_answer": "Mostra la solució", "print": "Imprimix / Desa en PDF",
        "download": "Descarrega el PDF A4", "status": "Estat",
        "card_instruction": "Prem una targeta per veure la resposta. Per al PDF A4, imprimix a doble cara, amb gir pel costat llarg i escala del 100 %; els reversos estan reflectits horitzontalment per a retallar.",
        "card_action": "Prem per veure la resposta", "blank_diagram": "Esquema sense etiquetes",
        "vocabulary": "Vocabulari de l'examen", "support_terms": "Altres termes de suport",
        "review_tips": "Trucs de repàs", "topic_index": "Torna als temes",
        "all_subjects": "Totes les assignatures",
        "starts_with": "Comença per", "contains": "Conté la", "reveal_concept": "Mostra el concepte",
        "write_answer": "Resposta", "answer_key": "Solucionari del Pasapalabra",
    },
    "es": {
        "podcast": "Podcast", "pause": "Pausa para pensar", "transcription": "Transcripción", "chapters": "Capítulos",
        "notes": "Apuntes", "exercises": "Ejercicios", "solutions": "Solucionario",
        "cards": "Tarjetas", "pasapalabra": "Pasapalabra", "objectives": "Objetivos",
        "study_tips": "Cómo estudiarlo", "self_check": "Comprobación final",
        "exercise": "Ejercicio", "prompt": "Enunciado",
        "answer": "Solución", "show_answer": "Ver solución", "print": "Imprimir / Guardar en PDF",
        "download": "Descargar el PDF A4", "status": "Estado",
        "card_instruction": "Pulsa una tarjeta para ver la respuesta. Para el PDF A4, imprime a doble cara, girando por el borde largo y al 100 % de escala; los reversos están reflejados horizontalmente para recortar.",
        "card_action": "Pulsa para ver la respuesta", "blank_diagram": "Esquema sin etiquetas",
        "vocabulary": "Vocabulario del examen", "support_terms": "Otros términos de apoyo",
        "review_tips": "Trucos de repaso", "topic_index": "Volver a los temas",
        "all_subjects": "Todas las asignaturas",
        "starts_with": "Empieza por", "contains": "Contiene la", "reveal_concept": "Mostrar el concepto",
        "write_answer": "Respuesta", "answer_key": "Solucionario del Pasapalabra",
    },
}


def ui(language: str, key: str) -> str:
    return UI.get(language, UI["ca-valencia"])[key]


def page_header(topic, course, section):
    language = topic["teaching_language"]
    return (
        frontmatter(f"{section} · {topic['title']}", language)
        + f"**{esc(course_label(course, topic['teaching_language']))} · {esc(topic['title'])}**\n\n"
        + f"::: {{.callout-note}}\n{esc(topic['intro'])}\n:::\n\n"
    )


def pdf_link(relative: Path, filename: str, language: str) -> str:
    target = "../../../../pdf/" + relative.as_posix() + "/" + filename + ".pdf"
    return f'<p class="pdf-link"><a class="download-button" href="{esc(target)}">{ui(language, "download")}</a></p>\n\n'


def diagram_markup(diagrams_by_id, diagram_id: str, blank: bool = False) -> str:
    entry = diagrams_by_id[diagram_id]
    suffix = "-blank" if blank else ""
    return f"![{esc(entry['title'])}](diagrams/{diagram_id}{suffix}.svg){{.topic-diagram}}\n\n"


def copy_diagrams(folder: Path, dest: Path, diagrams):
    if not diagrams:
        return
    target = dest / "diagrams"
    target.mkdir()
    for entry in diagrams["diagrams"]:
        svg = (folder / "diagrams" / entry["file"]).read_text(encoding="utf-8")
        (target / f"{entry['id']}.svg").write_text(svg, encoding="utf-8")
        blank = re.sub(
            r'(<text class="diagram-answer"[^>]*>)[^<]*(</text>)',
            lambda match: match.group(1) + "________" + match.group(2),
            svg,
        )
        (target / f"{entry['id']}-blank.svg").write_text(blank, encoding="utf-8")


def vocabulary_markup(vocabulary, language: str):
    if not vocabulary:
        return ""
    exam_count = sum(bool(entry.get("exam")) for entry in vocabulary["terms"])
    support_count = len(vocabulary["terms"]) - exam_count
    out = ""
    for exam in (True, False):
        if exam and exam_count:
            out += f"## {ui(language, 'vocabulary')} ({exam_count})\n\n"
        elif not exam and support_count:
            out += f"## {ui(language, 'support_terms')}\n\n"
        current_category = None
        for entry in vocabulary["terms"]:
            if entry["exam"] != exam:
                continue
            if entry["category"] != current_category:
                current_category = entry["category"]
                out += f"### {esc(current_category)}\n\n"
            tag = f" <small>({esc(entry['editorial_tag'])})</small>" if entry.get("editorial_tag") else ""
            out += f'<div class="vocab-entry"><strong>{esc(entry["term"])}</strong> — {esc(entry["definition"])}{tag}</div>\n\n'
    if vocabulary.get("mnemonics"):
        out += f"### {ui(language, 'review_tips')}\n\n"
        out += "\n".join(f"- {esc(tip)}" for tip in vocabulary["mnemonics"]) + "\n\n"
    return out


def prepare(preview_audio: tuple[str, Path] | None = None):
    validate()
    if SITE.exists():
        for child in SITE.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    if GENERATED.exists():
        for child in GENERATED.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    else:
        GENERATED.mkdir()
    shutil.copytree(ROOT / "styles", GENERATED / "styles")
    (GENERATED / "_quarto.yml").write_text(
        "project:\n  type: website\n  output-dir: ../site\n  render:\n    - '*.qmd'\n    - 'courses/**/*.qmd'\n    - 'topics/**/*.qmd'\nwebsite:\n  title: Martina Edu\n"
        "  navbar:\n    left:\n      - href: index.qmd\n        text: Inici\n"
        "format:\n  html:\n    theme: cosmo\n    css: styles/site.css\n    toc: true\n    lang: ca\n"
        "    include-after-body:\n      - styles/cards.js\n      - styles/navigation.js\n      - styles/podcast.js\nexecute:\n  freeze: auto\n",
        encoding="utf-8",
    )
    config = read_yaml(ROOT / "config/subjects.yml")
    manifest_path = ROOT / "audio" / "published.json"
    published = read_yaml(manifest_path) if manifest_path.exists() else {}
    if not isinstance(published, dict):
        raise ValueError("El manifest de pòdcasts ha de ser un mapa")
    subjects = config["subjects"]
    index_topics = {}
    for path in topics():
        topic, notes, exercises, cards = data_for(path)
        vocabulary, diagrams = optional_data(path.parent)
        pasapalabra = pasapalabra_data(path.parent)
        policy = resolve_config(config, path.parts[-4], topic)
        script_path = path.parent / "podcast" / "script.md"
        podcast = parse_script(script_path, topic, policy) if script_path.exists() else None
        diagrams_by_id = {entry["id"]: entry for entry in diagrams["diagrams"]} if diagrams else {}
        subject, course, slug = path.parts[-4:-1]
        language = topic["teaching_language"]
        rel = Path("topics") / subject / course / slug
        dest = GENERATED / rel
        dest.mkdir(parents=True)
        copy_diagrams(path.parent, dest, diagrams)
        materials = [(ui(language, key), key) for key in ("notes", "exercises", "cards")]
        if pasapalabra:
            materials.append((ui(language, "pasapalabra"), "pasapalabra"))
        if podcast:
            materials.append((ui(language, "podcast"), "podcast"))
        course_path = Path("courses") / subject / course / "index.qmd"
        nav = (
            '<div class="material-nav">\n\n'
            + f"[← {ui(language, 'topic_index')}](../../../../{course_path.as_posix()}) · "
            + " | ".join(f"[{name}]({file}.qmd)" for name, file in materials)
            + '\n\n</div>'
        )
        index_topics.setdefault(subject, {}).setdefault(course, []).append((topic["title"], language, rel, bool(pasapalabra), bool(podcast)))
        base = page_header(topic, course, ui(language, "notes")) + pdf_link(rel, "notes", language) + nav + f"\n\n## {ui(language, 'objectives')}\n\n"
        base += "\n".join(f"- {esc(o['text'])}" for o in topic["objectives"]) + "\n\n"
        for row in notes["sections"]:
            base += f"## {esc(row['title'])}\n\n{row['body']}\n\n"
            for diagram_id in row.get("diagram_ids", []):
                base += diagram_markup(diagrams_by_id, diagram_id)
        base += vocabulary_markup(vocabulary, language)
        if notes.get("study_tips"):
            base += f"## {ui(language, 'study_tips')}\n\n" + "\n".join(
                f"{number}. {esc(tip)}" for number, tip in enumerate(notes["study_tips"], 1)
            ) + "\n\n"
        if notes.get("self_check"):
            base += f"## {ui(language, 'self_check')}\n\n" + "\n".join(
                f"- [ ] {esc(item)}" for item in notes["self_check"]
            ) + "\n\n"
        (dest / "notes.qmd").write_text(base, encoding="utf-8")
        exercise_page = page_header(topic, course, ui(language, "exercises")) + pdf_link(rel, "exercises", language) + nav + "\n\n"
        solution_page = f'::: {{.print-solutions}}\n## {ui(language, "solutions")}\n\n'
        for n, row in enumerate(exercises["exercises"], 1):
            exercise_page += f"::: {{.exercise style=\"--answer-space:{row.get('answer_space_mm', 30)}mm\"}}\n### {ui(language, 'exercise')} {n}\n\n{esc(row['question'])}\n\n"
            solution_page += (
                f"::: {{.key-item}}\n### {ui(language, 'exercise')} {n}\n\n"
                f"::: {{.solution-answer}}\n"
            )
            if row.get("diagram_id"):
                exercise_page += diagram_markup(diagrams_by_id, row["diagram_id"], blank=True)
                solution_page += diagram_markup(diagrams_by_id, row["diagram_id"])
            exercise_page += f'<details class="exercise-solution"><summary>{ui(language, "show_answer")}</summary>\n\n{esc(row["answer"])}\n\n'
            if row.get("diagram_id"):
                exercise_page += diagram_markup(diagrams_by_id, row["diagram_id"])
            exercise_page += '</details>\n\n'
            exercise_page += ':::\n\n'
            solution_page += f"{esc(row['answer'])}\n\n:::\n\n:::\n\n"
        exercise_page += solution_page + ':::\n'
        (dest / "exercises.qmd").write_text(exercise_page, encoding="utf-8")
        card_page = page_header(topic, course, ui(language, "cards")) + pdf_link(rel, "cards", language) + nav + "\n\n"
        card_page += ui(language, "card_instruction") + "\n\n"
        card_page += '<div class="card-grid web-cards">\n'
        for row in cards["cards"]:
            diagram = (
                f'<img class="card-diagram" src="diagrams/{esc(row["diagram_id"])}-blank.svg" alt="{ui(language, "blank_diagram")}">'
                if row.get("diagram_id") else ""
            )
            card_page += (
                '<button type="button" class="study-card" aria-pressed="false" '
                f'aria-label="{esc(row["front"])}. {ui(language, "card_action")}">'
                f'<span class="card-front">{esc(row["front"])}{diagram}</span>'
                f'<span class="card-back">{esc(row["back"])}</span></button>\n'
            )
        card_page += "</div>\n"
        card_page += '<div class="flashcard-print">\n'
        for sheet_number, start in enumerate(range(0, len(cards["cards"]), 8), 1):
            chunk = cards["cards"][start:start + 8]
            for face in ("front", "back"):
                card_page += f'<div class="print-sheet {face}-sheet"><div class="sheet-title">{esc(topic["title"])} · {"Anversos" if face == "front" else "Reversos"} {sheet_number}</div>\n'
                ordered = chunk if face == "front" else [item for index in range(0, len(chunk), 2) for item in chunk[index:index + 2][::-1]]
                for row in ordered:
                    content = row["front"] if face == "front" else row["back"]
                    card_page += f'<div class="cut-card" data-card-id="{esc(row["id"])}"><span class="cut-card-id">{esc(row["id"])}</span><span>{esc(content)}</span>'
                    if face == "front" and row.get("diagram_id"):
                        card_page += f'<img class="card-diagram" src="diagrams/{esc(row["diagram_id"])}-blank.svg" alt="{ui(language, "blank_diagram")}">'
                    card_page += '</div>\n'
                card_page += '</div>\n'
        card_page += '</div>\n'
        (dest / "cards.qmd").write_text(card_page, encoding="utf-8")
        if pasapalabra:
            game = page_header(topic, course, pasapalabra["title"]) + pdf_link(rel, "pasapalabra", language) + nav + "\n\n"
            game += f'<p class="pasapalabra-intro">{esc(pasapalabra["intro"])}</p>\n\n'
            for group in pasapalabra["groups"]:
                letter = group["letter"]
                prompt = ui(language, "contains" if group.get("mode") == "contains" else "starts_with")
                game += f'<section class="pasapalabra-group" aria-labelledby="letter-{esc(letter)}">\n<h2 id="letter-{esc(letter)}">{esc(letter)}</h2>\n<ol>\n'
                for clue in group["clues"]:
                    game += (
                        f'<li class="pasapalabra-item" id="{esc(clue["id"])}">'
                        f'<span class="pasapalabra-clue">{esc(prompt)} {esc(letter)}. {esc(clue["clue"])}</span>'
                        f'<details class="pasapalabra-reveal"><summary>{ui(language, "reveal_concept")}</summary>'
                        f'<strong class="pasapalabra-answer">{esc(clue["answer"])}</strong></details>'
                        f'<span class="pasapalabra-write">{ui(language, "write_answer")}: <span></span></span>'
                        '</li>\n'
                    )
                game += '</ol>\n</section>\n'
            game += f'<section class="pasapalabra-key">\n<h2>{ui(language, "answer_key")}</h2>\n'
            for group in pasapalabra["groups"]:
                game += f'<div class="pasapalabra-key-group"><h3>{esc(group["letter"])}</h3><ol>\n'
                for clue in group["clues"]:
                    game += f'<li>{esc(clue["answer"])}</li>\n'
                game += '</ol></div>\n'
            game += '</section>\n'
            (dest / "pasapalabra.qmd").write_text(game, encoding="utf-8")
        if podcast:
            preview = frontmatter(f"{ui(language, 'podcast')} · {topic['title']}", language)
            preview += pdf_link(rel, "podcast", language) + nav + "\n\n"
            reference = f"{subject}/{course}/{slug}"
            chapter_titles = [event["title"] for event in podcast["events"] if event["type"] == "chapter"]
            audio = published_audio(published, reference, chapter_titles)
            if preview_audio and preview_audio[0] == reference:
                audio = local_preview(preview_audio[1], reference)
            if audio:
                preview += player_markup(audio, language)
            preview += f'<section class="podcast-transcription" id="transcription" aria-label="{ui(language, "transcription")}">\n'
            preview += f'<h2>{ui(language, "transcription")}</h2>\n'
            preview += f'<p>{esc(podcast["metadata"]["title"])}</p>\n\n'
            preview += f'<nav class="podcast-transcript-index" aria-label="{ui(language, "chapters")}"><ol>\n'
            for number, title in enumerate(chapter_titles, 1):
                preview += f'<li><a href="#chapter-{number}">{esc(title)}</a></li>\n'
            preview += '</ol></nav>\n\n'
            chapter_number = 0
            for event in podcast["events"]:
                if event["type"] == "chapter":
                    chapter_number += 1
                    preview += f'<h3 id="chapter-{chapter_number}">{esc(event["title"])}</h3>\n\n'
                elif event["type"] == "speech":
                    preview += f"<p>{esc(event['text'])}</p>\n\n"
            preview += '</section>\n\n'
            (dest / "podcast.qmd").write_text(preview, encoding="utf-8")
    index = frontmatter("Martina Edu", toc=False) + "Materials per a estudiar en línia i imprimir en A4.\n\n## Assignatures\n\n"
    ordered_subjects = sorted(index_topics, key=lambda subject: subjects[subject]["label"].casefold())
    index += '<div class="subject-index">\n'
    for subject in ordered_subjects:
        label = subjects[subject]["label"]
        index += f'<section class="subject-entry"><h3>{esc(label)}</h3><ul>\n'
        for course in sorted(index_topics[subject], key=lambda value: int(value.split("-")[0])):
            rows = sorted(index_topics[subject][course], key=lambda row: natural_key(row[0]))
            language = rows[0][1]
            course_rel = Path("courses") / subject / course
            index += f'<li><a class="course-link" href="{course_rel.as_posix()}/index.html">{esc(course_label(course, language))}</a></li>\n'
            title = f"{label} · {course_label(course, language)}"
            course_page = frontmatter(title, language, toc=False)
            course_page += f"[← {ui(language, 'all_subjects')}](../../../index.qmd)\n\n"
            for topic_title, topic_language, rel, has_pasapalabra, has_podcast in rows:
                course_page += f"::: {{.topic-entry}}\n## {esc(topic_title)}\n\n"
                material_keys = ("notes", "exercises", "cards", "pasapalabra") if has_pasapalabra else ("notes", "exercises", "cards")
                if has_podcast:
                    material_keys += ("podcast",)
                course_page += " | ".join(
                    f"[{ui(topic_language, key)}](../../../{rel.as_posix()}/{key}.qmd)"
                    for key in material_keys
                ) + "\n\n:::\n\n"
            course_dest = GENERATED / course_rel
            course_dest.mkdir(parents=True, exist_ok=True)
            (course_dest / "index.qmd").write_text(course_page, encoding="utf-8")
        index += "</ul></section>\n"
    index += "</div>\n"
    (GENERATED / "index.qmd").write_text(index, encoding="utf-8")
    print(f"Preparades {len(topics())} pàgines de tema per a Quarto.")


def pdf():
    from playwright.sync_api import sync_playwright

    pages = sorted((SITE / "topics").rglob("*.html"))
    if not pages:
        raise SystemExit("No hi ha HTML renderitzat en site/topics")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for page_path in pages:
            relative = page_path.relative_to(SITE).with_suffix(".pdf")
            output = SITE / "pdf" / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            page = browser.new_page()
            page.goto(page_path.resolve().as_uri(), wait_until="load")
            page.pdf(path=str(output), format="A4", print_background=True, prefer_css_page_size=True)
            page.close()
        browser.close()
    print(f"Generats {len(pages)} PDF en site/pdf/.")


def verify():
    from playwright.sync_api import sync_playwright
    from pypdf import PdfReader

    course_topics = {}
    course_links = {}
    extra_pages = 0
    for path in topics():
        subject, course, _ = path.parts[-4:-1]
        course_topics[(subject, course)] = course_topics.get((subject, course), 0) + 1
        has_pasapalabra = (path.parent / "pasapalabra.yml").exists()
        has_podcast = (path.parent / "podcast" / "script.md").exists()
        course_links[(subject, course)] = course_links.get((subject, course), 0) + 3 + int(has_pasapalabra) + int(has_podcast)
        extra_pages += int(has_pasapalabra) + int(has_podcast)
    pages = sorted((SITE / "topics").rglob("*.html"))
    if not pages:
        raise SystemExit("Falten pàgines HTML")
    if len(pages) != 3 * len(topics()) + extra_pages or any(page.name not in {"notes.html", "exercises.html", "cards.html", "pasapalabra.html", "podcast.html"} for page in pages):
        raise SystemExit("El nombre de pàgines web per tema no és correcte")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        home = browser.new_page()
        home.goto((SITE / "index.html").resolve().as_uri(), wait_until="load")
        if (
            home.locator(".subject-entry").count() != len({subject for subject, _ in course_topics})
            or home.locator(".course-link").count() != len(course_topics)
            or home.locator(".topic-entry").count()
        ):
            raise SystemExit("L'índex principal no mostra assignatures i cursos correctament")
        for (subject, course), topic_count in course_topics.items():
            link = f"courses/{subject}/{course}/index.html"
            if home.locator(f'.course-link[href="{link}"]').count() != 1:
                raise SystemExit(f"Falta el curs {subject}/{course} a l'índex principal")
            course_page = browser.new_page()
            course_page.goto((SITE / link).resolve().as_uri(), wait_until="load")
            if (
                course_page.locator(".topic-entry").count() != topic_count
                or course_page.locator(".topic-entry p a").count() != course_links[(subject, course)]
            ):
                raise SystemExit(f"Falten temes o enllaços a {subject}/{course}")
            course_page.close()
        home.close()
        for page_path in pages:
            page = browser.new_page()
            page.goto(page_path.resolve().as_uri(), wait_until="load")
            relative = page_path.relative_to(SITE).parts
            folder = CONTENT.joinpath(*relative[1:-1])
            if page_path.name == "podcast.html":
                topic = read_yaml(folder / "topic.yml")
                policy = resolve_config(read_yaml(ROOT / "config/subjects.yml"), relative[1], topic)
                parsed = parse_script(folder / "podcast" / "script.md", topic, policy)
                chapter_count = sum(event["type"] == "chapter" for event in parsed["events"])
                if (page.locator(".podcast-transcription h3").count() != chapter_count
                        or page.locator(".podcast-transcript-index li").count() != chapter_count
                        or page.locator(".podcast-pause").count()):
                    raise SystemExit(f"Vista del pòdcast incompleta: {page_path}")
                if page.locator("main").inner_text().find(next(event["text"] for event in parsed["events"] if event["type"] == "speech")) < 0:
                    raise SystemExit(f"Text narrat absent: {page_path}")
                if page.locator(".podcast-player").count():
                    if (page.locator(".podcast-player audio[controls]").count() != 1
                            or page.locator(".podcast-speed option").count() != 4
                            or page.locator('.podcast-actions a[href*="download=1"]').count() != 1):
                        raise SystemExit(f"Controls de pòdcast incomplets: {page_path}")
                    page.locator(".podcast-speed").select_option("1.25")
                    if page.locator("audio").evaluate("audio => audio.playbackRate") != 1.25:
                        raise SystemExit(f"Velocitat de pòdcast incorrecta: {page_path}")
                    page.emulate_media(media="print")
                    if page.locator(".podcast-player").is_visible() or not page.locator(".podcast-transcription").is_visible():
                        raise SystemExit(f"Impressió del pòdcast incorrecta: {page_path}")
                    page.emulate_media(media="screen")
            links = page.locator('a[href$=".pdf"]')
            if links.count() != 1:
                raise SystemExit(f"Enllaç PDF absent o duplicat: {page_path}")
            pdf_path = (page_path.parent / links.first.get_attribute("href")).resolve()
            if not pdf_path.is_file() or pdf_path.stat().st_size < 1000:
                raise SystemExit(f"PDF absent o buit: {pdf_path}")
            reader = PdfReader(str(pdf_path))
            if not reader.pages or any(
                abs(float(pdf_page.mediabox.width) - 595) > 3
                or abs(float(pdf_page.mediabox.height) - 842) > 3
                for pdf_page in reader.pages
            ):
                raise SystemExit(f"Pàgines PDF fora de format A4: {pdf_path}")
            if not page.locator("img.topic-diagram, img.card-diagram").evaluate_all(
                "images => images.every(image => image.complete && image.naturalWidth > 0)"
            ):
                raise SystemExit(f"Diagrama absent a {page_path}")
            if page_path.name == "exercises.html":
                exercise_count = len(read_yaml(folder / "exercises.yml")["exercises"])
                if page.locator(".exercise").count() != exercise_count or page.locator(".exercise-solution").count() != exercise_count:
                    raise SystemExit("Falten exercicis o solucions en l'HTML")
                if page.locator(".answer-space, .print-button").count() or page.locator(".print-solutions").evaluate("element => getComputedStyle(element).display") != "none":
                    raise SystemExit("L'HTML mostra elements reservats al PDF")
                if page.locator(".exercise").first.evaluate("element => getComputedStyle(element).borderBottomStyle") == "none":
                    raise SystemExit("Falta la separació entre exercicis")
                first_solution = page.locator(".exercise-solution").first
                first_solution.locator("summary").click()
                if not first_solution.evaluate("element => element.open"):
                    raise SystemExit("La solució no es mostra en fer clic")
            if page_path.name == "exercises.html":
                items = page.locator(".key-item")
                exercise_count = len(read_yaml(folder / "exercises.yml")["exercises"])
                if items.count() != exercise_count:
                    raise SystemExit("Falten solucions en l'HTML")
                for number in range(1, exercise_count + 1):
                    item = items.nth(number - 1)
                    if (
                        item.locator("h3").inner_text() != f"{ui(read_yaml(folder / 'topic.yml')['teaching_language'], 'exercise')} {number}"
                        or item.locator(".solution-prompt").count() != 0
                        or item.locator(".solution-answer").count() != 1
                        or item.evaluate("element => getComputedStyle(element).borderBottomStyle") == "none"
                    ):
                        raise SystemExit(f"Presentació incompleta de la solució {number}")
            if page_path.name == "cards.html":
                card = page.locator(".study-card").first
                if card.count() == 0:
                    raise SystemExit("No hi ha targetes")
                card.click()
                if card.get_attribute("aria-pressed") != "true":
                    raise SystemExit("La targeta no mostra la resposta")
                card_rows = read_yaml(folder / "flashcards.yml")["cards"]
                if len(reader.pages) != 2 * math.ceil(len(card_rows) / 8):
                    raise SystemExit("Els anversos i reversos no ocupen parelles de pàgines A4")
                ids = [row["id"] for row in card_rows]
                pattern = re.compile(r"\b(?:" + "|".join(map(re.escape, ids)) + r")\b")
                for sheet_number, start in enumerate(range(0, len(ids), 8)):
                    front = ids[start:start + 8]
                    back = [item for index in range(0, len(front), 2) for item in front[index:index + 2][::-1]]
                    found_front = pattern.findall(reader.pages[2 * sheet_number].extract_text())
                    found_back = pattern.findall(reader.pages[2 * sheet_number + 1].extract_text())
                    if found_front != front or found_back != back:
                        raise SystemExit(f"Anversos i reversos desalineats al full {sheet_number + 1}")
            if page_path.name == "pasapalabra.html":
                activity = pasapalabra_data(folder)
                clue_count = sum(len(group["clues"]) for group in activity["groups"])
                if page.locator(".pasapalabra-item").count() != clue_count or page.locator(".pasapalabra-key li").count() != clue_count:
                    raise SystemExit("Falten pistes o respostes del Pasapalabra")
                if page.locator(".pasapalabra-key").is_visible() or page.locator(".pasapalabra-reveal[open]").count():
                    raise SystemExit("El Pasapalabra mostra respostes abans del clic")
                first_reveal = page.locator(".pasapalabra-reveal").first
                if first_reveal.evaluate("element => element.open"):
                    raise SystemExit("El concepte del Pasapalabra es mostra abans del clic")
                first_reveal.locator("summary").click()
                if not first_reveal.evaluate("element => element.open") or not first_reveal.locator(".pasapalabra-answer").is_visible():
                    raise SystemExit("El concepte del Pasapalabra no es mostra en fer clic")
                if len(reader.pages) < 2 or not any("Solucionari del Pasapalabra" in pdf_page.extract_text() for pdf_page in reader.pages):
                    raise SystemExit("El PDF del Pasapalabra no inclou un solucionari separat")
            page.close()
        browser.close()
    print(f"Verificades {len(pages)} pàgines de tema, {len(course_topics)} índexs de curs, PDF i targetes interactives.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "prepare", "pdf", "verify", "serve"):
        action = commands.add_parser(name)
        if name in ("prepare", "serve"):
            action.add_argument("--preview-audio", metavar="TEMA=RUTA_MP3",
                                help="Utilitza un MP3 local només per a previsualitzar un tema")
    podcast_command = commands.add_parser("podcast", help="Inspecció i validació de guions")
    podcast_actions = podcast_command.add_subparsers(dest="action", required=True)
    for name in ("validate", "text", "inspect", "policy"):
        action = podcast_actions.add_parser(name)
        action.add_argument("topic", help="assignatura/curs/tema")
        if name == "policy":
            action.add_argument("--all-materials", action="store_true")
            action.add_argument("--explicit", action="store_true")
    tts_command = commands.add_parser("tts", help="Síntesi local de pòdcasts amb Piper i FFmpeg")
    tts_actions = tts_command.add_subparsers(dest="action", required=True)
    for name in ("download", "sample", "build", "play"):
        action = tts_actions.add_parser(name)
        action.add_argument("topic", help="assignatura/curs/tema")
        if name == "play":
            action.add_argument("--sample", action="store_true", help="Reproduïx la mostra en lloc del pòdcast")
    for name in ("check", "inspect", "verify", "publish", "sync"):
        action = tts_actions.add_parser(name)
        if name != "check":
            action.add_argument("topic", nargs="?" if name == "sync" else None,
                                help="assignatura/curs/tema")
        action.add_argument("--backend", choices=("local", "r2"), default="r2")
        if name == "sync":
            action.add_argument("--all", action="store_true")
            action.add_argument("--manifest", type=Path, default=ROOT / "audio/published.json")
            action.add_argument("--download-models", action="store_true")
        if name == "publish":
            action.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()
    preview_audio = None
    if args.command in ("prepare", "serve") and args.preview_audio:
        try:
            reference, filename = args.preview_audio.split("=", 1)
            preview_audio = (reference, Path(filename).resolve())
            local_preview(preview_audio[1], reference)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
    if args.command == "serve":
        from functools import partial
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

        if not (SITE / "index.html").exists():
            parser.error("Genera primer la web amb prepare i quarto render")
        class PreviewHandler(SimpleHTTPRequestHandler):
            def do_HEAD(self):
                if not self.local_audio():
                    super().do_HEAD()

            def do_GET(self):
                if not self.local_audio():
                    super().do_GET()

            def local_audio(self):
                if not preview_audio or self.path not in (
                    f"/__local_audio/{preview_audio[0]}.mp3",
                    f"/__local_audio/{preview_audio[0]}.mp3?download=1",
                ):
                    return False
                path = preview_audio[1]
                size = path.stat().st_size
                match = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
                start = int(match.group(1)) if match else 0
                end = min(int(match.group(2)), size - 1) if match and match.group(2) else size - 1
                if start >= size or end < start:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return True
                self.send_response(206 if match else 200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Accept-Ranges", "bytes")
                if self.path.endswith("?download=1"):
                    self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
                self.send_header("Content-Length", str(end - start + 1))
                if match:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                if self.command == "GET":
                    try:
                        with path.open("rb") as stream:
                            stream.seek(start)
                            remaining = end - start + 1
                            while remaining:
                                chunk = stream.read(min(65536, remaining))
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                                remaining -= len(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        pass  # El navegador pot cancel·lar un rang en buscar una altra posició.
                return True
        print("Previsualització: http://localhost:8000", flush=True)
        ThreadingHTTPServer(("localhost", 8000), partial(PreviewHandler, directory=str(SITE))).serve_forever()
    elif args.command == "prepare":
        prepare(preview_audio)
    elif args.command == "tts":
        try:
            if args.action in {"check", "inspect", "verify", "publish", "sync"}:
                store = (LocalStorage(ROOT / "audio/store") if args.backend == "local" else R2Storage.from_env())
                if args.action == "check":
                    if args.backend == "r2":
                        store.check_bucket()
                    print("Emmagatzematge accessible")
                elif args.action == "sync":
                    if args.all == bool(args.topic):
                        parser.error("Indica un tema o --all")
                    references = script_references(ROOT) if args.all else [args.topic]
                    rows = [sync_one(ROOT, reference, store, download=args.download_models) for reference in references
                            if resolve_config(read_yaml(ROOT / "config/subjects.yml"), reference.split("/")[0],
                                              read_yaml(topic_folder(CONTENT, reference) / "topic.yml"))["generation"] != "disabled"]
                    if args.backend == "r2":
                        for row in rows:
                            verify_public_audio(row)
                    write_podcast_manifest(rows, args.manifest)
                    print(json.dumps(rows, ensure_ascii=False, indent=2))
                else:
                    state = resolve_podcast(ROOT, args.topic)
                    if args.action == "publish":
                        row = sync_one(ROOT, args.topic, store, local_mp3=args.file)
                        if args.backend == "r2":
                            verify_public_audio(row)
                        print(json.dumps(row, ensure_ascii=False))
                    else:
                        record = check_record(store, args.topic, state["hash"])
                        if record is None:
                            parser.error("No existeix esta versió")
                        print(json.dumps(record if args.action == "inspect" else
                                         {"topic": args.topic, "hash": state["hash"],
                                          "url": store.url(object_keys(args.topic, state["hash"])[0]), "valid": True},
                                         ensure_ascii=False, indent=2))
            else:
                _, settings, _, events, model = tts_context(ROOT, args.topic)
                if args.action == "download":
                    print(f"Model descarregat: {download_voice(ROOT, model)}")
                elif args.action == "play":
                    path = audio_path(ROOT, args.topic, sample=args.sample)
                    print(f"Reproduint {path}", flush=True)
                    play_audio(path)
                else:
                    path = audio_path(ROOT, args.topic, sample=args.action == "sample")
                    synthesizer = PiperSynthesizer(model_path(ROOT, model), settings)
                    result = build_audio(events, path, settings, synthesizer, sample=args.action == "sample")
                    if args.action == "build":
                        write_local_provenance(ROOT, args.topic, path)
                    print(f"MP3: {result['path']}\nVeu: {model}\nDuració aproximada: {result['duration_estimate']:.1f} s"
                          f"\nPauses del guió: {result['explicit_pause_count']} ({result['explicit_pause_seconds']:.1f} s)"
                          f"\nPauses totals: {result['pause_count']} ({result['pause_seconds']:.1f} s)"
                          f"\nTemps de generació: {result['elapsed_seconds']:.1f} s")
        except (OSError, KeyError, TypeError, ValueError, StorageError, yaml.YAMLError) as exc:
            parser.error(str(exc))
    elif args.command == "podcast":
        try:
            folder = topic_folder(CONTENT, args.topic)
            topic = read_yaml(folder / "topic.yml")
            policy = resolve_config(read_yaml(ROOT / "config/subjects.yml"), args.topic.split("/")[0], topic)
            if args.action == "policy":
                exists = (folder / "podcast" / "script.md").exists()
                print(json.dumps({**policy, "generate": should_generate(policy, all_materials=args.all_materials,
                                                                          explicit=args.explicit, script_exists=exists),
                                  "script_exists": exists}, ensure_ascii=False, indent=2))
            else:
                parsed = parse_script(folder / "podcast" / "script.md", topic, policy)
                if args.action == "validate":
                    print(f"Guió vàlid: {args.topic}")
                elif args.action == "text":
                    print(spoken_text(parsed))
                else:
                    print(json.dumps(parsed, ensure_ascii=False, indent=2))
        except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
            parser.error(str(exc))
    else:
        {"validate": validate, "pdf": pdf, "verify": verify}[args.command]()
