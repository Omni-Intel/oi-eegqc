"""Public storage targets shared by upload clients and the signing service."""
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

S3_ENDPOINT = "http://172.16.4.2:9000"
S3_BUCKET = "oi-data"
S3_MULTIPART_THRESHOLD = 128 * 1024**2


def normalize_destination(target):
    if not isinstance(target, dict):
        raise ValueError("invalid storage target")
    provider = target.get("provider", "cos")
    if provider not in ("cos", "s3"):
        raise ValueError("unsupported storage provider")
    bucket = target.get("bucket")
    prefix = target.get("prefix")
    if not isinstance(prefix, str):
        raise ValueError("invalid storage prefix")
    prefix = prefix.strip("/")
    if not isinstance(bucket, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise ValueError("invalid storage bucket")
    if not prefix or "\x00" in prefix or "\\" in prefix or any(p in ("", ".", "..") for p in prefix.split("/")):
        raise ValueError("invalid storage prefix")
    result = dict(provider=provider, bucket=bucket, prefix=prefix,
                  region=target.get("region") or ("us-east-1" if provider == "s3" else "ap-beijing"))
    if provider == "s3":
        endpoint = urlsplit(target["endpoint"])
        if (endpoint.scheme not in ("http", "https") or not endpoint.hostname
                or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
                or endpoint.path not in ("", "/")
                or any(c.isspace() for c in endpoint.netloc)):
            raise ValueError("invalid S3 endpoint")
        # Accessing port also rejects malformed or out-of-range ports.
        port = endpoint.port
        if port == 0:
            raise ValueError("invalid S3 endpoint port")
        hostname = endpoint.hostname.lower()
        host = f"[{hostname}]" if ":" in hostname else hostname
        if port is not None and port != (443 if endpoint.scheme == "https" else 80):
            host += f":{port}"
        result["endpoint"] = urlunsplit((endpoint.scheme, host, "", "", ""))
    return result


def destination_id(target):
    public = normalize_destination(target)
    return hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()[:16]


def queue_directory(data_directory, paradigm):
    code = paradigm["code"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", code):
        raise ValueError("invalid paradigm code")
    target = normalize_destination(paradigm["destination"])
    root = Path(data_directory)
    if target["provider"] == "s3":
        return root / "uploads" / f"{code}-{destination_id(target)}"
    return root / ("uploads-cos-beijing" if code == "AVEEG-20260923" else f"uploads/{code}")


def destination_text(target):
    public = normalize_destination(target)
    text = f"{public['provider']}://{public['bucket']}/{public['prefix']}/"
    return text + (f" · {public['endpoint']}" if public["provider"] == "s3" else "")


def single_put_limit(target):
    return S3_MULTIPART_THRESHOLD if target and target.get("provider") == "s3" else 5 * 1024**3
