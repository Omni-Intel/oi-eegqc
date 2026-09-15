"""Evidence-based presentation; no signal processing or inferred probabilities."""
from collections import Counter, defaultdict


CAUSES = {
    "missing": ("缺失或非有限样本", "核对采集丢包、文件导出和数据处理记录；不能据此认定电极脱落或数值恒定。"),
    "constant": ("数值恒定", "核对原始数值、数据输出与电极连接；恒定值不能单独证明电极脱落。"),
    "flat": ("幅度过低", "核对单位、增益和电极接触；低幅信号也可能来自参考或采集配置。"),
    "extreme": ("幅度越界", "核对单位、原始波形与现场日志；运动、接触变化或量程设置均需排查。"),
    "temporal_outlier": ("时间方向离群", "检查该段突变和采集日志；统计离群不能单独确定运动。"),
    "spatial_outlier": ("通道间幅度离群", "对照相邻导联和参考设置；局部差异不等同于坏导。"),
    "high_nsr": ("高频噪声比例偏高", "对照频谱与现场记录，排查肌肉活动或电气噪声。"),
    "line": ("工频干扰", "核对电网频率、参考/接地连接及附近电源；按设备规范操作。"),
    "low_corr": ("通道相关性偏低", "检查参考、通道映射和接触；真实空间差异也会降低相关性。"),
    "clipped": ("疑似削顶平台（仅提示）", "查看原始波形、量化精度和设备量程；平台也可能来自取整或软件处理，本项不单独扣分、判窗失败或总分归零。"),
}


def _coverage(windows):
    intervals = []
    for w in sorted(windows, key=lambda w: w["start_s"]):
        start, end = float(w["start_s"]), float(w["end_s"])
        if intervals and start <= intervals[-1][1]:
            intervals[-1][1] = max(end, intervals[-1][1])
        else:
            intervals.append([start, end])
    return sum(b - a for a, b in intervals)


def build_details(report):
    extras = report.extras or {}
    qa = report.window_qa
    windows = list(getattr(qa, "window_evidence", []) or [])
    names = extras.get("channel_names") or []
    cfg = extras.get("scoring_config") or {}
    profile = next((p for p in cfg.get("montage_profiles", []) if p.get("name") == getattr(report, "montage_profile", None)), {})
    def label(i):
        return str(names[i]) if i < len(names) else f"第 {i + 1} 路"
    total = len(windows)
    failed = [w for w in windows if not w["usable"]]
    affected = [w for w in windows if w["causes"]]
    sections = []
    summary = [f"记录 {report.duration_s:.3f} 秒；参与评分 {report.n_channels_used} 路；GQI {report.gqi:.1f}/100。"]
    if total:
        summary.append(f"通过 {total - len(failed)}/{total} 窗（{100 * (total - len(failed)) / total:.1f}%）；未通过 {len(failed)}/{total} 窗（{100 * len(failed) / total:.1f}%）。")
        summary.append(f"存在任一异常的窗口 {len(affected)}/{total}（{100 * len(affected) / total:.1f}%）；允许少量异常导联的窗口仍可能通过。")
        summary.append(f"未通过窗口覆盖区间去重后为 {_coverage(failed):.3f} 秒；这是需复核区间的覆盖长度，不是精确伪迹持续时间。")
        if not affected:
            summary.append("当前窗口规则未检出异常。未检出不代表所有类型伪迹都已排除。")
        elif not failed:
            summary.append("存在局部通道异常，但每窗均在允许的异常路数范围内。")
        elif len(failed) == total:
            summary.append("可用窗口为 0 表示每个分析窗口均未通过；不能据此认定被试全程运动或全程阻抗过高。")
    else:
        summary.append("此报告没有逐窗证据，无法定位异常秒数或计算逐窗占比；请重新评分以生成详情。")
    rule = extras.get("usable_window_rule") or {}
    if rule:
        summary.append(f"窗口 {rule.get('window_s', '未知')} 秒，步长 {rule.get('hop_s', '未知')} 秒；每窗最多允许 {rule.get('max_bad_channels', '未知')} 路异常。")
    sections.append({"title": "本次结论与统计口径", "lines": summary})
    causes = defaultdict(list)
    channel_counts = Counter()
    channel_causes = defaultdict(Counter)
    timeline = []
    for w in windows:
        for i in set(w["bad_channels"]):
            channel_counts[i] += 1
        parts = []
        for cause, channels in w["causes"].items():
            causes[cause].append(w)
            title = CAUSES.get(cause, (cause, "此检测项暂无解释，请结合原始报告复核。"))[0]
            parts.append(title + "：" + "、".join(label(i) for i in channels))
            for i in set(channels):
                if cause != "clipped":
                    channel_causes[i][title] += 1
        if w["causes"] or not w["usable"]:
            plateaus = w.get("clipping_plateau_ratio") or {}
            if plateaus:
                parts.append("平台样本占该窗该路有效样本：" + "、".join(f"{label(int(i))} {100 * value:.1f}%" for i, value in plateaus.items()))
            for i, values in (w.get("measurements") or {}).items():
                metrics = [f"高通后峰峰值 {values['ptp_uv']:.3f} µV"]
                if values.get("missing_samples"):
                    n, count = values["sample_count"], values["missing_samples"]
                    metrics.append(f"缺失 {count}/{n} 样本（{100 * count / n:.2f}%）")
                for key, title in (("nsr", "高频/信号功率比"), ("line_ratio", "工频/信号功率比"), ("spatial_z", "空间离群 z"), ("temporal_z", "时间离群 |z|"), ("correlation", "最强邻路相关性")):
                    if values.get(key) is not None:
                        metrics.append(f"{title} {values[key]:.4f}")
                for cause, key, threshold, direction in (("extreme", "ptp_uv", "ptp_max_uv", 1), ("flat", "ptp_uv", "ptp_min_uv", -1), ("high_nsr", "nsr", "nsr_threshold", 1), ("line", "line_ratio", "line_ratio_threshold", 1), ("spatial_outlier", "spatial_z", "amp_z", 1), ("temporal_outlier", "temporal_z", "amp_z", 1), ("low_corr", "correlation", "corr_threshold", -1)):
                    if int(i) in w['causes'].get(cause, []) and values.get(key) is not None and threshold in profile:
                        delta = direction * (values[key] - profile[threshold])
                        metrics.append(f"{CAUSES[cause][0]}：{'高于' if direction == 1 else '低于'}阈值 {profile[threshold]:g}，差值 {delta:.4g}（同实测单位）")
                parts.append(label(int(i)) + " 实测：" + "；".join(metrics))
            timeline.append({"title": f"{w['start_s']:.3f}–{w['end_s']:.3f} 秒 · {'通过（含异常）' if w['usable'] else '未通过'} · {len(w['bad_channels'])}/{report.n_channels_used} 路异常",
                             "text": "\n".join(parts) or "该窗口未通过，未保存具体检测原因。"})
    lines = []
    for cause, selected in sorted(causes.items(), key=lambda pair: -len(pair[1])):
        title, action = CAUSES.get(cause, (cause, "请结合原始报告复核。"))
        cells = sum(len(set(w["causes"][cause])) for w in selected)
        lines.append(f"{title}：{len(selected)}/{total} 窗（{100 * len(selected) / total:.1f}%）；{cells} 个通道×窗口单元；窗口区间去重覆盖 {_coverage(selected):.3f} 秒。\n复核建议：{action}")
    if lines:
        sections.append({"title": "异常类型与复核建议（不同类型可重叠，百分比不可相加）", "lines": lines})
    if channel_counts:
        sections.append({"title": "逐导联统计（分母为该记录全部分析窗口）", "lines": [
            f"{label(i)}：异常 {count}/{total} 窗（{100 * count / total:.1f}%）；" + "；".join(f"{kind} {n} 窗" for kind, n in channel_causes[i].items())
            for i, count in sorted(channel_counts.items(), key=lambda pair: (-pair[1], pair[0]))]})
    unknowns = ["本页百分比是检测结果占比，不是病因概率或置信度。当前算法没有经设备/人群校准的概率模型，不提供虚构的置信区间。",
                "异常定位精度受窗口长度与步长限制；窗口起止不等于伪迹精确起止，运动次数不能从异常窗口数推算。",
                "阻抗、运动、电极脱落等原因需结合设备测量、现场记录和原始波形确认。"]
    assessed = extras.get("assessed_dimensions") or []
    for key, title in (("integrity", "完整性"), ("stimulus_sync", "刺激同步")):
        if key not in assessed:
            unknowns.append(f"{title}：未评估或没有足够输入证据，不能按正常解释。")
    coverage = extras.get("frequency_coverage") or {}
    for key, title in (("signal_band_complete", "信号频段"), ("noise_band_complete", "高频噪声频段"), ("line_measurable", "工频")):
        if coverage.get(key) is False:
            unknowns.append(f"{title}检测受采样率限制，相关异常可能漏检。")
    if not names:
        unknowns.append("缺少通道名称，以下采用行号；实际电极位置需要核对采集映射。")
    if profile.get("corr_detector_enabled") is False:
        unknowns.append("当前导联规模未启用相关性检测；未报告相关性异常不代表已经通过该检查。")
    sections.append({"title": "不确定性与待核实事项", "lines": unknowns})
    thresholds = []
    for key, title, unit in (("ptp_min_uv", "平直峰峰值下限", "µV"), ("ptp_max_uv", "幅度峰峰值上限", "µV"), ("amp_z", "幅度离群阈值", "robust z"), ("nsr_threshold", "高频/信号功率比阈值", ""), ("line_ratio_threshold", "工频/信号功率比阈值", "")):
        if key in profile:
            thresholds.append(f"{title}：{profile[key]} {unit}。")
    if "clip_frac_threshold" in cfg:
        thresholds.append(f"疑似削顶：连续平台至少 max(3 个样本, {1000 * cfg['clip_min_plateau_s']:g} 毫秒对应样本数向上取整)，平台样本占比 > {100 * cfg['clip_frac_threshold']:g}%。")
    if thresholds:
        sections.append({"title": "本次实际判定阈值", "lines": thresholds})
    if report.hard_fail_reasons:
        lines = []
        for reason in report.hard_fail_reasons:
            if "event marker" in reason:
                lines.append("事件标记完整性未通过：核对采集事件与日志。这是配置/记录检查结果，不表示硬件故障。")
            elif "expected channels" in reason:
                lines.append("实际通道数低于声明数量所要求的下限：核对通道声明和文件导出。原始依据：" + reason)
            elif "clipping" in reason:
                lines.append("旧算法曾根据平台形态将总分归零。此结论不能确认硬件饱和，请使用新算法重新评分。")
            else:
                lines.append("质量规则未通过，请复核原始依据：" + reason)
        sections.append({"title": "总分归零的规则与复核方向", "lines": lines})
    sections.append({"title": "评估版本", "lines": [f"算法：{extras.get('algorithm_version', '未记录')}；阈值：{report.threshold_version}"]})
    return {"sections": sections, "timeline": timeline}
