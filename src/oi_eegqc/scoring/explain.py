"""Plain-language operator notes. Scoring math stays in grades.py."""
from __future__ import annotations

import re

from .grades import DIMENSIONS

_PLACEHOLDER = re.compile(r"^(ch|eeg)\d+$", re.IGNORECASE)

_DIMENSION_LABELS = {
    "contact": "接触",
    "cleanliness": "洁净",
    "usable_time": "可用时长",
    "integrity": "完整性",
    "stimulus_sync": "刺激同步",
}

_ISSUE_TEXT = {
    "zero": "{name} 全程为 0，像是没接上或这路没出数",
    "constant": "{name} 数值几乎不变，像是死导",
    "flat": "{name} 幅度过小，像是接触不良或空接",
    "clipped": "{name} 顶到量程，检查是否松脱或被试乱动",
    "extreme": "{name} 幅度过大，检查是否松脱或运动伪迹",
    "uncoupled": "{name} 和其他电极对不上，像是浮空或贴偏",
    "line": "{name} 工频干扰偏高，检查地线和附近电源",
    "noisy": "{name} 高频噪声偏高",
}

_ISSUE_ORDER = ("zero", "constant", "clipped", "flat", "extreme", "uncoupled", "line", "noisy")

_DIMENSION_HINTS = {
    "contact": "电极接触或死导",
    "cleanliness": "噪声和干扰",
    "usable_time": "能用的时间",
    "integrity": "文件是否完整",
    "stimulus_sync": "刺激是否对齐",
}

_REASON_NOTES = (
    (re.compile(r"median impedance|impedance", re.I), "开录阻抗偏高，重新打湿或压紧电极再录"),
    (re.compile(r"rail-clipped|rail clipped", re.I), "有导联顶到量程，检查是否松脱或被试大幅乱动"),
    (re.compile(r"flat/dead|dead channels", re.I), "有死导或几乎没信号的导联，先查帽位和插头"),
    (re.compile(r"bad channels", re.I), "坏导偏多，优先检查接触差的那几路"),
    (re.compile(r"HF noise|noise-to-signal|nsr", re.I), "高频噪声偏高，减少说话、咬牙、附近电器"),
    (re.compile(r"mains ratio|line", re.I), "工频干扰偏高，检查地线和旁边电源"),
    (re.compile(r"muscle-band|muscle", re.I), "肌电偏高，提醒被试少动、放松下颌"),
    (re.compile(r"usable window ratio", re.I), "能用的时间偏短，中间坏段太多"),
    (re.compile(r"event marker", re.I), "事件标记不可用，这份记录没法按刺激切段验收"),
    (re.compile(r"duration mismatch|duration drift", re.I), "录制时长和刺激对不上，核对起止日志"),
    (re.compile(r"channels missing", re.I), "声明的导联有缺失，核对采集配置"),
    (re.compile(r"sync error", re.I), "音视频和脑电对时偏差偏大"),
    (re.compile(r"only .+ of .+ expected channels", re.I), "在场导联远少于声明数量，记录不完整"),
)


def channel_label(name, row) -> str:
    text = str(name or "").strip()
    if not text or _PLACEHOLDER.match(text.replace(" ", "")):
        return f"第 {int(row) + 1} 路"
    return text


def _issue_texts(issue: dict) -> list[str]:
    kinds = [kind for kind in _ISSUE_ORDER if kind in (issue.get("kinds") or [])]
    name = channel_label(issue.get("name"), issue.get("row", 0))
    if not kinds:
        return [f"{name} 需要检查"]
    # Keep the primary line, then at most one extra so the sheet stays readable.
    lines = [_ISSUE_TEXT[kinds[0]].format(name=name)]
    if len(kinds) > 1:
        second = _ISSUE_TEXT[kinds[1]].format(name=name)
        # Drop the repeated channel name prefix on the follow-up.
        suffix = second.split("，", 1)
        if len(suffix) == 2 and second.startswith(name):
            lines.append(f"另外{suffix[1]}")
        else:
            lines.append(second)
    return lines


def _layout_line(extras: dict, n_channels: int) -> str:
    layout = str(extras.get("channel_layout") or "")
    if layout.startswith("bcigo") or "brainco" in layout:
        return f"已对上强脑 {n_channels} 导通道名"
    if layout:
        return f"已对上采集端 {n_channels} 导通道名"
    return ""


def _dimension_cards(extras: dict) -> list[dict]:
    quality = extras.get("dimension_quality") or {}
    assessed = set(extras.get("assessed_dimensions") or quality)
    cards = []
    for name in DIMENSIONS:
        label = _DIMENSION_LABELS[name]
        if name not in assessed:
            cards.append({"name": label, "score": "—", "hint": "这次没测这项"})
            continue
        value = float(quality.get(name, 0.0))
        hint = _DIMENSION_HINTS[name]
        if value >= 0.85:
            hint = "这项看起来正常"
        elif value < 0.5:
            hint = hint + "，需要看下面的说明"
        cards.append({"name": label, "score": f"{round(100.0 * value)}", "hint": hint})
    return cards


def _headline(report, issues: list[dict]) -> str:
    if getattr(report, "hard_failed", False):
        return "这份记录有硬问题，质量分数记为 0，建议重采或先修采集链路"
    gqi = float(getattr(report, "gqi", 0) or 0)
    if not issues:
        if gqi >= 90:
            return "整体看起来正常，下面是子项分数"
        if gqi >= 70:
            return "没有明显坏导，但分数一般，看一下子项"
        return "没点名单导坏导，但整体质量偏低，看子项和下面说明"
    zeros = [item for item in issues if "zero" in (item.get("kinds") or [])]
    named = "、".join(
        channel_label(item.get("name"), item.get("row", 0)) for item in (zeros or issues)[:6]
    )
    if zeros:
        return f"有 {len(zeros)} 路全程没出数：{named}"
    return f"有 {len(issues)} 路需要检查：{named}"


def _coverage_notes(extras: dict) -> list[str]:
    coverage = extras.get("frequency_coverage") or {}
    notes = []
    if coverage.get("signal_band_complete") is False:
        notes.append("采样率偏低，信号频段没验全，不能当成完整频谱都过关")
    if coverage.get("noise_band_complete") is False:
        notes.append("采样率限制了高频噪声检测，这一项能力打折")
    if coverage.get("line_measurable") is False:
        notes.append("当前采样率测不了工频干扰，工频项不可信")
    return notes


def _reason_notes(report) -> list[str]:
    seen = set()
    notes = []
    for reason in list(getattr(report, "hard_fail_reasons", None) or []) + list(
        getattr(report, "reasons", None) or []
    ):
        text = str(reason or "")
        for pattern, note in _REASON_NOTES:
            if pattern.search(text) and note not in seen:
                seen.add(note)
                notes.append(note)
                break
    return notes


def _usable_note(report) -> str | None:
    ratio = getattr(report, "usable_ratio", None)
    if ratio is None:
        return None
    ratio = float(ratio)
    if ratio >= 0.9:
        return None
    if ratio >= 0.7:
        return f"可用时间约 {ratio:.0%}，中间还有坏段"
    return f"可用时间只有约 {ratio:.0%}，大半时间质量不稳"


_DURATION_LABELS = {
    "ultra_short": "超短片段",
    "short": "短片段",
    "medium": "中等时长",
    "long": "长片段",
}

_MONTAGE_LABELS = {
    "low_density": "低密度导联",
    "mid_density": "中密度导联",
    "high_density": "高密度导联",
}


def _context_notes(report, extras: dict) -> list[str]:
    """Recording shape / source hints that help interpret the score."""
    notes = []
    dur = _DURATION_LABELS.get(str(getattr(report, "duration_profile", "") or ""))
    mont = _MONTAGE_LABELS.get(str(getattr(report, "montage_profile", "") or ""))
    if dur and mont:
        notes.append(f"按 {dur}、{mont} 的尺子评分")
    elif dur:
        notes.append(f"按 {dur} 的尺子评分")
    elif mont:
        notes.append(f"按 {mont} 的尺子评分")

    meta = extras.get("dataset") or extras.get("session_stamp") or extras.get("source")
    session_like = bool(extras.get("session_stamp") or extras.get("task_mode"))
    if session_like:
        task = extras.get("task_mode")
        stamp = extras.get("session_stamp")
        if stamp and task:
            notes.append(f"识别为会话目录 {stamp}（{task}）")
        elif stamp:
            notes.append(f"识别为会话目录 {stamp}")
        elif task:
            notes.append(f"会话任务模式：{task}")
        assessed = set(extras.get("assessed_dimensions") or [])
        if "stimulus_sync" not in assessed:
            notes.append("这次没提供刺激同步信息，同步分未计入")
    elif isinstance(meta, str) and meta:
        notes.append(f"数据来源：{meta}")

    return notes


def _action_notes(issues: list[dict], report) -> list[str]:
    kinds = {kind for item in issues for kind in (item.get("kinds") or [])}
    actions = []
    if getattr(report, "hard_failed", False):
        actions.append("先别入库：修事件/导联配置或重采后再评")
    if "zero" in kinds or "constant" in kinds or "flat" in kinds:
        actions.append("先查没出数的那几路：插头、帽子、导电膏")
    if "clipped" in kinds or "extreme" in kinds:
        actions.append("让被试少动，检查电极是否松脱")
    if "line" in kinds:
        actions.append("挪开电源线，确认地线")
    if "noisy" in kinds:
        actions.append("减少咬牙、说话和附近电器")
    if "uncoupled" in kinds:
        actions.append("检查浮空或贴偏的电极")
    # Keep at most three actionable lines.
    return actions[:3]


def build_operator(report) -> dict:
    """Chinese briefing for the GUI. Letter / availability tracks stay out."""
    extras = dict(getattr(report, "extras", None) or {})
    issues = list(extras.get("channel_issues") or [])
    n_channels = int(getattr(report, "n_channels_used", 0) or 0)

    notes: list[dict] = []
    for item in issues:
        for text in _issue_texts(item):
            notes.append({"text": text})
    for text in _coverage_notes(extras):
        notes.append({"text": text})
    for text in _reason_notes(report):
        notes.append({"text": text})
    usable = _usable_note(report)
    if usable:
        notes.append({"text": usable})
    for text in _context_notes(report, extras):
        notes.append({"text": text})
    for text in _action_notes(issues, report):
        notes.append({"text": f"建议：{text}"})

    # Deduplicate while preserving order.
    deduped, seen = [], set()
    for item in notes:
        text = item["text"]
        if text in seen:
            continue
        seen.add(text)
        deduped.append(item)
    if not deduped:
        deduped = [{"text": "没看出需要处理的导联。"}]

    return {
        "headline": _headline(report, issues),
        "layout": _layout_line(extras, n_channels),
        "dimensions": _dimension_cards(extras),
        "notes": deduped[:16],
    }
