"""Strict HTTPS transport. Anonymous signing and direct object uploads."""
import http.client
import json
import ssl
from urllib.parse import urlsplit, unquote

ORIGIN = "https://eeg-upload.kunpeng.blog"
PREFIX = "eeg/inbox/"
MAX_FILE = 5 * 1024 ** 3


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
        pass

    def health(self):
        connection = http.client.HTTPSConnection("eeg-upload.kunpeng.blog", timeout=8, context=ssl.create_default_context())
        try:
            connection.request("GET", "/health")
            if connection.getresponse().status != 200:
                raise NetworkUnavailable()
        except (OSError, http.client.HTTPException):
            raise NetworkUnavailable() from None
        finally:
            connection.close()

    def _request(self, route, payload=None):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        connection = http.client.HTTPSConnection("eeg-upload.kunpeng.blog", timeout=30, context=ssl.create_default_context())
        try:
            # Connect and verify TLS before any allocating HTTP request is sent.
            try:
                connection.connect()
            except (OSError, http.client.HTTPException):
                raise NetworkUnavailable() from None
            connection.request("POST", route, body=body, headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
            response = connection.getresponse()
            if response.status != 200:
                raise ServiceError(response.status)
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("response too large")
            return json.loads(raw)
        finally:
            connection.close()

    def sign(self, paths, upload_id=None):
        payload = {"files": paths}
        if upload_id:
            payload["uploadId"] = upload_id
        return self._request("/v1/uploads/sign", payload)

    def complete(self, upload_id):
        return self._request(f"/v1/uploads/{upload_id}/complete")

    def put(self, url, key, source, size, cancelled, progress):
        validate_put_url(url, key)
        parsed = urlsplit(url)
        connection = http.client.HTTPSConnection(parsed.hostname, timeout=30, context=ssl.create_default_context())
        stream = None
        try:
            if cancelled():
                raise InterruptedError()
            stream = open(source, "rb") if source is not None else None
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
        finally:
            if stream:
                stream.close()
            connection.close()

    def close(self):
        pass
