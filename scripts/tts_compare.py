"""Reproducible, local listening comparison and technical MP3 checks."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import yaml

from podcast import parse_script, resolve_config
from tts import (PiperSynthesizer, apply_pronunciations, build_audio,
                 load_pronunciations, model_path, run_command)


PROFILES = (
    ("01_ona_base", "ca_ES-upc_ona-medium", {}),
    ("02_ona_lento_108", "ca_ES-upc_ona-medium", {"length_scale": 1.08}),
    ("03_ona_rapido_094", "ca_ES-upc_ona-medium", {"length_scale": 0.94}),
    ("04_ona_pausas_largas", "ca_ES-upc_ona-medium",
     {"sentence_pause_seconds": 0.75, "paragraph_pause_seconds": 1.0}),
    ("05_ona_variacion_baja", "ca_ES-upc_ona-medium",
     {"noise_scale": 0.5, "noise_w_scale": 0.6}),
    ("06_ona_volumen_menos18", "ca_ES-upc_ona-medium", {"loudness_lufs": -18}),
    ("07_pau_xlow_base", "ca_ES-upc_pau-x_low", {}),
)


def comparison_excerpt(folder: Path, topic: dict, policy: dict, chapter: str,
                       paragraph_index: int = 0) -> str:
    """Return one complete existing paragraph, with TTS-only replacements."""
    if paragraph_index < 0:
        raise ValueError("L'índex de paràgraf ha de ser positiu")
    parsed = parse_script(folder / "podcast" / "script.md", topic, policy)
    current_chapter = None
    paragraphs = []
    for event in parsed["events"]:
        if event["type"] == "chapter":
            current_chapter = event["title"]
        elif current_chapter == chapter and event["type"] == "speech":
            paragraphs.append(event["text"])
    if not paragraphs:
        raise ValueError(f"No existeix el capítol o no conté text: {chapter}")
    if paragraph_index >= len(paragraphs):
        raise ValueError(f"El capítol només té {len(paragraphs)} paràgrafs")
    return apply_pronunciations(paragraphs[paragraph_index], load_pronunciations(folder))


def profile_plan(settings: dict) -> list[dict]:
    return [{"name": name, "model": model, "settings": {**settings, **overrides},
             "overrides": overrides} for name, model, overrides in PROFILES]


def _number(pattern: str, text: str, label: str) -> float:
    match = re.search(pattern, text, re.S)
    if not match:
        raise ValueError(f"No s'ha pogut mesurar {label} amb FFmpeg")
    return float(match.group(1))


def inspect_mp3(path: Path, settings: dict, boundary_jump_dbfs: float) -> dict:
    probe = json.loads(run_command([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size:stream=codec_name,channels,sample_rate,bit_rate",
        "-of", "json", str(path),
    ]).stdout)
    stream = next((item for item in probe.get("streams", []) if item.get("codec_name") == "mp3"), None)
    if stream is None or stream.get("channels") != 1 or int(stream["sample_rate"]) != settings["output_sample_rate"]:
        raise ValueError(f"Format MP3 inesperat: {path}")
    bitrate = int(stream.get("bit_rate", 0))
    if abs(bitrate - settings["mp3_bitrate_kbps"] * 1000) > 1000:
        raise ValueError(f"Taxa de bits MP3 inesperada: {path}")
    duration = float(probe["format"]["duration"])
    if not 5 <= duration <= 120 or int(probe["format"]["size"]) < 1000:
        raise ValueError(f"Duració o mida de mostra invàlida: {path}")
    run_command(["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(path), "-f", "null", "-"])
    loudness_log = run_command([
        "ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-i", str(path),
        "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-",
    ]).stderr
    summary = loudness_log.split("Summary:")[-1]
    lufs = _number(r"Integrated loudness:\s+I:\s+(-?\d+(?:\.\d+)?)", summary, "LUFS")
    true_peak = _number(r"True peak:\s+Peak:\s+(-?\d+(?:\.\d+)?)", summary, "pic real")
    silence_log = run_command([
        "ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-i", str(path),
        "-af", "silencedetect=n=-55dB:d=2", "-f", "null", "-",
    ]).stderr
    long_silences = [float(value) for value in re.findall(r"silence_duration:\s*([0-9.]+)", silence_log)]
    warnings = []
    if abs(lufs - settings["loudness_lufs"]) > 2:
        warnings.append("El volum mesurat s'allunya més de 2 LU de l'objectiu")
    if true_peak > -0.5:
        warnings.append("El pic real és massa prop de 0 dBFS")
    if long_silences:
        warnings.append("S'han detectat silencis superiors a 2 segons en el fragment")
    if math.isfinite(boundary_jump_dbfs) and boundary_jump_dbfs > -12:
        warnings.append("Unió PCM amb un salt que podria produir un clic")
    return {
        "duration_seconds": round(duration, 3), "codec": stream["codec_name"],
        "channels": stream["channels"], "sample_rate_hz": int(stream["sample_rate"]),
        "bitrate_bps": bitrate,
        "integrated_lufs": lufs, "true_peak_dbfs": true_peak,
        "long_silences_seconds": long_silences,
        "max_boundary_jump_dbfs": round(boundary_jump_dbfs, 1) if math.isfinite(boundary_jump_dbfs) else None,
        "decode_ok": True, "warnings": warnings,
    }


def generate_comparison(root: Path, reference: str, chapter: str,
                        paragraph_index: int = 0) -> Path:
    folder = root / "content" / reference
    topic = yaml.safe_load((folder / "topic.yml").read_text(encoding="utf-8"))
    config = yaml.safe_load((root / "config" / "subjects.yml").read_text(encoding="utf-8"))
    policy = resolve_config(config, reference.split("/")[0], topic)
    if policy["generation"] == "disabled":
        raise ValueError("La política del pòdcast està desactivada")
    from tts import resolve_tts_config
    settings = resolve_tts_config(config, reference.split("/")[0], topic)
    if topic["teaching_language"] != "ca-valencia":
        raise ValueError("Els perfils de comparació actuals són per a valencià")
    excerpt = comparison_excerpt(folder, topic, policy, chapter, paragraph_index)
    output_dir = root / "audio" / reference / "comparacion"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "texto.txt").write_text(excerpt + "\n", encoding="utf-8")
    script = folder / "podcast" / "script.md"
    report = {
        "topic": reference, "chapter": chapter, "paragraph_index": paragraph_index,
        "text_file": "texto.txt", "text_sha256": hashlib.sha256(excerpt.encode()).hexdigest(),
        "script_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
        "note": "Totes les mostres pronuncien exactament el mateix text. Les mètriques no valoren la naturalitat.",
        "profiles": [],
    }
    events = [{"type": "speech", "text": excerpt}]
    for profile in profile_plan(settings):
        output = output_dir / f"{profile['name']}.mp3"
        model = model_path(root, profile["model"])
        synthesizer = PiperSynthesizer(model, profile["settings"])
        built = build_audio(events, output, profile["settings"], synthesizer)
        metrics = inspect_mp3(output, profile["settings"], built["max_boundary_jump_dbfs"])
        report["profiles"].append({
            "name": profile["name"], "file": output.name, "model": profile["model"],
            "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
            "settings": profile["settings"], "overrides": profile["overrides"],
            "metrics": metrics,
        })
        print(f"{output.name}: {metrics['duration_seconds']:.1f} s, "
              f"{metrics['integrated_lufs']:.1f} LUFS, pic {metrics['true_peak_dbfs']:.1f} dBFS")
    manifest = output_dir / "manifest.json"
    manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return manifest
