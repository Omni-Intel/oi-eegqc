"""Strict HTTPS transport. Anonymous signing and direct object uploads."""
import http.client
import json
import ssl
import socket
import threading
from urllib.parse import quote, urlsplit, unquote

ORIGIN = "https://eeg-upload.kunpeng.blog"
PREFIX = "eeg/inbox/"
MAX_SINGLE_PUT = 5 * 1024**3
PART_SIZE = 64 * 1024**2


class ServiceError(Exception):
    def __init__(self, status):
        self.status_code = status


class NetworkUnavailable(Exception):
    """No allocating request was sent."""


def validate_put_url(url, object_key):
    parsed = urlsplit(url)
    hosts = {"xiekp.tos-cn-beijing.volces.com", "tos-cn-beijing.volces.com"}
    expected = "/" + object_key if parsed.hostname == "xiekp.tos-cn-beijing.volces.com" else "/xiekp/" + object_key
    if (parsed.scheme != "https" or parsed.netloc not in hosts or parsed.fragment
            or unquote(parsed.path) != expected or not parsed.query):
        raise ValueError("invalid signed destination")


class SignClient:
    def __init__(self, data_directory=None):
        self.stopped = threading.Event()
        self.connection_lock = threading.Lock()
        self.connection = None

    def interrupt(self):
        self.stopped.set()
        with self.connection_lock:
            connection = self.connection
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

    def _release(self, connection):
        with self.connection_lock:
            if self.connection is connection:
                self.connection = None
            connection.close()

    def health(self):
        connection = http.client.HTTPSConnection("eeg-upload.kunpeng.blog", timeout=8, context=ssl.create_default_context())
        self._track(connection)
        try:
            connection.request("GET", "/health")
            if connection.getresponse().status != 200:
                raise NetworkUnavailable()
        except (OSError, http.client.HTTPException):
            raise NetworkUnavailable() from None
        finally:
            self._release(connection)

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
        connection = http.client.HTTPSConnection("eeg-upload.kunpeng.blog", timeout=30, context=ssl.create_default_context())
        self._track(connection)
        try:
            # Connect and verify TLS before any allocating HTTP request is sent.
            try:
                connection.connect()
            except ssl.SSLError:
                raise
            except (OSError, http.client.HTTPException):
                raise NetworkUnavailable() from None
            connection.request(method, route, body=body, headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
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
        validate_put_url(url, key)
        parsed = urlsplit(url)
        connection = http.client.HTTPSConnection(parsed.hostname, timeout=30, context=ssl.create_default_context())
        self._track(connection)
        stream = None
        try:
            if cancelled():
                raise InterruptedError()
            stream = open(source, "rb") if source is not None else None
            if stream:
                stream.seek(offset)
            connection.putrequest("PUT", parsed.path + "?" + parsed.query)
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
