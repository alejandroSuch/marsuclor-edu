"""Validate published audio references and render podcast-only web controls."""

from __future__ import annotations

import html
import math
import re
from pathlib import Path
from urllib.parse import urlsplit


HASH = re.compile(r"[0-9a-f]{64}")
REFERENCE = re.compile(r"[a-z0-9-]+/[1-4]-eso/[a-z0-9-]+")


def published_audio(manifest: dict, reference: str, chapter_titles: list[str]) -> dict | None:
    """Return a validated immutable Worker URL or no published audio."""
    if not isinstance(manifest, dict):
        raise ValueError("El manifest de pòdcasts ha de ser un mapa")
    for key, row in manifest.items():
        if not isinstance(key, str) or not REFERENCE.fullmatch(key) or not isinstance(row, dict):
            raise ValueError("Entrada invàlida en el manifest de pòdcasts")
        digest, url = row.get("hash"), row.get("url")
        if not isinstance(digest, str) or not HASH.fullmatch(digest) or not isinstance(url, str):
            raise ValueError(f"Referència publicada invàlida: {key}")
        parsed = urlsplit(url)
        expected = f"/podcasts/{key}/{digest}.mp3"
        if (parsed.scheme != "https" or not re.fullmatch(r"[a-z0-9-]+\.[a-z0-9-]+\.workers\.dev", parsed.netloc)
                or parsed.path != expected or parsed.query or parsed.fragment or parsed.username or parsed.password):
            raise ValueError(f"URL de pòdcast publicada invàlida: {key}")
    row = manifest.get(reference)
    if row is None:
        return None
    url = row["url"]
    chapters = row.get("chapters")
    if chapters is not None:
        if (not isinstance(chapters, list) or len(chapters) != len(chapter_titles)
                or any(not isinstance(chapter, dict) or chapter.get("title") != title
                       or type(chapter.get("start_seconds")) not in (int, float)
                       or not math.isfinite(chapter["start_seconds"])
                       or chapter["start_seconds"] < 0
                       for chapter, title in zip(chapters, chapter_titles))
                or any(a["start_seconds"] >= b["start_seconds"] for a, b in zip(chapters, chapters[1:]))
                or (chapters and chapters[0]["start_seconds"] != 0)):
            raise ValueError(f"Marques de capítol invàlides: {reference}")
    return {"url": url, "chapters": chapters}


def player_markup(audio: dict, language: str) -> str:
    labels = {
        "es": ("Escuchar podcast", "Retroceder 15 segundos", "Avanzar 15 segundos", "Velocidad",
               "Descargar MP3", "Leer transcripción", "Capítulos"),
        "ca-valencia": ("Escolta el pòdcast", "Retrocedix 15 segons", "Avança 15 segons", "Velocitat",
                        "Descarrega l'MP3", "Llig la transcripció", "Capítols"),
    }
    listen, back, ahead, speed, download, transcript, chapters_label = labels[language]
    url = html.escape(audio["url"], quote=True)
    markup = (
        '<section class="podcast-player" aria-label="' + html.escape(listen, quote=True) + '">\n'
        f'<audio controls preload="metadata" src="{url}" aria-label="{html.escape(listen, quote=True)}"></audio>\n'
        '<div class="podcast-actions">\n'
        f'<button type="button" data-skip="-15" aria-label="{html.escape(back, quote=True)}">↶ 15 s</button>\n'
        f'<button type="button" data-skip="15" aria-label="{html.escape(ahead, quote=True)}">15 s ↷</button>\n'
        f'<label>{html.escape(speed)} <select class="podcast-speed" aria-label="{html.escape(speed, quote=True)}">'
        '<option value="0.75">0,75×</option><option value="1" selected>1×</option>'
        '<option value="1.25">1,25×</option><option value="1.5">1,5×</option></select></label>\n'
        f'<a href="{url}?download=1">{html.escape(download)}</a>\n'
        f'<a href="#transcription">{html.escape(transcript)}</a>\n'
        '</div>\n'
    )
    if audio["chapters"]:
        markup += f'<nav class="podcast-chapters" aria-label="{html.escape(chapters_label, quote=True)}"><ol>\n'
        for chapter in audio["chapters"]:
            seconds = chapter["start_seconds"]
            stamp = f"{int(seconds // 60)}:{int(seconds % 60):02d}"
            markup += (f'<li><button type="button" data-seek="{seconds:g}">'
                       f'{html.escape(chapter["title"])} <span>{stamp}</span></button></li>\n')
        markup += '</ol></nav>\n'
    return markup + '</section>\n\n'


def local_preview(path: Path, reference: str) -> dict:
    if not REFERENCE.fullmatch(reference) or not path.is_file() or path.suffix.lower() != ".mp3":
        raise ValueError("MP3 local de previsualització invàlid")
    return {"url": f"http://localhost:8000/__local_audio/{reference}.mp3", "chapters": None}
