#!/usr/bin/env python3
"""Validate content, prepare Quarto pages, and print rendered HTML to PDF."""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

import yaml

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


def validate():
    subjects = read_yaml(ROOT / "config/subjects.yml")["subjects"]
    errors = []
    if not topics():
        errors.append("No hi ha cap tema")
    for path in topics():
        label = str(path.relative_to(ROOT))
        try:
            topic, notes, exercises, cards = data_for(path)
            vocabulary, diagrams = optional_data(path.parent)
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


def frontmatter(title: str, language: str | None = None) -> str:
    language_line = f"lang: {language}\n" if language else ""
    return f"---\ntitle: {json.dumps(title, ensure_ascii=False)}\n{language_line}---\n\n"


UI = {
    "ca-valencia": {
        "notes": "Apunts", "exercises": "Exercicis", "solutions": "Solucionari",
        "cards": "Targetes", "objectives": "Objectius",
        "study_tips": "Com estudiar-ho", "self_check": "Comprovació final",
        "exercise": "Exercici", "prompt": "Enunciat",
        "answer": "Solució", "show_answer": "Mostra la solució", "print": "Imprimix / Desa en PDF",
        "download": "Descarrega el PDF A4", "status": "Estat",
        "card_instruction": "Prem una targeta per veure la resposta. Per al PDF A4, imprimix a doble cara, amb gir pel costat llarg i escala del 100 %; els reversos estan reflectits horitzontalment per a retallar.",
        "card_action": "Prem per veure la resposta", "blank_diagram": "Esquema sense etiquetes",
        "vocabulary": "Vocabulari de l'examen", "support_terms": "Altres termes de suport",
        "review_tips": "Trucs de repàs",
    },
    "es": {
        "notes": "Apuntes", "exercises": "Ejercicios", "solutions": "Solucionario",
        "cards": "Tarjetas", "objectives": "Objetivos",
        "study_tips": "Cómo estudiarlo", "self_check": "Comprobación final",
        "exercise": "Ejercicio", "prompt": "Enunciado",
        "answer": "Solución", "show_answer": "Ver solución", "print": "Imprimir / Guardar en PDF",
        "download": "Descargar el PDF A4", "status": "Estado",
        "card_instruction": "Pulsa una tarjeta para ver la respuesta. Para el PDF A4, imprime a doble cara, girando por el borde largo y al 100 % de escala; los reversos están reflejados horizontalmente para recortar.",
        "card_action": "Pulsa para ver la respuesta", "blank_diagram": "Esquema sin etiquetas",
        "vocabulary": "Vocabulario del examen", "support_terms": "Otros términos de apoyo",
        "review_tips": "Trucos de repaso",
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


def prepare():
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
        "project:\n  type: website\n  output-dir: ../site\n  render:\n    - '*.qmd'\n    - 'topics/**/*.qmd'\nwebsite:\n  title: Martina Edu\n"
        "  navbar:\n    left:\n      - href: index.qmd\n        text: Inici\n"
        "format:\n  html:\n    theme: cosmo\n    css: styles/site.css\n    toc: true\n    lang: ca\n"
        "    include-after-body: styles/cards.js\nexecute:\n  freeze: auto\n",
        encoding="utf-8",
    )
    index = frontmatter("Martina Edu") + "Materials per a estudiar en línia i imprimir en A4.\n\n"
    for path in topics():
        topic, notes, exercises, cards = data_for(path)
        vocabulary, diagrams = optional_data(path.parent)
        diagrams_by_id = {entry["id"]: entry for entry in diagrams["diagrams"]} if diagrams else {}
        subject, course, slug = path.parts[-4:-1]
        language = topic["teaching_language"]
        rel = Path("topics") / subject / course / slug
        dest = GENERATED / rel
        dest.mkdir(parents=True)
        copy_diagrams(path.parent, dest, diagrams)
        materials = [(ui(language, key), key) for key in ("notes", "exercises", "cards")]
        nav = '<div class="material-nav">\n\n' + " | ".join(
            f"[{name}]({file}.qmd)" for name, file in materials
        ) + '\n\n</div>'
        index += f"## {esc(topic['title'])}\n\n{esc(course_label(course, topic['teaching_language']))} · {esc(subject)}\n\n"
        index += " | ".join(f"[{name}]({rel.as_posix()}/{file}.qmd)" for name, file in materials) + "\n\n"
        index += f"**{ui(language, 'status')}:** {esc(topic.get('status', 'revisat'))}. {esc(topic['intro'])}\n\n"
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

    pages = sorted((SITE / "topics").rglob("*.html"))
    if not pages:
        raise SystemExit("Falten pàgines HTML")
    if len(pages) != 3 * len(topics()) or any(page.name not in {"notes.html", "exercises.html", "cards.html"} for page in pages):
        raise SystemExit("El nombre de pàgines web per tema no és correcte")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for page_path in pages:
            page = browser.new_page()
            page.goto(page_path.resolve().as_uri(), wait_until="load")
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
            relative = page_path.relative_to(SITE).parts
            folder = CONTENT.joinpath(*relative[1:-1])
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
            page.close()
        browser.close()
    print(f"Verificades {len(pages)} pàgines, PDF i targetes interactives.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["validate", "prepare", "pdf", "verify"])
    command = parser.parse_args().command
    {"validate": validate, "prepare": prepare, "pdf": pdf, "verify": verify}[command]()
