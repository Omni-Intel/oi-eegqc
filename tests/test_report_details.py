from types import SimpleNamespace

from oi_eegqc.report_details import build_details


def report(windows):
    return SimpleNamespace(
        extras={"channel_names": ["F3", "F4"], "assessed_dimensions": []},
        window_qa=SimpleNamespace(window_evidence=windows),
        duration_s=5, n_channels_used=2, gqi=60, threshold_version="test",
        hard_fail_reasons=[],
    )


def test_overlapping_intervals_are_not_double_counted_and_causes_stay_named():
    windows = [dict(start_s=a, end_s=b, usable=False, bad_channels=[0],
                    causes={"clipped": [0], "extreme": [0]},
                    clipping_plateau_ratio={"0": .12}) for a, b in [(0, 2.5), (1.25, 3.75)]]
    result = build_details(report(windows))
    summary = " ".join(result["sections"][0]["lines"])
    assert "3.750 秒" in summary
    assert "0/2" in summary
    assert "全程运动" in summary
    assert "F3 12.0%" in result["timeline"][0]["text"]
    channel = next(s for s in result["sections"] if s["title"].startswith("逐导联"))
    assert "2/2" in channel["lines"][0]  # two causes must not double count


def test_allowed_bad_channel_remains_visible_and_all_windows_are_available():
    windows = [dict(start_s=i, end_s=i + 1, usable=True, bad_channels=[1],
                    causes={"flat": [1]}) for i in range(45)]
    result = build_details(report(windows))
    assert len(result["timeline"]) == 45
    assert "通过（含异常）" in result["timeline"][0]["title"]
    assert "F4" in result["timeline"][0]["text"]


def test_missing_evidence_does_not_become_clean_data():
    result = build_details(report([]))
    assert result["timeline"] == []
    assert "没有逐窗证据" in " ".join(result["sections"][0]["lines"])
    uncertainties = next(s for s in result["sections"] if s["title"].startswith("不确定性"))
    assert any("刺激同步：未评估" in line for line in uncertainties["lines"])
