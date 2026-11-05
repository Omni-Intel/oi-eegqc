"""LAN signing service and direct uploads to configured COS or RustFS targets."""
import http.client
import json
import os
import ssl
import socket
import threading
from urllib.parse import quote, urlsplit, unquote

from .upload_destination import normalize_destination, destination_id

INTRANET_HOST = "personnel.intra.omni-intel.cn"
INTRANET_PORT = 8443
ALLOWED_HOSTS = {INTRANET_HOST, "172.16.1.249", "127.0.0.1", "localhost"}
COS_HOST = "neurolm-1442740494.cos.ap-beijing.myqcloud.com"
ORIGIN = os.environ.get("OI_EEGQC_UPLOAD_ORIGIN", f"https://{INTRANET_HOST}")
if os.environ.get("OI_EEGQC_UPLOAD_ORIGIN"):
    configured_origin = urlsplit(ORIGIN)
    if configured_origin.scheme == 'https' and configured_origin.hostname:
        ALLOWED_HOSTS.add(configured_origin.hostname)
PREFIX = "neuro-lm/data/inbox/"
MAX_SINGLE_PUT = 5 * 1024**3
PART_SIZE = 64 * 1024**2


class ServiceError(Exception):
    def __init__(self, status):
        self.status_code = status


class NetworkUnavailable(Exception):
    """No allocating request was sent."""


def _origin():
    parsed = urlsplit(ORIGIN)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("upload origin is not an allowed intranet host")
    return parsed


def validate_put_url(url, object_key, destination=None):
    parsed = urlsplit(url)
    destination = destination or {}
    if destination.get("provider") == "s3":
        target = normalize_destination(destination)
        endpoint = urlsplit(target["endpoint"])
        if (not _same_endpoint(parsed, endpoint) or parsed.username or parsed.password
                or parsed.fragment or not parsed.query
                or unquote(parsed.path) != f"/{target['bucket']}/{object_key}"
                or not object_key.startswith(target["prefix"] + "/")
                or "\\" in object_key
                or any(p in ("", ".", "..") for p in object_key.rstrip("/").split("/"))):
            raise ValueError("invalid S3 destination")
        return
    host = f"{destination['bucket']}.cos.{destination['region']}.myqcloud.com" if destination else COS_HOST
    prefix = destination.get('prefix', PREFIX).rstrip('/') + '/'
    if parsed.hostname==host:
        if parsed.scheme!='https' or parsed.port not in (None,443) or parsed.username or parsed.password or parsed.fragment or not parsed.query or unquote(parsed.path)!='/'+object_key or not object_key.startswith(prefix) or '..' in object_key.split('/'):raise ValueError('invalid COS destination')
        return
    expected = "/objects/" + object_key
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.fragment
        or unquote(parsed.path) != expected
        or not parsed.query
    ):
        raise ValueError("invalid signed destination")


def _same_endpoint(parsed, endpoint):
    def port(url):
        return url.port if url.port is not None else (443 if url.scheme == "https" else 80)
    return (parsed.scheme, parsed.hostname, port(parsed)) == (endpoint.scheme, endpoint.hostname, port(endpoint))


def _connection(parsed, timeout, destination=None):
    if destination and destination.get("provider") == "s3":
        endpoint = urlsplit(normalize_destination(destination)["endpoint"])
        if not _same_endpoint(parsed, endpoint) or parsed.username or parsed.password:
            raise ValueError("upload host is not the configured S3 endpoint")
    else:
        host = f"{destination['bucket']}.cos.{destination['region']}.myqcloud.com" if destination else COS_HOST
        if parsed.hostname not in ALLOWED_HOSTS and not (parsed.hostname==host and parsed.scheme=='https'):
            raise ValueError("upload host is not an allowed intranet address")
    if parsed.scheme == "https":
        return http.client.HTTPSConnection(
            parsed.hostname, parsed.port, timeout=timeout, context=ssl.create_default_context()
        )
    if parsed.scheme == "http":
        return http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
    raise ValueError("invalid upload origin")


class SignClient:
    def __init__(self, data_directory=None):
        self.paradigm = None
        self.destination = None
        self.stopped = threading.Event()
        self.connection_lock = threading.Lock()
        self.connection = None
        self.connections = []

    def interrupt(self):
        self.stopped.set()
        with self.connection_lock:
            active = list(self.connections)
            if self.connection is not None and all(c is not self.connection for c in active):
                active.append(self.connection)
            for connection in active:
                sock = getattr(connection, "sock", None)
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

    def _track(self, connection):
        with self.connection_lock:
            if self.stopped.is_set():
                connection.close()
                raise InterruptedError()
            self.connection = connection
            if all(c is not connection for c in self.connections):
                self.connections.append(connection)

    def _release(self, connection):
        with self.connection_lock:
            self.connections = [c for c in self.connections if c is not connection]
            if self.connection is connection:
                self.connection = self.connections[-1] if self.connections else None
            connection.close()

    def paradigms(self):
        return self._request('/v1/uploads/paradigms', method='GET')['paradigms']

    def resolve_subjects(self, numbers):
        return self._request('/v1/capture/imports/resolve', {'participant_numbers': numbers})['subjects']

    def resolve_imports(self, numbers):
        return self._request('/v1/capture/imports/resolve', {'participant_numbers': numbers})

    def commit_import(self, payload):
        return self._request('/v1/capture/imports/commit', payload)

    def resolve_collection(self, files):
        return self._request('/v1/uploads/resolve', {'files':files})

    def health(self):
        return self._request("/upload-health" if _origin().hostname == INTRANET_HOST else "/health", method="GET")

    def _request(self, route, payload=None, method="POST"):
        # Only round requests have persisted request identities and idempotent handlers.
        attempts = 3 if "/rounds" in route else 1
        for attempt in range(attempts):
            try:
                return self._request_once(route, payload, method)
            except (NetworkUnavailable, OSError, http.client.HTTPException, ServiceError) as error:
                if isinstance(error, (ssl.SSLError, InterruptedError)) or (
                    isinstance(error, ServiceError) and error.status_code not in (408, 429, 500, 502, 503, 504)
                ) or attempt == attempts - 1:
                    raise
                if self.stopped.wait(2 ** attempt):
                    raise InterruptedError() from None

    def _request_once(self, route, payload=None, method="POST"):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        connection = _connection(_origin(), 30)
        self._track(connection)
        try:
            try:
                connection.connect()
            except ssl.SSLError:
                raise
            except (OSError, http.client.HTTPException):
                raise NetworkUnavailable() from None
            headers = {'Content-Type': 'application/json', 'Content-Length': str(len(body))}
            headers['X-EEG-Upload-Capabilities'] = 's3-targets-v1'
            if self.destination:
                headers['X-EEG-Storage-Target'] = destination_id(self.destination)
            if self.paradigm:
                headers['X-EEG-Paradigm'] = self.paradigm
            connection.request(method, route, body=body, headers=headers)
            response = connection.getresponse()
            if response.status != 200:
                raise ServiceError(response.status)
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("response too large")
            return json.loads(raw)
        finally:
            self._release(connection)

    def sign(self, paths, upload_id=None):
        payload = {"files": paths}
        if upload_id:
            payload["uploadId"] = upload_id
        return self._request("/v1/uploads/sign", payload)

    def complete(self, upload_id):
        return self._request(f"/v1/uploads/{upload_id}/complete")

    def start_round(self, upload_id, request_id, replaces_request_id=None):
        payload = {"clientRequestId": request_id}
        if replaces_request_id:
            payload["replacesRequestId"] = replaces_request_id
        if upload_id:
            payload["uploadId"] = upload_id
        return self._request("/v1/uploads/rounds", payload)

    def register_files(self, upload_id, round_id, files):
        return self._request(
            f"/v1/uploads/{upload_id}/rounds/{round_id}/files", {"files": files}
        )

    def seal_round(self, upload_id, round_id, file_count):
        return self._request(
            f"/v1/uploads/{upload_id}/rounds/{round_id}/seal", {"fileCount": file_count}
        )

    def round_status(self, upload_id, round_id):
        return self._request(
            f"/v1/uploads/{upload_id}/rounds/{round_id}", method="GET"
        )

    def sign_round(self, upload_id, round_id, paths):
        return self._request(
            f"/v1/uploads/{upload_id}/rounds/{round_id}/sign", {"files": paths}
        )

    def complete_files(self, upload_id, round_id, files):
        return self._request(
            f"/v1/uploads/{upload_id}/rounds/{round_id}/files/complete", {"files": files}
        )

    def start_multipart(self, upload_id, round_id, path, part_size=PART_SIZE):
        return self._request(
            f"/v1/uploads/{upload_id}/rounds/{round_id}/multipart",
            {"path": path, "partSize": part_size},
        )

    def list_parts(self, upload_id, round_id, path):
        route = (
            f"/v1/uploads/{upload_id}/rounds/{round_id}/multipart/parts"
            f"?path={quote(path, safe='')}"
        )
        return self._request(route, method="GET")

    def sign_parts(self, upload_id, round_id, path, part_numbers):
        return self._request(
            f"/v1/uploads/{upload_id}/rounds/{round_id}/multipart/parts/sign",
            {"path": path, "partNumbers": part_numbers},
        )

    def complete_multipart(self, upload_id, round_id, path):
        return self._request(
            f"/v1/uploads/{upload_id}/rounds/{round_id}/multipart/complete",
            {"path": path},
        )

    def complete_round(self, upload_id, round_id):
        return self._request(f"/v1/uploads/{upload_id}/rounds/{round_id}/complete")

    def put(self, url, key, source, size, cancelled, progress):
        return self._put_range(url, key, source, 0, size, cancelled, progress)

    def put_part(self, url, key, source, offset, size, cancelled, progress):
        return self._put_range(url, key, source, offset, size, cancelled, progress)

    def _put_range(self, url, key, source, offset, size, cancelled, progress):
        validate_put_url(url, key, self.destination)
        parsed = urlsplit(url)
        connection = _connection(parsed, 30, self.destination)
        self._track(connection)
        stream = None
        try:
            if cancelled():
                raise InterruptedError()
            stream = open(source, "rb") if source is not None else None
            if stream:
                stream.seek(offset)
            path = parsed.path + ("?" + parsed.query if parsed.query else "")
            connection.putrequest("PUT", path)
            connection.putheader("Content-Length", str(size))
            connection.endheaders()
            sent = 0
            while sent < size:
                if cancelled():
                    raise InterruptedError()
                block = stream.read(min(256 * 1024, size - sent))
                if not block:
                    raise OSError("source truncated")
                connection.send(block)
                sent += len(block)
                progress(sent)
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                raise ServiceError(response.status)
            return response.getheader("ETag") if hasattr(response, "getheader") else ""
        finally:
            if stream:
                stream.close()
            self._release(connection)

    def close(self):
        pass
