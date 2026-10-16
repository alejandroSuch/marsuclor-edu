#!/usr/bin/env python3
"""Validate content, prepare Quarto pages, and print rendered HTML to PDF."""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
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


def validate():
    subjects = read_yaml(ROOT / "config/subjects.yml")["subjects"]
    errors = []
    if not topics():
        errors.append("No hi ha cap tema")
    for path in topics():
        label = str(path.relative_to(ROOT))
        try:
            topic, notes, exercises, cards = data_for(path)
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
        except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
            errors.append(f"{label}: {exc}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Validats {len(topics())} temes: objectius, llengües i fonts coberts.")


def esc(value):
    return html.escape(str(value), quote=True)


def frontmatter(title: str) -> str:
    return f"---\ntitle: {json.dumps(title, ensure_ascii=False)}\n---\n\n"


def source_list(topic):
    return "\n".join(
        f"- [{esc(s['title'])}]({s['url']})" if s.get("url") else f"- {esc(s['title'])}"
        for s in topic["sources"]
    )


def page_header(topic, course, section):
    return (
        frontmatter(f"{section} · {topic['title']}")
        + f"**{esc(course_label(course, topic['teaching_language']))} · {esc(topic['title'])}**\n\n"
        + f"::: {{.callout-note}}\n{esc(topic['intro'])}\n:::\n\n"
        + '<button class="print-button" onclick="window.print()">Imprimix / Desa en PDF</button>\n\n'
    )


def pdf_link(relative: Path, filename: str) -> str:
    target = "../../../../pdf/" + relative.as_posix() + "/" + filename + ".pdf"
    return f'<p class="pdf-link"><a href="{esc(target)}">Descarrega el PDF A4</a></p>\n\n'


def prepare():
    validate()
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
        subject, course, slug = path.parts[-4:-1]
        rel = Path("topics") / subject / course / slug
        dest = GENERATED / rel
        dest.mkdir(parents=True)
        nav = '<div class="material-nav">\n\n' + " | ".join(f"[{name}]({file}.qmd)" for name, file in [
            ("Apunts", "notes"), ("Exercicis", "exercises"),
            ("Solucionari", "solutions"), ("Targetes", "cards")]) + '\n\n</div>'
        index += f"## {esc(topic['title'])}\n\n{esc(course_label(course, topic['teaching_language']))} · {esc(subject)}\n\n"
        index += " | ".join(f"[{name}]({rel.as_posix()}/{file}.qmd)" for name, file in [
            ("Apunts", "notes"), ("Exercicis", "exercises"),
            ("Solucionari", "solutions"), ("Targetes", "cards")]) + "\n\n"
        index += f"**Estat:** {esc(topic.get('status', 'revisat'))}. {esc(topic['intro'])}\n\n"
        base = page_header(topic, course, "Apunts") + pdf_link(rel, "notes") + nav + "\n\n## Objectius\n\n"
        base += "\n".join(f"- {esc(o['text'])}" for o in topic["objectives"]) + "\n\n"
        for row in notes["sections"]:
            base += f"## {esc(row['title'])}\n\n{row['body']}\n\n"
            base += "*Fonts d'este apartat: " + ", ".join(
                esc(next(s["title"] for s in topic["sources"] if s["id"] == source_id))
                for source_id in row["source_ids"]
            ) + ".*\n\n"
        base += "## Fonts i procedència\n\n" + source_list(topic) + "\n\n" + esc(topic.get("source_note", "")) + "\n"
        (dest / "notes.qmd").write_text(base, encoding="utf-8")
        exercise_page = page_header(topic, course, "Exercicis") + pdf_link(rel, "exercises") + nav + "\n\n"
        solution_page = page_header(topic, course, "Solucionari") + pdf_link(rel, "solutions") + nav + "\n\n"
        for n, row in enumerate(exercises["exercises"], 1):
            exercise_page += f"## {n}. {esc(row['question'])}\n\n<div class='answer-space'></div>\n\n"
            solution_page += f"## {n}. {esc(row['question'])}\n\n{esc(row['answer'])}\n\n"
        (dest / "exercises.qmd").write_text(exercise_page, encoding="utf-8")
        (dest / "solutions.qmd").write_text(solution_page, encoding="utf-8")
        card_page = page_header(topic, course, "Targetes") + pdf_link(rel, "cards") + nav + "\n\n"
        card_page += "Prem una targeta per veure la resposta. En imprimir, cada targeta mostra pregunta i resposta per a retallar.\n\n"
        card_page += '<div class="card-grid">\n'
        for row in cards["cards"]:
            card_page += (
                '<button type="button" class="study-card" aria-pressed="false" '
                f'aria-label="{esc(row["front"])}. Prem per veure la resposta">'
                f'<span class="card-front">{esc(row["front"])}</span>'
                f'<span class="card-back">{esc(row["back"])}</span></button>\n'
            )
        card_page += "</div>\n"
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

    pages = sorted((SITE / "topics").rglob("*.html"))
    if not pages:
        raise SystemExit("Falten pàgines HTML")
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
            if page_path.name == "cards.html":
                card = page.locator(".study-card").first
                if card.count() == 0:
                    raise SystemExit("No hi ha targetes")
                card.click()
                if card.get_attribute("aria-pressed") != "true":
                    raise SystemExit("La targeta no mostra la resposta")
            page.close()
        browser.close()
    print(f"Verificades {len(pages)} pàgines, PDF i targetes interactives.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["validate", "prepare", "pdf", "verify"])
    command = parser.parse_args().command
    {"validate": validate, "prepare": prepare, "pdf": pdf, "verify": verify}[command]()
