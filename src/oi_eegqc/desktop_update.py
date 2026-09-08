"""Check GitHub Releases for a newer Windows build.

The app only GETs release metadata. It does not upload recordings.
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
    if token:
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
    endpoint = url or os.environ.get("OI_EEGQC_RELEASES_URL") or LATEST_RELEASE_URL
    try:
        payload = fetch_latest_release(
            endpoint, current_version=current_version, opener=opener
        )
        return interpret_release(payload, current_version)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError):
        return UpdateInfo(status="error", current=current_version)
