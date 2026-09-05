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
  <a href="#机器协议">机器协议</a> ·
  <a href="docs/windows-app.zh-CN.md">Windows 壳层</a> ·
  <a href="#阈值标定">标定</a> ·
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
  --unit uV \
  --ch-names ch_names.npy \
  -o report.json
```

批量目录：

```bash
oi-eegqc eval-dir -i ./clips --sfreq 250 --unit uV -o batch_report.json
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
        unit="uV",                  # 或 "V" / "mV" / "adc" 配 adc_to_uv
        clip_id="vid_001",
        expected_n_channels=64,
        stimulus_duration_s=18.0,
        event_ok=True,
        sync_error_ms=8.0,
    )
)
print(report.letter_grade, report.gqi, report.availability)
```

注册过的数据集适配器一律产出 `RecordingInput`，它们自己不评分：

```bash
oi-eegqc datasets
oi-eegqc eval-bdf -i recording.bdf --unit V -o report.json   # 需要 oi-eegqc[mne]
oi-eegqc bench hw --root ./sessions -o hw.json
oi-eegqc bench nod --root ./epochs_uV --subjects sub-01 sub-02
oi-eegqc bench synthetic --channels 32 --duration 20
```

```python
from oi_eegqc import load_npy, load_edf_bdf, open_dataset, score_adapter

rec = load_npy("clip.npy", sfreq=250.0, unit="uV")
adapter = open_dataset("hw", "./sessions")          # 或 "nod" / "npy" / "synthetic"
rows, summary = score_adapter(adapter)              # 数据集字段只在 extras
```

机器可读 stdout（脚本或桌面 sidecar）：

```bash
oi-eegqc --json datasets
oi-eegqc --ndjson bench synthetic --channels 32 --duration 12
oi-eegqc serve --stdio
```

| 适配器 | 输入 | 说明 |
| --- | --- | --- |
| `npy` | 二维 `.npy` 片段目录 | 必须给 `--sfreq` 与 `--unit` |
| `hw` | 会话目录（`session.json` + BDF） | Neuracle 伏特 / TD10 ADC 计数 |
| `nod` | `{subject}_epochs_uV.npy` | 物理 µV；可作为 QC 参照 |
| `things` | THINGS-EEG2 预处理数组 | 无量纲；不可比，需显式开启 |
| `synthetic` | 内存合成 | 干净 / 噪声 / 死导 / 饱和 |

### 单位是契约的一部分

`unit` 不是装饰。饱和与死通道门禁比对的是物理微伏阈值，单位声明错了会**静默地**让这些门禁失效。用 MNE 读 EDF/BDF 时传 `"V"`（MNE 会把微伏表头换算成 SI 伏特），原始头戴计数传 `"adc"` 并给出 `adc_to_uv`。未知单位直接报错，不做默认猜测。

已做噪声归一化或白化的数据（例如公开的 THINGS-EEG2，其数值标准差约为 1）没有物理尺度，绝对幅度门禁对它根本不适用。

## 它能做什么

| 工作流 | 结果 |
| --- | --- |
| 时长自适应 QA | 为约 5–60s+ 片段选择窗长 / hop / ODQ 档位线 |
| 通道自适应 QA | 为 4 导 → 128 导+ 调整相关、坏道与幅度容忍 |
| 绝对幅度门禁 | 以微伏判定轨道削波、饱和、死导 / 脱落导 |
| 相对离群检测 | 通道自身时间维 + 跨通道空间维 robust-z |
| 频谱 QA | 宽带高频噪信比与工频干扰，并保留连续量 |
| 空间耦合 | Top-3 邻道相关；导联过于稀疏时自动关闭 |
| 字母评级 | WeBrain 式 **A / B / C / D**，作用于可用录制时长 |
| 可分解 GQI | **0–100**，维度：接触 · 洁净 · 可用时长 · 完整性 · 刺激同步 |
| 硬性否决门 | 事件损坏、放大器打满、导联大面积缺失 → 直接拒收 |
| 可用性旗标 | HBN 式 **Available / Caution / Unavailable**，由字母派生 |
| 阈值版本化 | 每条分数携带 `threshold_version`，可审计 |

### 两个不能混为一谈的质量数

`clean_ratio` 与 `usable_ratio` 回答的是不同问题，分开上报：

- **`clean_ratio`** 是**通道×窗格子**上的污染**密度**：录到的面有多少被污染。
- **`usable_ratio`**（×100 = **ODQ**）是**时间**指标：坏道占比不超过 `max_bad_ch_frac_per_window` 的窗所占比例，即有多少秒还能用。这与 WeBrain 定义 A/B/C/D 档位线时所用的量一致。

每个窗都有 10% 坏道 → `clean_ratio` 0.90 而 ODQ 100；10% 的窗整段报废 → `clean_ratio` 0.90 而 ODQ 90。把两者合成一个数，会让它在 GQI 的两个权重里被重复计算。

## 设计原则

- **质控 ≠ 任务评估。** 入库分只看信号与采集完整性；科学结论与模型指标另开轨道。
- **假设数据纯粹。** 事件、montage、单位、片段边界属于协议，而不是事后考古。
- **按时长适配，不写死一个窗。** 6 秒与 60 秒不能共用同一套统计。
- **按通道密度适配，不写死一个阈值。** 低密度阵列不能照搬高密度相关门槛。
- **先 QA 后 QC。** 连续指标在前，字母 / GQI / 可用性是其上的决策层。
- **一份权威报告体。** `report.to_dict()` 是机器契约；可视化是派生视图。
- **绝不给认知打分。** 频带比、「专注」「投入」、难度相关 ERP 不进验收主分。
- **绝不洗白分母。** 死导、平坦导留在导联里并扣分。悄悄剔掉它们，会让四分之一电极脱落的记录报出满分。
- **没测的维度不白送分。** GQI 只在**实际有输入**的维度上做加权平均，其余权重按比例重分配。不提供同步元数据，就拿不到同步分。
- **用注入式故障标定，不要用顺手的数据集标定。** 把阈值降到真实数据能过为止是循环论证，并且会毁掉该阈值本该具备的检出能力。

## 三级评级

| 轨道 | 刻度 | 用途 |
| --- | --- | --- |
| Letter | A / B / C / D | **权威依据。** 结算、重采、放行 |
| GQI | 0–100 + 维度分解 | 排序、看板、连续监控 |
| Availability | Available / Caution / Unavailable | 数据集过滤与目录旗标 |

字母等级是结算依据，可用性旗标由它派生，因此两者不会互相矛盾：**D 一律为 Unavailable**，硬性否决同时置为两者。GQI 永不覆盖字母，它只在同一档内做排序。

商业读取建议：

| 等级 | 含义 | 典型动作 |
| --- | --- | --- |
| **A** | 足够干净 | 主训练 / 对外交付 |
| **B** | 良好，轻微缺陷 | 保留；可轻度清洗 |
| **C** | 边缘 | 降权或人工复核 |
| **D** | 差 | 拒收 / 重采 |

字母等级按设计是**阶梯式**跳变的，因为它是档位决策。GQI 才是连续轨道：某种退化一次性把所有窗都推过坏道预算时，字母会陡降，而 GQI 因为混合了标记密度与连续频谱量，仍然平滑下降。

## 机器协议

人读 CLI 给终端用。Windows 上的 Electron 应用不要去刮它的 stdout。
用 `--json` / `--ndjson`，或把 `oi-eegqc serve --stdio` 拉起当 sidecar，在
stdin/stdout 上走 NDJSON。

两套版本号刻意分开：

| 字段 | 示例 | 何时改 |
| --- | --- | --- |
| 信封上的 `schema_version` | `oi-eegqc-protocol-v1` | 信封键（`ok` / `event` / `kind`） |
| 报告体上的 `schema_version` | `oi-eegqc-report-v1` | `QualityReport.to_dict()` 的字段 |
| `threshold_version` | `oi-eegqc-v0.2.0` | 评分阈值（与线协议正交） |

机器模式下 stdout **只有 JSON**。警告和人读进度走 stderr。
`--json` / `--ndjson` 必须显式给 `--root`，不会悄悄用工作站默认路径。

```bash
oi-eegqc --json datasets
oi-eegqc --ndjson bench synthetic --channels 32 --duration 12
```

Sidecar 操作：`ping`、`list_datasets`、`score_file`、`score_dataset`、`cancel`、
`shutdown`。`cancel` 在两条记录**之间**打断当前批次；正在跑的
`evaluate_recording` 仍会跑完，已完成的行保留，并带 `cancelled: true`。
错误形状是 `{ok:false, code, message}`，前端按 `code` 分支。数据集溯源只写在
`report.extras`，不再平铺到报告体。

`score_adapter(..., on_progress=..., cancel=...)` 与 sidecar 是同一套契约。
桌面壳层应调这些 Python 入口，而不是解析人读 CLI。

Windows 入库界面是原生薄壳，不是 Electron —— 见
[docs/windows-app.zh-CN.md](docs/windows-app.zh-CN.md)。

## 管线（v0.2）

1. 只去掉辅助导 —— 平坦导与死导留在分母里
2. 按声明的 `unit` 换算到微伏
3. 在**未滤波**、去 DC 后的信号上检测轨道削波
4. 零相位 Butterworth 高通（>1 Hz）
5. 选择**时长 profile** + **montage profile**
6. 窗级 QA → `clean_ratio`（格子密度）与 `usable_ratio`/ODQ（存活时长）
7. 硬性否决门：事件损坏、放大器打满、导联缺失
8. 由 ODQ 定字母，再由坏道占比上限封顶
9. GQI 只在已评估维度上做归一化加权
10. 可用性旗标由字母派生

## 阈值标定

阈值来自**已知严重度的注入式故障**，不是调到某个数据集能过为止：

```bash
python examples/calibrate_thresholds.py
```

脚本在七种退化模型上扫描严重度网格，检查三个性质——单调下降（Spearman ρ ≤ −0.9）、响应是渐变而非台阶、以及在预期失败的严重度上确实检出。任一项不通过即以非零码退出，因此它应当作为上调 `threshold_version` 前的 CI 关卡。

| 场景 | 要求性质 | 依据 |
| --- | --- | --- |
| 宽带噪声 | 渐变 | 传感器与电磁噪声连续变化 |
| 死通道 | 渐变 | 电极是一个一个脱落的 |
| 脱落通道 | 渐变 | 幅度正常但无共享信号，只有耦合检测器能发现 |
| 运动伪迹爆发 | 渐变 | 被伪迹占用的时长连续变化 |
| 工频干扰 | 渐变 | 幅度连续；轻度工频可 notch 去除，保留 A |
| 饱和 | 二值拒收 | 放大器要么打满要么不打满，强行要求渐变是编造 |
| 慢漂移 | 不应影响 | 1 Hz 高通必须吸收它，用于防止滤波器退化 |

其中两个阈值特别说明，都由**实测分离度**而非直觉确定：

- **空间耦合。** 脱落电极在 top-3 |corr| 统计上落在 0.09–0.17，完好记录则位于 0.69（NOD-EEG 第 5 百分位）与 0.71（Neuracle）。0.40 的阈值取在这段间隙中。但在 4 通道头戴上，**完好**通道只有 0.13–0.31，与脱落区间完全重叠，检测器在此没有判别力，因此对 `low_density` 直接关闭，而不是硬给一个阈值。
- **工频。** 只有当工频功率可与整个 1–45 Hz 频带相比时才标记格子。更轻的干扰只降低洁净度评分，不取消验收资格。

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
├── docs/windows-app.zh-CN.md  # Windows 原生 QC 薄壳
├── examples/
│   ├── sidecar_session.py            # stdio sidecar 客户端（Windows 应对齐这份）
│   ├── calibrate_thresholds.py       # 注入式故障阈值标定
│   ├── run_hw_bdf_bench.py           # Neuracle / TD10 BDF 会话
│   └── run_public_dataset_bench.py   # NOD-EEG（THINGS 需显式开启）
├── src/oi_eegqc/
│   ├── io/                 # npy / EDF / BDF / 切段 / 报告
│   ├── datasets/           # npy、hw、nod、things、synthetic 适配器
│   ├── protocol.py         # 信封与结构化错误
│   ├── serve.py            # NDJSON stdio sidecar
│   ├── adapters.py         # 通道选择、削波、高通、分窗
│   ├── config.py           # 自适应 profile 与阈值
│   ├── qa/windows.py       # 窗级检测器 → clean_ratio + ODQ
│   ├── scoring/grades.py   # 字母 / GQI / 可用性 / 硬性否决
│   ├── pipeline.py         # evaluate_recording
│   └── cli.py              # oi-eegqc 入口
└── tests/
```

## 要求

- Python 3.9+
- `numpy`、`scipy`、`pyyaml`
- 可选：`mne`（EDF/BDF：`pip install -e ".[mne]"`）

## 参考

- [PREP](https://doi.org/10.3389/fninf.2015.00016)  
- [Autoreject](https://doi.org/10.1016/j.neuroimage.2017.06.030)  
- [WeBrain EEG QA](https://doi.org/10.1088/1361-6579/ac890d)  
- [HBCD EEG QC](https://doi.org/10.1016/j.dcn.2024.101447)  
- HBN-EEG availability · MEEGqc GQI · IFCN/ILAE 阻抗指引  

## 状态

面向 OI 音视频脑电入库的工程 bench。上线结算前请用自有设备与试点批次标定阈值——任何改动后都应重跑 `examples/calibrate_thresholds.py`。

### v0.2 — 评分重构

`threshold_version` 升至 `oi-eegqc-v0.2.0`，v0.1 的分数**不可**与之比较。本次修复：

- `usable_ratio` 在代数上与 `ODQ/100` 完全相同，导致同一个量通过两个权重承担了 GQI 的 60%。现在 ODQ 回归 WeBrain 式的存活窗占比，`clean_ratio` 独立表示格子密度。
- 所有指标都是尺度无关的，0.05 µV 与 53000 µV 同样拿 A。现在单位必须声明，绝对幅度门禁生效。
- 零方差通道在评分前被丢弃，32 导中 8 导死亡仍报 A 且原因列表为空。现在它们留在分母里并被扣分。
- 幅度离群用的是跨通道 MAD，在 50% 污染时崩溃，半个导联被衰减仍得满分 ODQ。现在改为通道自身时间维检测，跨通道检验仅保留高侧且要求至少 8 导。
- 不可用数据的 GQI 卡在 26/100，因为未测维度白送了权重。现在权重在已评估维度间重分配，GQI 可达 0。
- 所有 D 级片段都报 `Caution`。现在可用性由字母派生，D 一律 `Unavailable`。
- 信号带 `(1, 50)` 与噪声带 `(50, 100)` 都包含工频，同一份功率被当作信号又当作噪声。现在改为 `(1, 45)` 与 `(55, 95)`，并配独立的工频检测器。
- 时长与同步完整性从未被真正考核：bench 把每个片段自身的长度当作刺激时长传回，并硬编码一个合格的同步误差。现在华为 bench 改用采样数与墙钟时间互校，未标定的同步则记为未评估。

### v0.3 — 机器协议

包版本 `0.3.0`。评分与 `threshold_version` 不变（仍为 `oi-eegqc-v0.2.0`）。
本版是给 Electron 用的接口层：

- 协议信封（`oi-eegqc-protocol-v1`）与报告体（`oi-eegqc-report-v1`）分开。
- `--json` / `--ndjson` / `--quiet`；机器模式下人读文字走 stderr。
- `oi-eegqc serve --stdio`，批次可取消。
- 数据集字段只留在 `extras`，不再平铺到报告。

## 许可证

[MIT](LICENSE)
