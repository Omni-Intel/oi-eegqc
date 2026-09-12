from __future__ import annotations

import datetime as dt
import json
import os
import re
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .service import (
    INBOX_PREFIX,
    MAX_FILES_PER_REQUEST,
    UPLOAD_ID_RE,
    Conflict,
    NotFound,
    UploadCoordinator,
    make_upload_id,
    normalize_path,
)
from .tos_backend import TosStorage


ROUND_ROUTE = re.compile(
    r"^/v1/uploads/(?P<upload>[^/]+)/rounds/(?P<round>[^/]+)(?:/(?P<action>.*))?$"
)


class UploadServer(ThreadingHTTPServer):
    def __init__(self, address, coordinator, storage):
        super().__init__(address, UploadHandler)
        self.coordinator = coordinator
        self.storage = storage


class UploadHandler(BaseHTTPRequestHandler):
    server_version = "EEGUploadSigner/2.0"

    @property
    def coordinator(self):
        return self.server.coordinator

    @property
    def storage(self):
        return self.server.storage

    def log_message(self, fmt, *args):
        print(f"{self.client_address[0]} {fmt % args}", flush=True)

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self, required=True):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if not length and not required:
            return {}
        if not 1 <= length <= 8 * 1024 * 1024:
            raise ValueError("request body size is invalid")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("request body must be UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("request body must be an object")
        return value

    def dispatch(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        if method == "GET" and path == "/health":
            return {
                "status": "ok",
                "service": "eeg-upload-signer",
                "version": 2,
                "bucket": self.storage.bucket,
                "prefix": INBOX_PREFIX + "/",
                "capabilities": ["rounds", "multipart", "round-replacement", "staged-put"],
            }
        if method == "POST" and path == "/v1/uploads/sign":
            return self.legacy_sign()
        if method == "POST" and path == "/v1/uploads/rounds":
            body = self.read_json()
            result = self.coordinator.start_round(
                body.get("uploadId"), body.get("clientRequestId"), body.get("replacesRequestId")
            )
            return {
                "uploadId": result["upload_id"],
                "roundId": result["round_id"],
                "state": "updating",
            }
        legacy_complete = re.fullmatch(r"/v1/uploads/([^/]+)/complete", path)
        if method == "POST" and legacy_complete:
            return self.legacy_complete(legacy_complete.group(1))
        match = ROUND_ROUTE.fullmatch(path)
        if not match:
            raise NotFound("route not found")
        upload_id, round_id = match.group("upload"), match.group("round")
        action = (match.group("action") or "").rstrip("/")
        if method == "GET" and not action:
            return self.coordinator.round_status(upload_id, round_id)
        if method == "GET" and action == "multipart/parts":
            values = parse_qs(parsed.query)
            path_value = values.get("path", [None])[0]
            return {"parts": self.coordinator.list_parts(upload_id, round_id, path_value)}
        if method != "POST":
            raise NotFound("route not found")
        body = self.read_json(required=action in {"files", "seal", "sign", "files/complete", "multipart", "multipart/parts/sign", "multipart/complete"})
        if action == "files":
            return {"files": self.coordinator.register_files(upload_id, round_id, body.get("files"))}
        if action == "seal":
            return self.coordinator.seal_round(upload_id, round_id, body.get("fileCount"))
        if action == "sign":
            objects = self.coordinator.sign_single(upload_id, round_id, body.get("files"))
            return {
                "uploadId": upload_id,
                "roundId": round_id,
                "prefix": f"{INBOX_PREFIX}/{upload_id}/",
                "expiresAt": self.expires_at(),
                "objects": objects,
            }
        if action == "files/complete":
            return self.coordinator.mark_single_complete(upload_id, round_id, body.get("files"))
        if action == "multipart":
            return self.coordinator.start_multipart(
                upload_id, round_id, body.get("path"), body.get("partSize")
            )
        if action == "multipart/parts/sign":
            parts = self.coordinator.sign_parts(
                upload_id, round_id, body.get("path"), body.get("partNumbers")
            )
            return {"parts": parts, "expiresAt": self.expires_at()}
        if action == "multipart/complete":
            return self.coordinator.complete_multipart(upload_id, round_id, body.get("path"))
        if action == "complete":
            return self.coordinator.complete_round(upload_id, round_id)
        raise NotFound("route not found")

    def expires_at(self):
        expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=self.storage.url_ttl)
        return expires.isoformat().replace("+00:00", "Z")

    def legacy_sign(self):
        body = self.read_json()
        upload_id = body.get("uploadId") or make_upload_id()
        if not isinstance(upload_id, str) or not UPLOAD_ID_RE.fullmatch(upload_id):
            raise ValueError("invalid uploadId")
        if body.get("uploadId"):
            self.coordinator.ensure_legacy_allowed(upload_id)
        files = body.get("files")
        if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES_PER_REQUEST:
            raise ValueError("invalid file list")
        paths = [normalize_path(path) for path in files]
        if len(set(paths)) != len(paths):
            raise ValueError("duplicate file path")
        objects = []
        for path in paths:
            key = f"{INBOX_PREFIX}/{upload_id}/{path}"
            objects.append({"path": path, "objectKey": key, "putUrl": self.storage.sign_put(key)})
        return {
            "uploadId": upload_id,
            "bucket": self.storage.bucket,
            "prefix": f"{INBOX_PREFIX}/{upload_id}/",
            "expiresAt": self.expires_at(),
            "objects": objects,
        }

    def legacy_complete(self, upload_id):
        self.coordinator.ensure_legacy_allowed(upload_id)
        key = f"{INBOX_PREFIX}/{upload_id}/_COMPLETE"
        return {"uploadId": upload_id, "objectKey": key, "putUrl": self.storage.sign_put(key)}

    def do_GET(self):
        self.respond("GET")

    def do_POST(self):
        self.respond("POST")

    def respond(self, method):
        try:
            self.send_json(200, self.dispatch(method))
        except Conflict as exc:
            self.send_json(409, {"error": str(exc)})
        except NotFound as exc:
            self.send_json(404, {"error": str(exc)})
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception:
            self.send_json(500, {"error": "upload service failed"})
            raise


def main():
    config = os.environ.get("TOS_CONFIG", "/home/xiekp/.tosutilconfig")
    state = os.environ.get(
        "UPLOAD_STATE", "/home/xiekp/.local/share/eeg-upload-signer/state.sqlite3"
    )
    storage = TosStorage.from_tosutil_config(config)
    coordinator = UploadCoordinator(state, storage)
    server = UploadServer(
        (os.environ.get("LISTEN_HOST", "0.0.0.0"), int(os.environ.get("LISTEN_PORT", "8443"))),
        coordinator,
        storage,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(
        certfile=os.environ.get("TLS_CERT", "/home/xiekp/.config/eeg-upload-signer/tls.crt"),
        keyfile=os.environ.get("TLS_KEY", "/home/xiekp/.config/eeg-upload-signer/tls.key"),
    )
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f"listening on https://{server.server_address[0]}:{server.server_address[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
