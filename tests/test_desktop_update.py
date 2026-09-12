import io
import json
import hashlib
from dataclasses import replace
import pytest
from urllib.error import URLError
from urllib.request import Request

from oi_eegqc.desktop_update import (
    INSTALLER_ASSET,
    MIRROR_ORIGIN,
    MIRROR_RELEASE_URL,
    ZIP_ASSET,
    check_update,
    downloadable,
    interpret_release,
    pick_asset,
    version_tuple,
)


def test_version_tuple_strips_prefix_and_pads():
    assert version_tuple("v0.3.0") == (0, 3, 0)
    assert version_tuple("0.4") == (0, 4, 0)
    assert version_tuple("1.2.3-rc.1") == (1, 2, 3)


def test_pick_asset_prefers_installer():
    assets = [
        {"name": ZIP_ASSET, "browser_download_url": "https://example.test/zip"},
        {"name": INSTALLER_ASSET, "browser_download_url": "https://example.test/setup"},
    ]
    picked = pick_asset(assets)
    assert picked["name"] == INSTALLER_ASSET
    assert pick_asset([{"name": ZIP_ASSET, "browser_download_url": "https://example.test/zip"}])["name"] == ZIP_ASSET
    assert pick_asset([]) is None


def test_interpret_release_available_and_current():
    payload = {
        "tag_name": "v0.4.0",
        "html_url": "https://github.com/Omni-Intel/oi-eegqc/releases/tag/v0.4.0",
        "assets": [{"name": INSTALLER_ASSET, "browser_download_url": "https://example.test/setup"}],
    }
    newer = interpret_release(payload, "0.3.0")
    assert newer.status == "available"
    assert newer.latest == "0.4.0"
    assert newer.asset_url.endswith("/setup")
    same = interpret_release({**payload, "tag_name": "v0.3.0"}, "0.3.0")
    assert same.status == "current"
    ahead = interpret_release({**payload, "tag_name": "v0.2.0"}, "0.3.0")
    assert ahead.status == "current"


def test_check_update_uses_opener_and_maps_errors():
    payload = {
        "tag_name": "v0.5.0",
        "html_url": "https://github.com/Omni-Intel/oi-eegqc/releases/tag/v0.5.0",
        "assets": [{"name": ZIP_ASSET, "browser_download_url": "https://example.test/zip"}],
    }

    def opener(request, timeout=0):
        assert isinstance(request, Request)
        assert timeout == 8
        assert "api.github.com" in request.full_url
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    info = check_update(
        "0.3.0",
        url="https://api.github.com/repos/Omni-Intel/oi-eegqc/releases/latest",
        opener=opener,
    )
    assert info.status == "available"
    assert info.asset_name == ZIP_ASSET

    def boom(request, timeout=0):
        raise URLError("offline")

    failed = check_update("0.3.0", opener=boom)
    assert failed.status == "error"
    assert failed.current == "0.3.0"

    requested = []
    def mirror(request, timeout=0):
        requested.append(request.full_url)
        return io.BytesIO(json.dumps(payload).encode("utf-8"))
    check_update("0.3.0", opener=mirror)
    assert requested == [MIRROR_RELEASE_URL]


def installer_info(data=b"installer"):
    from oi_eegqc.desktop_update import UpdateInfo, GITHUB_REPO
    return UpdateInfo("available", "0.3.4", "0.3.5",
                      asset_url=f"https://github.com/{GITHUB_REPO}/releases/download/v0.3.5/{INSTALLER_ASSET}",
                      asset_name=INSTALLER_ASSET, size=len(data),
                      digest="sha256:" + hashlib.sha256(data).hexdigest())


def test_download_verifies_size_hash_and_no_credentials(tmp_path, monkeypatch):
    from oi_eegqc.desktop_update import download_installer
    monkeypatch.setenv("GITHUB_TOKEN", "not-for-downloads")
    progress = []
    def opener(request, timeout):
        assert request.get_header("Authorization") is None
        return io.BytesIO(b"installer")
    path = download_installer(installer_info(), tmp_path, progress.append, lambda: False, opener=opener)
    assert path.read_bytes() == b"installer"
    assert progress[-1] == 100


@pytest.mark.parametrize("data", [b"short", b"installer-extra", b"tampered!"])
def test_download_rejects_corrupt_or_incomplete(tmp_path, data):
    from oi_eegqc.desktop_update import download_installer
    with pytest.raises(ValueError):
        download_installer(installer_info(), tmp_path, lambda n: None, lambda: False,
                           opener=lambda *a, **k: io.BytesIO(data))
    assert list(tmp_path.iterdir()) == []


def test_download_cancel_and_untrusted_source(tmp_path):
    from oi_eegqc.desktop_update import download_installer, DownloadCancelled, downloadable
    info = installer_info()
    assert not downloadable(replace(info, digest=""))
    assert not downloadable(replace(info, asset_url=info.asset_url.replace("github.com", "evil.test")))
    assert not downloadable(replace(info, asset_name=ZIP_ASSET))
    with pytest.raises(DownloadCancelled):
        download_installer(info, tmp_path, lambda n: None, lambda: True,
                           opener=lambda *a, **k: io.BytesIO(b"installer"))
    assert list(tmp_path.iterdir()) == []


def test_mirrored_installer_is_downloadable():
    info = installer_info()
    mirrored = replace(
        info,
        asset_url=f"{MIRROR_ORIGIN}/oi-eegqc/releases/v{info.latest}/{INSTALLER_ASSET}",
    )
    assert downloadable(mirrored)


def test_mirror_never_receives_github_token(monkeypatch):
    from oi_eegqc.desktop_update import fetch_latest_release
    monkeypatch.setenv("GITHUB_TOKEN", "github-only")
    def opener(request, timeout):
        assert request.get_header("Authorization") is None
        return io.BytesIO(b'{}')
    fetch_latest_release(MIRROR_RELEASE_URL, opener=opener)


def test_launch_rechecks_and_never_forces_close(tmp_path, monkeypatch):
    import sys
    import subprocess
    from oi_eegqc.desktop_update import launch_installer
    path = tmp_path / INSTALLER_ASSET
    path.write_bytes(b"installer")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: calls.append(args))
    launch_installer(path, installer_info())
    assert "/UPDATE=1" in calls[0] and "/NORESTART" in calls[0]
    assert "/NOFORCECLOSEAPPLICATIONS" in calls[0]
    path.write_bytes(b"tampered!")
    with pytest.raises(ValueError):
        launch_installer(path, installer_info())
    assert len(calls) == 1
