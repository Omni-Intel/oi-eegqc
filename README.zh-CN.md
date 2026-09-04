<h1 align="center">oi-eegqc</h1>

<p align="center">
  <strong>简体中文</strong> · <a href="README.md">English</a>
</p>

<p align="center">
  <strong>面向干净商业采集的自适应脑电质量控制。</strong><br>
  给多时长、多通道配置的片段做入库评级 —— 不做任务表现评估。
</p>

<p align="center">
  <img alt="python" src="https://img.shields.io/badge/python-3.9%2B-111111?style=flat-square">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-111111?style=flat-square">
  <img alt="qa/qc" src="https://img.shields.io/badge/QA%2FQC-separated-FF5A01?style=flat-square">
  <img alt="grades" src="https://img.shields.io/badge/grades-A–D%20·%20GQI%20·%20Availability-555555?style=flat-square">
</p>

<p align="center">
  <a href="#快速开始"><strong>安装</strong></a> ·
  <a href="#它能做什么">能力</a> ·
  <a href="#设计原则">原则</a> ·
  <a href="#三级评级">评级</a> ·
  <a href="#配置">配置</a>
</p>

<p align="center">
  <img src="assets/oi-eegqc-hero.png" alt="Omni-Intelligence 标识与 oi-eegqc 字标" width="100%">
</p>

`oi-eegqc` 是音视频观看脑电的**入库验收 bench**。默认假设进来的数据已经协议干净：montage 明确、采样率明确、片段边界明确、事件流可信。

它只回答一个问题：

> **这条记录能不能作为数据收下？**

它**不**回答被试有没有做出 ASSR、N-back、caption 解码，或任何下游模型指标。任务越难，科学结果越容易随机；这种方差**绝不能**写成质量惩罚。

## 快速开始

```bash
pip install -e ".[dev]"
oi-eegqc demo --channels 32 --duration 12
```

评一条连续片段：

```bash
oi-eegqc eval-npy \
  -i clip.npy \
  --sfreq 250 \
  --ch-names ch_names.npy \
  -o report.json
```

批量目录：

```bash
oi-eegqc eval-dir -i ./clips --sfreq 250 -o batch_report.json
```

Python API：

```python
from oi_eegqc import RecordingInput, evaluate_recording
import numpy as np

data = np.load("clip.npy")  # (n_channels, n_times)
report = evaluate_recording(
    RecordingInput(
        data=data,
        sfreq=250.0,
        ch_names=[f"E{i}" for i in range(data.shape[0])],
        clip_id="vid_001",
        stimulus_duration_s=18.0,
        event_ok=True,
        sync_error_ms=8.0,
    )
)
print(report.letter_grade, report.gqi, report.availability)
```

## 它能做什么

| 工作流 | 结果 |
| --- | --- |
| 时长自适应 QA | 为约 5–60s+ 片段选择窗长 / hop / 可用率门槛 |
| 通道自适应 QA | 为 8 导 → 128 导+ 调整相关与坏道容忍 |
| 窗级信号 QA | 常数 / 高幅 / NSR / 低相关 → **ODQ%** |
| 字母评级 | WeBrain 式 **A / B / C / D**，服务结算与重采 |
| 可分解 GQI | **0–100**，惩罚分解：接触 · 洁净 · 可用时长 · 完整性 · 刺激同步 |
| 可用性旗标 | HBN 式 **Available / Caution / Unavailable** |
| 阈值版本化 | 每条分数携带 `threshold_version`，可审计 |

## 设计原则

- **质控 ≠ 任务评估。** 入库分只看信号与采集完整性；科学结论与模型指标另开轨道。
- **假设数据纯粹。** 事件、montage、单位、片段边界属于协议，而不是事后考古。
- **按时长适配，不写死一个窗。** 6 秒与 60 秒不能共用同一套统计。
- **按通道密度适配，不写死一个阈值。** 低密度阵列不能照搬高密度相关门槛。
- **先 QA 后 QC。** 连续指标在前，字母 / GQI / 可用性是其上的决策层。
- **一份权威报告体。** `report.to_dict()` 是机器契约；可视化是派生视图。
- **绝不给认知打分。** 频带比、「专注」「投入」、难度相关 ERP 不进验收主分。

## 三级评级

| 轨道 | 刻度 | 用途 |
| --- | --- | --- |
| Letter | A / B / C / D | 结算、重采、放行 |
| GQI | 0–100 + 惩罚分解 | 排序、看板、连续监控 |
| Availability | Available / Caution / Unavailable | 数据集过滤与目录旗标 |

商业读取建议：

| 等级 | 含义 | 典型动作 |
| --- | --- | --- |
| **A** | 足够干净 | 主训练 / 对外交付 |
| **B** | 良好，轻微缺陷 | 保留；可轻度清洗 |
| **C** | 边缘 | 降权或人工复核 |
| **D** | 差 | 拒收 / 重采 |

## 管线（v0.1）

1. 去掉辅助导 / 平坦导  
2. 高通（>1 Hz）  
3. 选择**时长 profile** + **montage profile**  
4. 窗级 QA → ODQ  
5. 时长自适应可用率地板 → 字母降级  
6. 惩罚式 GQI（接触 · 洁净 · 可用时长 · 完整性 · 刺激同步）  
7. 可用性旗标  

## 配置

```bash
oi-eegqc init-config -o my_qc.yaml
```

也可直接改 [`configs/default.yaml`](configs/default.yaml)。阈值变更时务必上调 `threshold_version`，保证历史分数可比。

## 目录结构

```text
.
├── assets/                 # hero 与字标
├── configs/default.yaml    # 时长与 montage 配置
├── examples/
├── src/oi_eegqc/
│   ├── adapters.py
│   ├── config.py
│   ├── qa/windows.py
│   ├── scoring/grades.py
│   ├── pipeline.py
│   └── cli.py
└── tests/
```

## 要求

- Python 3.9+
- `numpy`、`scipy`、`pyyaml`
- 可选：`mne`（后续 EDF/BDF：`pip install -e ".[mne]"`）

## 参考

- [PREP](https://doi.org/10.3389/fninf.2015.00016)  
- [Autoreject](https://doi.org/10.1016/j.neuroimage.2017.06.030)  
- [WeBrain EEG QA](https://doi.org/10.1088/1361-6579/ac890d)  
- [HBCD EEG QC](https://doi.org/10.1016/j.dcn.2024.101447)  
- HBN-EEG availability · MEEGqc GQI · IFCN/ILAE 阻抗指引  

## 状态

面向 OI 音视频脑电入库的工程 bench。上线结算前请用自有设备与试点批次标定阈值。

## 许可证

[MIT](LICENSE)
