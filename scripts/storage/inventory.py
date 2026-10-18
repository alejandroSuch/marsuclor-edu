"""Read-only podcast inventory. A published manifest is needed to classify old versions."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.request import urlopen

from podcast_web import published_audio
from . import StorageError, canonical_bytes


OBJECT = re.compile(r"podcasts/([a-z0-9-]+/[1-4]-eso/[a-z0-9-]+)/([0-9a-f]{64})\.(mp3|json)")
FREE_BYTES = 10_000_000_000  # Approximate payload threshold; R2 bills GB-month, not this snapshot.


def load_manifest(source: str | None) -> dict | None:
    if source is None:
        return None
    try:
        if source.startswith("https://"):
            with urlopen(source, timeout=20) as response:
                data = response.read(1_000_001)
        else:
            data = Path(source).read_bytes()
        if len(data) > 1_000_000:
            raise ValueError("Manifest massa gran")
        manifest = json.loads(data)
        if not isinstance(manifest, dict):
            raise ValueError("El manifest ha de ser un mapa")
        published_audio(manifest, "", [])  # Validates every topic, hash and Worker URL.
        return manifest
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise StorageError(f"No es pot llegir el manifest publicat: {exc}") from exc


def inventory(store, manifest: dict | None = None) -> dict:
    objects = sorted(store.list_objects(), key=lambda item: item["key"])
    pairs: dict[tuple[str, str], dict] = {}
    unexpected = []
    for item in objects:
        match = OBJECT.fullmatch(item["key"])
        if not match:
            unexpected.append(item)
            continue
        reference, digest, extension = match.groups()
        pairs.setdefault((reference, digest), {})[extension] = item
    versions = []
    for (reference, digest), pair in sorted(pairs.items()):
        audio, metadata = pair.get("mp3"), pair.get("json")
        problems = []
        record = None
        if not audio or not metadata:
            problems.append("pareja MP3/JSON incompleta")
        if metadata:
            if metadata["size"] > 1_000_000:
                problems.append("JSON demasiado grande")
            else:
                try:
                    record = json.loads(store.read(metadata["key"]))
                    if not isinstance(record, dict):
                        raise ValueError("El JSON no és un mapa")
                    generation = record["generation"]
                    voice = generation["voice"]
                    if (record.get("topic") != reference or record.get("generation_hash") != digest
                            or hashlib.sha256(canonical_bytes(generation)).hexdigest() != digest
                            or record.get("voice") != voice["model"]
                            or record.get("voice_sha256") != voice["sha256"]
                            or record.get("voice_config_sha256") != voice["config_sha256"]
                            or record.get("pipeline_version") != generation["pipeline"]
                            or not audio or record.get("mp3_size") != audio["size"]
                            or not re.fullmatch(r"[0-9a-f]{64}", record.get("mp3_sha256", ""))):
                        problems.append("metadatos inconsistentes")
                except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
                    problems.append(f"JSON inválido: {type(exc).__name__}")
        published = manifest is not None and manifest.get(reference, {}).get("hash") == digest
        versions.append({"topic": reference, "hash": digest, "published": published if manifest is not None else None,
                         "status": "published" if published else ("unreferenced" if manifest is not None else "unknown"),
                         "mp3_bytes": audio["size"] if audio else 0,
                         "metadata_bytes": metadata["size"] if metadata else 0,
                         "last_modified": audio["last_modified"] if audio else metadata["last_modified"],
                         "generated_at": record.get("generated_at") if isinstance(record, dict) else None,
                         "storage_class": audio["storage_class"] if audio else metadata["storage_class"],
                         "problems": problems})
    missing = []
    if manifest is not None:
        for reference, row in sorted(manifest.items()):
            if (reference, row["hash"]) not in pairs:
                missing.append({"topic": reference, "hash": row["hash"], "problem": "versión publicada ausente"})
            elif not all(pairs[(reference, row["hash"])].get(ext) for ext in ("mp3", "json")):
                missing.append({"topic": reference, "hash": row["hash"], "problem": "versión publicada incompleta"})
    return {"manifest_provided": manifest is not None, "versions": versions,
            "unexpected_objects": unexpected, "missing_published": missing}


def summary(report: dict) -> dict:
    versions = report["versions"]
    audio_bytes = sum(row["mp3_bytes"] for row in versions)
    metadata_bytes = sum(row["metadata_bytes"] for row in versions)
    unexpected_bytes = sum(row["size"] for row in report["unexpected_objects"])
    groups = {}
    for depth, name in ((1, "subjects"), (2, "courses"), (3, "topics")):
        groups[name] = {}
        for row in versions:
            key = "/".join(row["topic"].split("/")[:depth])
            group = groups[name].setdefault(key, {"versions": 0, "mp3_bytes": 0, "metadata_bytes": 0})
            group["versions"] += 1
            group["mp3_bytes"] += row["mp3_bytes"]
            group["metadata_bytes"] += row["metadata_bytes"]
    total_bytes = audio_bytes + metadata_bytes + unexpected_bytes
    percent = round(100 * total_bytes / FREE_BYTES, 2)
    level = next((label for threshold, label in ((95, "intervention"), (85, "review"), (70, "notice"))
                  if percent >= threshold), "ok")
    return {"podcasts": len(groups["topics"]), "versions": len(versions), "mp3_bytes": audio_bytes,
            "metadata_bytes": metadata_bytes, "unexpected_bytes": unexpected_bytes,
            "unexpected_objects": len(report["unexpected_objects"]),
            "total_object_bytes": total_bytes, "free_tier_payload_percent": percent, "capacity_level": level,
            "unreferenced_mp3_bytes": sum(row["mp3_bytes"] for row in versions if row["status"] == "unreferenced"),
            "incomplete_versions": sum(bool(row["problems"]) for row in versions),
            "missing_published": report["missing_published"], **groups}


def retention(report: dict) -> dict:
    if not report["manifest_provided"]:
        raise ValueError("Cal un manifest publicat per a analitzar la retenció")
    candidates = []
    for row in report["versions"]:
        if row["status"] == "unreferenced" or row["problems"]:
            candidates.append({**row, "reason": ", ".join(row["problems"]) if row["problems"] else
                               "no figura en el manifest actual", "deletion_risk":
                               "Puede estar enlazado desde una publicación anterior o externamente; no eliminar sin revisión."})
    return {"candidates": candidates, "candidate_mp3_bytes": sum(row["mp3_bytes"] for row in candidates),
            "missing_published": report["missing_published"], "unexpected_objects": report["unexpected_objects"]}
