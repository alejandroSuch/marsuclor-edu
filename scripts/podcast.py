"""Declarative podcast policy and deterministic narration-script processing."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


MODES = {"default", "on_demand", "disabled"}
FORMATS = {"narration"}
PAUSE = re.compile(r"\[pause: ([0-9]+(?:\.[0-9]+)?)\]")
CHAPTER = re.compile(r"## (.+)")
TOPIC_REF = re.compile(r"[a-z0-9-]+/[1-4]-eso/[a-z0-9-]+")


def resolve_config(config: dict, subject: str, topic: dict) -> dict:
    """Apply global, subject and topic overrides in that order."""
    result = {}
    for layer in (config, config["subjects"][subject], topic):
        override = layer.get("materials", {}).get("podcast", {})
        if not isinstance(override, dict):
            raise ValueError("La configuració del podcast ha de ser un mapa")
        result.update(override)
    if result.get("generation") not in MODES:
        raise ValueError("Mode de generació del podcast invàlid")
    if result.get("format") not in FORMATS:
        raise ValueError("Format del podcast invàlid")
    duration = result.get("duration_minutes")
    if type(duration) not in (int, float) or not 1 <= duration <= 60:
        raise ValueError("Duració orientativa del podcast invàlida")
    return result


def should_generate(policy: dict, *, all_materials: bool = False, explicit: bool = False,
                    script_exists: bool = False) -> bool:
    """Decide whether to write; a general materials request preserves existing scripts."""
    mode = policy["generation"]
    if mode == "disabled":
        return False
    if script_exists and not explicit:
        return False
    return explicit or (all_materials and mode == "default")


def topic_folder(content: Path, reference: str) -> Path:
    if not TOPIC_REF.fullmatch(reference):
        raise ValueError("Tema esperat: assignatura/curs/tema")
    folder = content.joinpath(*reference.split("/"))
    if not (folder / "topic.yml").is_file():
        raise ValueError(f"El tema no existeix: {reference}")
    return folder


def parse_script(path: Path, topic: dict, policy: dict) -> dict:
    """Parse a deliberately small Markdown dialect into ordered TTS-ready events."""
    raw = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n", raw, re.S)
    if not match:
        raise ValueError("Falten metadades YAML inicials")
    metadata = yaml.safe_load(match.group(1))
    if not isinstance(metadata, dict):
        raise ValueError("Metadades YAML invàlides")
    required = {"title", "language", "format", "estimated_duration_minutes", "objective_ids", "source_ids"}
    if not required <= metadata.keys() or not isinstance(metadata["title"], str) or not metadata["title"].strip():
        raise ValueError("Metadades del podcast incompletes")
    if metadata["language"] != topic["teaching_language"]:
        raise ValueError("Llengua del podcast diferent de la del tema")
    if metadata["format"] != policy["format"] or metadata["format"] not in FORMATS:
        raise ValueError("Format del podcast incorrecte")
    duration = metadata["estimated_duration_minutes"]
    if type(duration) not in (int, float) or not 1 <= duration <= 60:
        raise ValueError("Duració orientativa invàlida")
    for field, valid in (("objective_ids", {row["id"] for row in topic["objectives"]}),
                         ("source_ids", {row["id"] for row in topic["sources"]})):
        ids = metadata[field]
        if not isinstance(ids, list) or not ids or not all(isinstance(item, str) for item in ids) or not set(ids) <= valid:
            raise ValueError(f"{field} invàlids")
    events = []
    paragraph = []
    chapters = set()

    def flush():
        if paragraph:
            events.append({"type": "speech", "text": " ".join(paragraph)})
            paragraph.clear()

    for number, line in enumerate(raw[match.end():].splitlines(), raw[:match.end()].count("\n") + 1):
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        chapter = CHAPTER.fullmatch(stripped)
        pause = PAUSE.fullmatch(stripped)
        if chapter:
            flush()
            title = chapter.group(1).strip()
            if not title or title in chapters or any(char in title for char in "[]<>#*"):
                raise ValueError(f"Capítol invàlid a la línia {number}")
            chapters.add(title)
            events.append({"type": "chapter", "title": title})
        elif pause:
            flush()
            if not chapters:
                raise ValueError(f"Pausa fora de capítol a la línia {number}")
            seconds = float(pause.group(1))
            if not 0 < seconds <= 30:
                raise ValueError(f"Pausa invàlida a la línia {number}")
            events.append({"type": "pause", "duration_seconds": seconds})
        else:
            if not chapters or re.match(r"(?:#|[-*+]\s|\d+[.)]\s|>|```|~~~|<)", stripped) or re.search(r"\[[^]]+\]|!\[|\]\(|\{\{|[`*]", stripped):
                raise ValueError(f"Text no narratiu o fora de capítol a la línia {number}")
            paragraph.append(stripped)
    flush()
    chapter_indexes = [index for index, event in enumerate(events) if event["type"] == "chapter"]
    if not chapter_indexes or any(
        not any(event["type"] == "speech" for event in events[start + 1:end])
        for start, end in zip(chapter_indexes, chapter_indexes[1:] + [len(events)])
    ):
        raise ValueError("Cada capítol necessita text narrat")
    return {"metadata": metadata, "events": events}


def spoken_text(parsed: dict) -> str:
    return "\n\n".join(event["text"] for event in parsed["events"] if event["type"] == "speech")
