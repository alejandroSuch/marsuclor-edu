"""Resolve, reuse and publish immutable podcast versions."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from podcast import parse_script, resolve_config, topic_folder
from storage import canonical_bytes, check_record, object_keys, put_record, sha256_file
from tts import (PiperSynthesizer, build_audio, download_voice, load_pronunciations, model_path,
                 narration_events, resolve_tts_config, resolve_voice)
import yaml


PIPELINE_VERSION = 1  # Increment only when audio-affecting pipeline behavior changes.
SYNTHESIS_KEYS = ("length_scale", "noise_scale", "noise_w_scale", "volume")
ENCODING_KEYS = ("output_sample_rate", "mp3_bitrate_kbps", "loudness_lufs", "true_peak_db",
                 "sentence_pause_seconds", "paragraph_pause_seconds")


def versions() -> dict:
    try:
        piper = importlib.metadata.version("piper-tts")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ValueError("Cal Piper per a calcular l'empremta") from exc
    return {"piper": piper}


def fingerprint(reference: str, parsed: dict, settings: dict, model: str,
                model_checksum: str, model_config_checksum: str, tool_versions: dict,
                replacements: dict[str, str]) -> tuple[str, dict]:
    # Chapter headings are not narrated and do not change the encoded audio.
    ordered = []
    for event in parsed["events"]:
        if event["type"] == "speech":
            from tts import apply_pronunciations
            ordered.append({"type": "speech", "text": apply_pronunciations(event["text"], replacements)})
        elif event["type"] == "pause":
            ordered.append({"type": "pause", "duration_seconds": event["duration_seconds"]})
    payload = {"schema": 1, "pipeline": PIPELINE_VERSION, "events": ordered,
               "voice": {"model": model, "sha256": model_checksum, "config_sha256": model_config_checksum},
               "tools": tool_versions,
               "synthesis": {key: settings[key] for key in SYNTHESIS_KEYS},
               "encoding": {key: settings[key] for key in ENCODING_KEYS}}
    return hashlib.sha256(canonical_bytes(payload)).hexdigest(), payload


def resolve(root: Path, reference: str, *, download: bool = False) -> dict:
    folder = topic_folder(root / "content", reference)
    topic = yaml.safe_load((folder / "topic.yml").read_text(encoding="utf-8"))
    config = yaml.safe_load((root / "config/subjects.yml").read_text(encoding="utf-8"))
    policy = resolve_config(config, reference.split("/")[0], topic)
    if policy["generation"] == "disabled":
        raise ValueError(f"Pòdcast desactivat: {reference}")
    script = folder / "podcast" / "script.md"
    parsed = parse_script(script, topic, policy)
    settings = resolve_tts_config(config, reference.split("/")[0], topic)
    model = resolve_voice(settings, topic["teaching_language"])
    model_file = model_path(root, model)
    voice = settings["voices"][topic["teaching_language"]]
    configured = (voice.get("sha256"), voice.get("config_sha256"))
    if all(configured):
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in configured):
            raise ValueError(f"Checksum configurat invàlid per a {model}")
        model_digest, config_digest = configured
    elif model_file.is_file() and Path(str(model_file) + ".json").is_file():
        model_digest = sha256_file(model_file)
        config_digest = sha256_file(Path(str(model_file) + ".json"))
    else:
        raise ValueError(f"Falten checksums de {model} o el model local")
    replacements = load_pronunciations(folder)
    digest, payload = fingerprint(reference, parsed, settings, model, model_digest, config_digest,
                                  versions(), replacements)
    return {"reference": reference, "folder": folder, "parsed": parsed, "settings": settings,
            "model": model, "model_file": model_file, "events": narration_events(parsed, replacements),
            "hash": digest, "generation": payload, "download": download}


def validate_mp3(path: Path) -> float:
    if not path.is_file() or path.stat().st_size < 1000:
        raise ValueError("MP3 absent o massa menut")
    try:
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                               check=True, capture_output=True, text=True)
        duration = float(probe.stdout.strip())
        if duration <= 0:
            raise ValueError("Duració MP3 invàlida")
        subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"],
                       check=True, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise ValueError("MP3 no supera la verificació FFmpeg") from exc
    return duration


def write_local_provenance(root: Path, reference: str, mp3: Path) -> Path:
    state = resolve(root, reference)
    sidecar = mp3.with_name(mp3.name + ".provenance.json")
    sidecar.write_text(json.dumps({"generation_hash": state["hash"], "mp3_sha256": sha256_file(mp3),
                                   "mp3_size": mp3.stat().st_size}, sort_keys=True) + "\n", encoding="utf-8")
    return sidecar


def sync_one(root: Path, reference: str, store, *, download: bool = False,
             local_mp3: Path | None = None, synthesize=None) -> dict:
    state = resolve(root, reference, download=download)
    digest = state["hash"]
    found = check_record(store, reference, digest)
    if found is not None:
        return {"topic": reference, "hash": digest, "url": store.url(object_keys(reference, digest)[0]),
                "size": found["mp3_size"], "reused": True}
    # A second read narrows the Actions concurrency window. Any error fails closed.
    found = check_record(store, reference, digest)
    if found is not None:
        return {"topic": reference, "hash": digest, "url": store.url(object_keys(reference, digest)[0]),
                "size": found["mp3_size"], "reused": True}
    output = local_mp3 or root / "audio" / reference / f"{digest}.mp3"
    if local_mp3 is not None:
        provenance = output.with_name(output.name + ".provenance.json")
        try:
            source = json.loads(provenance.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Falta procedència local del MP3: {provenance}") from exc
        if (source.get("generation_hash") != digest or source.get("mp3_size") != output.stat().st_size
                or source.get("mp3_sha256") != sha256_file(output)):
            raise ValueError("El MP3 local no coincidix amb la generació validada")
    if local_mp3 is None:
        model_file = state["model_file"]
        if not model_file.is_file() or not Path(str(model_file) + ".json").is_file():
            if not download:
                raise ValueError(f"Falta la veu {state['model']}; executa tts download")
            download_voice(root, state["model"])
        if (sha256_file(model_file) != state["generation"]["voice"]["sha256"] or
                sha256_file(Path(str(model_file) + ".json")) != state["generation"]["voice"]["config_sha256"]):
            raise ValueError(f"Checksum de la veu descarregada incorrecte: {state['model']}")
        synthesizer = synthesize or PiperSynthesizer(state["model_file"], state["settings"])
        build_audio(state["events"], output, state["settings"], synthesizer)
    duration = validate_mp3(output)
    record = {"topic": reference, "generation_hash": digest, "voice": state["model"],
              "generation": state["generation"],
              "voice_sha256": state["generation"]["voice"]["sha256"],
              "voice_config_sha256": state["generation"]["voice"]["config_sha256"],
              "tools": state["generation"]["tools"], "pipeline_version": PIPELINE_VERSION,
              "duration_seconds": duration, "generated_at": datetime.now(timezone.utc).isoformat(),
              "mp3_size": output.stat().st_size, "mp3_sha256": sha256_file(output),
              "chapters": [{"title": event["title"]} for event in state["parsed"]["events"] if event["type"] == "chapter"]}
    put_record(store, reference, digest, output, record)
    return {"topic": reference, "hash": digest, "url": store.url(object_keys(reference, digest)[0]),
            "size": record["mp3_size"], "reused": False}


def verify_public_audio(row: dict) -> None:
    request = Request(row["url"], method="HEAD")
    try:
        with urlopen(request, timeout=20) as response:
            if (response.status != 200 or response.url != row["url"]
                    or response.headers.get("Content-Type") != "audio/mpeg"
                    or response.headers.get("Accept-Ranges") != "bytes"
                    or int(response.headers.get("Content-Length", "0")) != row["size"]):
                raise ValueError(f"Worker no servix correctament {row['topic']}")
    except (OSError, ValueError) as exc:
        raise ValueError(f"MP3 públic no disponible: {row['topic']}: {exc}") from exc


def script_references(root: Path) -> list[str]:
    return sorted(path.parent.parent.relative_to(root / "content").as_posix()
                  for path in (root / "content").glob("*/*/*/podcast/script.md"))


def manifest(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({row["topic"]: {"hash": row["hash"], "url": row["url"]}
                                for row in rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
