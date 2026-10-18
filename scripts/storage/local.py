"""Filesystem implementation of the same create-once object interface."""

from __future__ import annotations

import shutil
from pathlib import Path

from . import InconsistentObject, sha256_file


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        if key.startswith("/") or ".." in Path(key).parts:
            raise ValueError("Clau d'objecte invàlida")
        return self.root / key

    def head(self, key: str) -> dict | None:
        path = self._path(key)
        if not path.is_file():
            return None
        digest_path = path.with_name(path.name + ".sha256")
        return {"size": path.stat().st_size, "sha256": digest_path.read_text() if digest_path.exists() else None}

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def sha256(self, key: str) -> str:
        return sha256_file(self._path(key))

    def put_file_once(self, key: str, source: Path, content_type: str, digest: str) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("xb") as destination, source.open("rb") as stream:
                shutil.copyfileobj(stream, destination)
        except FileExistsError as exc:
            raise InconsistentObject(f"L'objecte ja existeix: {key}") from exc
        target.with_name(target.name + ".sha256").write_text(digest)

    def put_bytes_once(self, key: str, data: bytes, content_type: str) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("xb") as stream:
                stream.write(data)
        except FileExistsError as exc:
            raise InconsistentObject(f"L'objecte ja existeix: {key}") from exc

    def url(self, key: str) -> str:
        return self._path(key).as_uri()
