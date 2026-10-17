"""Immutable podcast object storage. Only a confirmed missing object permits creation."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


class StorageError(RuntimeError):
    pass


class InconsistentObject(StorageError):
    pass


def object_keys(reference: str, fingerprint: str) -> tuple[str, str]:
    if not re.fullmatch(r"[a-z0-9-]+/[1-4]-eso/[a-z0-9-]+", reference):
        raise ValueError("Identificador de tema invàlid")
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("Empremta invàlida")
    prefix = f"podcasts/{reference}/{fingerprint}"
    return prefix + ".mp3", prefix + ".json"


def canonical_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_record(store, reference: str, fingerprint: str) -> dict | None:
    """Return a fully verified record, None for two absent keys, or fail closed."""
    mp3_key, json_key = object_keys(reference, fingerprint)
    audio = store.head(mp3_key)
    info = store.head(json_key)
    if audio is None and info is None:
        return None
    if audio is None or info is None:
        raise InconsistentObject(f"Parell MP3/JSON incomplet: {mp3_key}")
    if info["size"] > 1_000_000:
        raise InconsistentObject(f"Metadades massa grans: {json_key}")
    try:
        record = json.loads(store.read(json_key))
    except (ValueError, UnicodeDecodeError) as exc:
        raise InconsistentObject(f"JSON invàlid: {json_key}") from exc
    if not isinstance(record, dict) or not isinstance(record.get("generation"), dict):
        raise InconsistentObject(f"Metadades inconsistents: {json_key}")
    try:
        computed = hashlib.sha256(canonical_bytes(record["generation"])).hexdigest()
    except (TypeError, ValueError) as exc:
        raise InconsistentObject(f"Generació invàlida: {json_key}") from exc
    voice = record["generation"].get("voice", {})
    if not isinstance(voice, dict):
        raise InconsistentObject(f"Veu invàlida: {json_key}")
    if (computed != fingerprint or record.get("topic") != reference or record.get("generation_hash") != fingerprint
            or record.get("voice") != voice.get("model")
            or record.get("voice_sha256") != voice.get("sha256")
            or record.get("voice_config_sha256") != voice.get("config_sha256")
            or record.get("pipeline_version") != record["generation"].get("pipeline")
            or record.get("mp3_size") != audio["size"] or audio["size"] < 1000
            or not re.fullmatch(r"[0-9a-f]{64}", record.get("mp3_sha256", ""))
            or audio.get("sha256") != record["mp3_sha256"]):
        raise InconsistentObject(f"Metadades inconsistents: {mp3_key}")
    if store.sha256(mp3_key) != record["mp3_sha256"]:
        raise InconsistentObject(f"Checksum remot incorrecte: {mp3_key}")
    return record


def put_record(store, reference: str, fingerprint: str, mp3: Path, record: dict) -> dict:
    """Create MP3 then JSON. On interruption, leave evidence for manual recovery."""
    if check_record(store, reference, fingerprint) is not None:
        raise InconsistentObject("La versió ja existeix; no se sobreescriu")
    mp3_key, json_key = object_keys(reference, fingerprint)
    if record.get("mp3_size") != mp3.stat().st_size or record.get("mp3_sha256") != sha256_file(mp3):
        raise InconsistentObject("El MP3 local no coincidix amb les metadades")
    store.put_file_once(mp3_key, mp3, "audio/mpeg", record["mp3_sha256"])
    store.put_bytes_once(json_key, canonical_bytes(record), "application/json")
    verified = check_record(store, reference, fingerprint)
    if verified is None:
        raise InconsistentObject("La pujada no es pot recuperar")
    return verified
