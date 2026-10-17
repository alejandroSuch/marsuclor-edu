"""Cloudflare R2 via its S3-compatible API; errors are never treated as misses."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from . import InconsistentObject, StorageError


class R2Storage:
    def __init__(self, *, bucket: str, account_id: str, access_key: str, secret_key: str, public_base: str):
        import boto3
        from botocore.config import Config

        if not all((bucket, account_id, access_key, secret_key)):
            raise StorageError("Falten dades de configuració R2")
        parsed = urlparse(public_base)
        if (parsed.scheme != "https" or not parsed.hostname
                or not re.fullmatch(r"[a-z0-9-]+\.[a-z0-9-]+\.workers\.dev", parsed.hostname)
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise StorageError("La URL pública ha de ser HTTPS en workers.dev")
        self.bucket = bucket
        self.public_base = public_base.rstrip("/")
        self.client = boto3.client("s3", endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
                                   aws_access_key_id=access_key, aws_secret_access_key=secret_key,
                                   region_name="auto", config=Config(retries={"mode": "standard", "max_attempts": 3}))

    @classmethod
    def from_env(cls):
        return cls(bucket=os.getenv("R2_BUCKET_NAME", "martina-edu-podcasts"),
                   account_id=os.getenv("CLOUDFLARE_ACCOUNT_ID", ""),
                   access_key=os.getenv("R2_ACCESS_KEY_ID", ""),
                   secret_key=os.getenv("R2_SECRET_ACCESS_KEY", ""),
                   public_base=os.getenv("MARTINA_AUDIO_BASE_URL", ""))

    def _call(self, operation, **kwargs):
        from botocore.exceptions import BotoCoreError, ClientError

        name = getattr(operation, "__name__", "S3")
        try:
            return operation(Bucket=self.bucket, **kwargs)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in ("NoSuchKey", "404", "NotFound") and status == 404:
                return None
            if code in ("PreconditionFailed", "412") or status == 412:
                raise InconsistentObject(f"L'objecte ja existeix: {kwargs.get('Key')}") from exc
            raise StorageError(f"R2 {name}: {code or status}") from exc
        except BotoCoreError as exc:
            raise StorageError(f"R2 {name}: error de xarxa o client") from exc

    def head(self, key: str) -> dict | None:
        result = self._call(self.client.head_object, Key=key)
        if result is None:
            return None
        return {"size": result["ContentLength"], "sha256": result.get("Metadata", {}).get("sha256")}

    def check_bucket(self) -> None:
        # Object-scoped tokens need not have bucket administration permissions.
        self.head("podcasts/.martina-connectivity-check")

    def read(self, key: str) -> bytes:
        from botocore.exceptions import BotoCoreError
        result = self._call(self.client.get_object, Key=key)
        if result is None:
            raise InconsistentObject(f"L'objecte ha desaparegut: {key}")
        try:
            return result["Body"].read()
        except (BotoCoreError, OSError) as exc:
            raise StorageError(f"Lectura R2 incompleta: {key}") from exc
        finally:
            result["Body"].close()

    def sha256(self, key: str) -> str:
        from botocore.exceptions import BotoCoreError
        result = self._call(self.client.get_object, Key=key)
        if result is None:
            raise InconsistentObject(f"L'objecte ha desaparegut: {key}")
        digest = hashlib.sha256()
        try:
            for block in result["Body"].iter_chunks(chunk_size=1024 * 1024):
                digest.update(block)
        except (BotoCoreError, OSError) as exc:
            raise StorageError(f"Lectura R2 incompleta: {key}") from exc
        finally:
            result["Body"].close()
        return digest.hexdigest()

    def put_file_once(self, key: str, source: Path, content_type: str, digest: str) -> None:
        with source.open("rb") as stream:
            self._call(self.client.put_object, Key=key, Body=stream, ContentLength=source.stat().st_size,
                       ContentType=content_type, Metadata={"sha256": digest}, IfNoneMatch="*",
                       StorageClass="STANDARD")

    def put_bytes_once(self, key: str, data: bytes, content_type: str) -> None:
        self._call(self.client.put_object, Key=key, Body=data, ContentLength=len(data),
                   ContentType=content_type, IfNoneMatch="*", StorageClass="STANDARD")

    def url(self, key: str) -> str:
        return f"{self.public_base}/{key}"
