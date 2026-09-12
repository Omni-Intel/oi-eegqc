"""Check the project release mirror for a newer Windows build.

The app fetches release metadata and verified installers. It never uploads recordings.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

GITHUB_REPO = "Omni-Intel/oi-eegqc"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
MIRROR_ORIGIN = "https://pack.kunpeng.blog"
MIRROR_RELEASE_URL = MIRROR_ORIGIN + "/oi-eegqc/latest.json"
INSTALLER_ASSET = "OI-EEGQC-Setup-Windows-x64.exe"
ZIP_ASSET = "OI-EEGQC-Windows-x64.zip"
TIMEOUT_S = 8


@dataclass(frozen=True)
class UpdateInfo:
    status: str  # current | available | error
    current: str
    latest: str = ""
    release_url: str = ""
    asset_url: str = ""
    asset_name: str = ""
    digest: str = ""
    size: int = 0


def version_tuple(text: str) -> tuple[int, int, int]:
    raw = str(text).strip()
    if raw[:1] in "vV":
        raw = raw[1:]
    parts: list[int] = []
    for chunk in raw.split("."):
        digits = ""
        for char in chunk:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
        if len(parts) == 3:
            break
    if not parts:
        raise ValueError("invalid version")
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def pick_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    by_name = {str(item.get("name") or ""): item for item in assets if isinstance(item, dict)}
    for name in (INSTALLER_ASSET, ZIP_ASSET):
        if name in by_name and by_name[name].get("browser_download_url"):
            return by_name[name]
    for item in assets:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if name.endswith(".exe") and "Setup" in name and item.get("browser_download_url"):
            return item
    return None


def interpret_release(payload: dict[str, Any], current_version: str) -> UpdateInfo:
    current = str(current_version).strip()
    tag = str(payload.get("tag_name") or "").strip()
    release_url = str(payload.get("html_url") or RELEASES_PAGE)
    try:
        latest_tuple = version_tuple(tag)
        current_tuple = version_tuple(current)
    except ValueError:
        return UpdateInfo(status="error", current=current)
    asset = pick_asset(list(payload.get("assets") or []))
    info = UpdateInfo(
        status="available" if latest_tuple > current_tuple else "current",
        current=current,
        latest=tag.lstrip("vV") or tag,
        release_url=release_url,
        asset_url=str((asset or {}).get("browser_download_url") or ""),
        asset_name=str((asset or {}).get("name") or ""),
        digest=str((asset or {}).get("digest") or ""),
        size=int((asset or {}).get("size") or 0),
    )
    return info


def fetch_latest_release(
    url: str = LATEST_RELEASE_URL,
    *,
    current_version: str = "",
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"oi-eegqc-desktop/{current_version or 'dev'}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("OI_EEGQC_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    from urllib.parse import urlsplit
    if token and urlsplit(url).scheme == "https" and urlsplit(url).netloc == "api.github.com":
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    open_url = opener or urlopen
    with open_url(request, timeout=TIMEOUT_S) as response:
        raw = response.read(1_000_000)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("release payload must be an object")
    return payload


def check_update(
    current_version: str,
    *,
    url: str | None = None,
    opener: Callable[..., Any] | None = None,
) -> UpdateInfo:
    endpoint = url or os.environ.get("OI_EEGQC_RELEASES_URL") or MIRROR_RELEASE_URL
    try:
        payload = fetch_latest_release(
            endpoint, current_version=current_version, opener=opener
        )
        return interpret_release(payload, current_version)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError):
        return UpdateInfo(status="error", current=current_version)


def downloadable(info):
    import re
    from urllib.parse import urlsplit
    url = urlsplit(info.asset_url)
    github_path = f"/{GITHUB_REPO}/releases/download/v{info.latest}/{INSTALLER_ASSET}"
    mirror_path = f"/oi-eegqc/releases/v{info.latest}/{INSTALLER_ASSET}"
    trusted = (
        url.netloc == "github.com" and url.path == github_path
    ) or (
        url.netloc == "pack.kunpeng.blog" and url.path == mirror_path
    )
    return (info.status == "available" and info.asset_name == INSTALLER_ASSET
            and url.scheme == "https" and trusted
            and not url.query and not url.fragment
            and re.fullmatch(r"sha256:[a-fA-F0-9]{64}", info.digest) is not None
            and 0 < info.size <= 1_000_000_000)


def installer_opener():
    from urllib.parse import urlsplit
    from urllib.request import HTTPRedirectHandler, build_opener

    class SafeRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            url = urlsplit(newurl)
            if url.scheme != "https" or url.netloc not in {
                "pack.kunpeng.blog", "github.com", "release-assets.githubusercontent.com",
                "objects.githubusercontent.com"
            }:
                raise ValueError("更新下载跳转不受信任")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return build_opener(SafeRedirect()).open


class DownloadCancelled(Exception):
    pass


def download_installer(info, cache, progress, cancelled, *, opener=None):
    """Download only the fixed repository installer; never forward API credentials."""
    import hashlib
    import tempfile
    import time
    from pathlib import Path
    if not downloadable(info):
        raise ValueError("无法验证更新来源")
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="update-", dir=cache))
    partial = folder / "download.part"
    target = folder / INSTALLER_ASSET
    started = time.monotonic()
    try:
        digest, total = hashlib.sha256(), 0
        request = Request(info.asset_url, headers={"User-Agent": "oi-eegqc-updater"})
        with (opener or installer_opener())(request, timeout=8) as response, partial.open("xb") as output:
            while True:
                if cancelled():
                    raise DownloadCancelled()
                if time.monotonic() - started > 1800:
                    raise TimeoutError()
                block = response.read(256 * 1024)
                if not block:
                    break
                total += len(block)
                if total > info.size:
                    raise ValueError("更新文件大小不符")
                output.write(block)
                digest.update(block)
                progress(int(total * 100 / info.size))
        if total != info.size or digest.hexdigest() != info.digest[7:].lower():
            raise ValueError("更新文件校验失败")
        if cancelled():
            raise DownloadCancelled()
        partial.rename(target)
        return target
    except Exception:
        partial.unlink(missing_ok=True)
        folder.rmdir()
        raise


def launch_installer(path, info):
    """Recheck the downloaded file immediately before handing off to Windows."""
    import hashlib
    import subprocess
    import sys
    from pathlib import Path
    path = Path(path)
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        raise OSError("请使用桌面安装包更新")
    if not downloadable(info) or path.stat().st_size != info.size:
        raise ValueError("更新文件校验失败")
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != info.digest[7:].lower():
            raise ValueError("更新文件校验失败")
    return subprocess.Popen([str(path), "/UPDATE=1", "/NORESTART", "/NOFORCECLOSEAPPLICATIONS"],
                            close_fds=True)
