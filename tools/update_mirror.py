"""Publish the latest Windows installer into a static HTTPS directory."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen


REPOSITORY = "Omni-Intel/oi-eegqc"
INSTALLER = "OI-EEGQC-Setup-Windows-x64.exe"
PUBLIC_ORIGIN = "https://pack.kunpeng.blog/oi-eegqc"


def fetch_json(url):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "oi-eegqc-update-mirror"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        return json.load(response)


def main():
    destination = Path(os.environ.get("OI_EEGQC_MIRROR_ROOT", "/var/www/oi-eegqc"))
    release = fetch_json(f"https://api.github.com/repos/{REPOSITORY}/releases/latest")
    tag = str(release["tag_name"])
    asset = next(item for item in release["assets"] if item["name"] == INSTALLER)
    target_dir = destination / "releases" / tag
    target = target_dir / INSTALLER
    target_dir.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.stat().st_size != int(asset["size"]):
        with tempfile.NamedTemporaryFile(dir=target_dir, delete=False) as temporary:
            partial = Path(temporary.name)
            request = Request(asset["browser_download_url"], headers={"User-Agent": "oi-eegqc-update-mirror"})
            with urlopen(request, timeout=60) as response:
                shutil.copyfileobj(response, temporary, 1024 * 1024)
        if partial.stat().st_size != int(asset["size"]):
            partial.unlink(missing_ok=True)
            raise ValueError("installer size differs from GitHub metadata")
        partial.replace(target)
        target.chmod(0o644)
    else:
        target.chmod(0o644)
    with target.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    payload = {
        "tag_name": tag,
        "html_url": release["html_url"],
        "assets": [{
            "name": INSTALLER,
            "browser_download_url": f"{PUBLIC_ORIGIN}/releases/{tag}/{INSTALLER}",
            "size": target.stat().st_size,
            "digest": "sha256:" + digest,
        }],
    }
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination, delete=False) as stream:
        json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
        temporary_json = Path(stream.name)
    temporary_json.replace(destination / "latest.json")
    (destination / "latest.json").chmod(0o644)


if __name__ == "__main__":
    main()
