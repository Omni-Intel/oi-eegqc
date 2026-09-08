import io
import json
from urllib.error import URLError
from urllib.request import Request

from oi_eegqc.desktop_update import (
    INSTALLER_ASSET,
    ZIP_ASSET,
    check_update,
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
