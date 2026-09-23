"""Filesystem inbox used on the LAN receiver. Object keys stay neuro-lm/data/inbox/..."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import time
from pathlib import Path
from urllib.parse import quote, urlencode

from .service import INBOX_PREFIX


class LocalInboxStorage:
    def __init__(self, root, public_url, secret, url_ttl=3600):
        self.root = Path(root)
        self.public_url = public_url.rstrip("/")
        self.secret = secret.encode("utf-8") if isinstance(secret, str) else secret
        self.url_ttl = url_ttl
        self.bucket = "local"
        self.prefix = INBOX_PREFIX
        self.root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls, root, public_url, secret_path, url_ttl=3600):
        secret_path = Path(secret_path)
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        if secret_path.exists():
            secret = secret_path.read_bytes().strip()
        else:
            secret = secrets.token_bytes(32)
            secret_path.write_bytes(secret)
            try:
                os.chmod(secret_path, 0o600)
            except OSError:
                pass
        return cls(root, public_url, secret, url_ttl=url_ttl)

    def resolve(self, key: str) -> Path:
        prefix = INBOX_PREFIX + "/"
        if not key.startswith(prefix) or "\\" in key or ".." in Path(key).parts:
            raise ValueError("invalid object key")
        relative = key[len(prefix) :]
        path = (self.root / relative).resolve()
        if path != self.root.resolve() and self.root.resolve() not in path.parents:
            raise ValueError("object key escapes inbox")
        return path

    def sign_put(self, key, extra=None):
        expires = str(int(time.time()) + self.url_ttl)
        payload = f"{key}\n{expires}".encode("utf-8")
        token = hmac.new(self.secret, payload, hashlib.sha256).hexdigest()
        query = {"expires": expires, "token": token}
        if extra:
            query.update(extra)
        return f"{self.public_url}/objects/{quote(key, safe='/')}?{urlencode(query)}"

    def verify_put(self, key, query) -> None:
        expires = (query.get("expires") or [""])[0]
        token = (query.get("token") or [""])[0]
        try:
            expiry = int(expires)
        except ValueError as exc:
            raise ValueError("invalid upload token") from exc
        if expiry < int(time.time()):
            raise ValueError("upload token expired")
        expected = hmac.new(
            self.secret, f"{key}\n{expires}".encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, token):
            raise ValueError("invalid upload token")

    def begin_round(self, upload_id, round_id, started_at):
        folder = self.resolve(f"{INBOX_PREFIX}/{upload_id}/_UPDATING").parent
        folder.mkdir(parents=True, exist_ok=True)
        complete = folder / "_COMPLETE"
        complete.unlink(missing_ok=True)
        payload = json.dumps(
            {"uploadId": upload_id, "roundId": round_id, "startedAt": started_at},
            separators=(",", ":"),
        ).encode("utf-8")
        (folder / "_UPDATING").write_bytes(payload)

    def finish_round(self, upload_id, round_id, completed_at):
        folder = self.resolve(f"{INBOX_PREFIX}/{upload_id}/_COMPLETE").parent
        folder.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"uploadId": upload_id, "roundId": round_id, "completedAt": completed_at},
            separators=(",", ":"),
        ).encode("utf-8")
        (folder / "_COMPLETE").write_bytes(payload)
        (folder / "_UPDATING").unlink(missing_ok=True)

    def promote(self, source, destination, etag):
        src = self.resolve(source)
        dst = self.resolve(destination)
        if not src.exists():
            raise FileNotFoundError(source)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst.unlink()
        shutil.copy2(src, dst)

    def discard_staged(self, key):
        path = self.resolve(key)
        path.unlink(missing_ok=True)

    def write_object(self, key, stream, size):
        path = self.resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".part")
        received = 0
        with temporary.open("wb") as handle:
            remaining = size
            while remaining:
                block = stream.read(min(256 * 1024, remaining))
                if not block:
                    raise OSError("upload truncated")
                handle.write(block)
                remaining -= len(block)
                received += len(block)
        if received != size:
            temporary.unlink(missing_ok=True)
            raise OSError("upload size mismatch")
        os.replace(temporary, path)
        return f'"{size}"'

    def create_multipart(self, key):
        upload_id = "local-" + secrets.token_hex(8)
        folder = self._parts_dir(upload_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "key.txt").write_text(key, encoding="utf-8")
        return upload_id

    def sign_part(self, key, multipart_upload_id, part_number):
        return self.sign_put(key, extra={"uploadId": multipart_upload_id, "partNumber": str(part_number)})

    def write_part(self, key, multipart_upload_id, part_number, stream, size):
        folder = self._parts_dir(multipart_upload_id)
        if not folder.exists() or (folder / "key.txt").read_text(encoding="utf-8") != key:
            raise ValueError("multipart upload is not initialized")
        part = folder / f"{part_number:05d}.bin"
        received = 0
        with part.open("wb") as handle:
            remaining = size
            while remaining:
                block = stream.read(min(256 * 1024, remaining))
                if not block:
                    raise OSError("upload truncated")
                handle.write(block)
                remaining -= len(block)
                received += len(block)
        if received != size:
            part.unlink(missing_ok=True)
            raise OSError("upload size mismatch")
        return {"partNumber": part_number, "size": size, "etag": f'"{size}"'}

    def list_parts(self, key, multipart_upload_id):
        folder = self._parts_dir(multipart_upload_id)
        if not folder.exists():
            return []
        result = []
        for path in sorted(folder.glob("*.bin")):
            number = int(path.stem)
            size = path.stat().st_size
            result.append({"partNumber": number, "size": size, "etag": f'"{size}"'})
        return result

    def complete_multipart(self, key, multipart_upload_id, parts):
        dest = self.resolve(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        folder = self._parts_dir(multipart_upload_id)
        with dest.open("wb") as handle:
            for part in parts:
                chunk = folder / f"{part['partNumber']:05d}.bin"
                with chunk.open("rb") as source:
                    shutil.copyfileobj(source, handle)
        shutil.rmtree(folder, ignore_errors=True)
        return f'"{dest.stat().st_size}"'

    def _parts_dir(self, multipart_upload_id):
        return self.root / ".multipart" / multipart_upload_id
