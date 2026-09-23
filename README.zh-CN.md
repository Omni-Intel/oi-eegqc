# OI-EEGQC 统一评分与上传软件

[English](README.md) · [下载已发布 Windows 安装包](https://github.com/Omni-Intel/oi-eegqc/releases/latest/download/OI-EEGQC-Setup-Windows-x64.exe)

OI-EEGQC 是面向 EEG 采集质控与数据入库的本地优先工具。Windows 桌面端和命令行共用同一套评分引擎、输入规范化流程和版本化报告格式。一个采集文件夹可以同时包含 EEG 记录、采集元数据、阻抗截图、佩戴定位照片、视频和其他辅助文件。

AV Capture 是主试的一站式入口，EEGQC 提供可无窗口调用的逐视频质检、整轮评分和可续传上传；EEGQC 窗口保留给专家复核与批量历史处理。协议与仓库职责见 [采集集成接口](docs/capture-integration.md)。

评分用于采集质量复核和数据入库，不推断认知状态、注意力、临床诊断或伪迹的生理原因。

## 数据流

```text
采集文件夹
├── EDF / BDF ─────────────── 从文件头读取采样率、单位和通道
├── NPY + 可选 sidecar ────── 声明采样率、单位、数组方向和通道布局
└── 辅助文件 ──────────────── 图片、视频、元数据和 session 文档
            │
            ▼
输入核验并统一到物理单位
            │
            ▼
分窗信号检查 ──► 版本化 GQI 报告 + 通道/时间窗证据
            │                         │
            │                         ├── Windows GUI
            │                         ├── CLI / Python API / 标准输入输出协议
            │                         └── 本地评分缓存
            ▼
采集快照与上传清单
            │
            ▼
采集文件夹增量上传与断点续传
```

评分与上传是两个独立阶段。EEG 信号始终在本地处理；上传功能将所选采集文件夹及其中的辅助文件发送到项目配置的存储位置。

## 支持的数据

| 输入 | 采集参数来源 | 处理方式 |
| --- | --- | --- |
| EDF / BDF | 文件头 | 使用记录中的采样率、物理单位和通道名；文件头损坏时停止处理 |
| NPY | sidecar 或显式输入 | 必须确定采样率和物理单位，并确认数组方向与通道布局 |
| 采集文件夹 | 递归扫描 | 对受支持的 EEG 文件评分，普通辅助文件保留用于整夹上传 |

NPY sidecar 可以声明 `sfreq`、`unit`、`channels_first`、`channel_names` 或已知 `channel_layout`。阻抗、同步误差等采集证据仅在实际存在时参与报告，不补造缺失信息。详见[通道布局](docs/channel-layouts.zh-CN.md)和[桌面端输入说明](docs/desktop.md)。

## 质量模型

当前报告格式为 v3，评分算法为 `oi-eegqc-score-v3`，阈值版本为 `oi-eegqc-v0.6.0`。每份报告都保存实际生效的配置和算法版本。

GQI 是 0–100 的综合分数，由接触质量、信号洁净度和可用时长构成；完整性和刺激同步仅在提供对应证据时参与。报告保留每个时间窗的结论、异常通道和检测到的信号形态，便于主试追溯分数变化的原因。

“可用窗口比例”和“时长评分”是两个不同指标。前者是通过通道规则的重叠时间窗占比，后者把该比例映射到配置的目标值；可用窗口比例不是精确可用秒数。

未滤波信号窗中的局部极值平台作为待复核形态展示。量化也可能产生类似平台，因此平台本身不单独扣分，也不能证明硬件饱和。平直导联、幅度越界、工频干扰和缺失值分别由对应规则处理。详见[评分规则与边界](docs/scoring.md)。

## 桌面端流程

1. 选择或拖入采集文件夹、EDF、BDF 或 NPY 文件。
2. 只补充无法从数据或 sidecar 中读取的采集参数。
3. 在本地评分，查看 GQI、可用窗口和通道级证据。
4. 确认采集内容后上传整个文件夹。

仅当摘要能够避免后续重复读取大文件时，桌面端才计算并保存文件 SHA-256。文件内容、评分参数和算法版本均未变化时复用评分缓存；采集参数、通道选择、配置或算法版本变化时重新评分。

评分在独立进程中运行。单个文件失败或取消不会清除已完成结果；评分进程崩溃或超时后，队列仍可继续。正式桌面界面为 Qt Quick / PySide6，旧 QWidget 界面不进入发布包。

## 增量与断点续传

应用能够跨日识别同一个本地采集文件夹，并为每次上传保存可恢复的进度：

- 清单比对后跳过未变化文件；
- 新文件追加到原采集目录；
- 变化文件仅在传输成功后更新同一相对路径；
- 本地删除文件不会删除云端历史对象；
- 大文件自动使用可续传的分片传输；
- 只有全部所选文件上传成功后，才把本次采集标记为完成。

上传中断后可以继续，不会重复传输已经确认且内容未变化的文件。再次上传同一文件夹时，新增内容会追加，变化内容会更新，无关的远端文件仍然保留。

## 使用接口

### Windows 桌面端

使用[已发布安装包](https://github.com/Omni-Intel/oi-eegqc/releases/latest/download/OI-EEGQC-Setup-Windows-x64.exe)或[便携版压缩包](https://github.com/Omni-Intel/oi-eegqc/releases/latest/download/OI-EEGQC-Windows-x64.zip)。采集电脑静默安装/更新走 GitHub Releases：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-desktop.ps1
```

安装器写入当前用户目录，不要求管理员权限。设置里的「检查更新」读 GitHub Releases API。评分保持本地执行，检查更新不会上传 EEG 数据。公开 WinGet 源需要另提 PR，采集点网络差时不要把它当主安装通道。

### 命令行

```sh
python -m pip install -e '.[mne]'

oi-eegqc score -i recording.bdf -o report.json
oi-eegqc score -i session_01 --sfreq 500 --unit uV -o batch-report.json
oi-eegqc --json datasets
oi-eegqc serve --stdio
```

CLI、数据集适配器、Python API 和标准输入输出协议与桌面端调用同一数值引擎。自动化程序可使用 JSON 或 NDJSON；交互式输出用于人工查看。

## 工程结构

| 层级 | 当前模块 | 职责 |
| --- | --- | --- |
| 表现层 | `desktop.py`、`quick.py`、`qml/`、`upload_ui.py` | 原生界面、用户操作、进度和报告展示 |
| 应用任务层 | `application/`、`desktop_service.py` | 批处理、取消、缓存复用和评分子进程恢复 |
| 采集输入层 | `intake.py`、`io/`、`layouts/` | 文件发现、单位、采样率、数组方向和通道身份 |
| 数值引擎 | `pipeline.py`、`qa/`、`scoring/`、`config.py` | 时间窗证据、指标聚合和版本化报告 |
| 持久化与传输 | `score_cache.py`、`desktop_upload.py`、`upload_session.py`、`upload_http.py` | 本地缓存、采集记录和断点直传 |
| 自动化接口 | `cli.py`、`serve.py`、`datasets/`、`examples/` | CLI、机器协议、数据集评估和算法校准 |

项目采用一个产品仓库、两个薄入口和一个共享核心。CLI 与 GUI 分别构建发布，但不维护两套评分实现。

## 开发与验证

Windows 构建使用 Python 3.12：

```sh
python -m venv .venv
# 在当前终端中激活虚拟环境。
python -m pip install -e '.[desktop,dev,packaging]'
oi-eegqc-desktop
python scripts/check.py --calibrate
```

Windows 上的 `scripts/build-desktop.ps1` 依次执行产品测试、注入故障校准、PyInstaller 打包、冻结应用启动检查和 Inno Setup 安装器生成。CI 调用仓库中的同一套脚本，不在工作流配置里隐藏另一套构建逻辑。

进一步文档：

- [架构与模块职责](docs/architecture.md)
- [评分规则与边界](docs/scoring.md)
- [桌面端操作](docs/desktop.md)
- [Windows 构建与发布](docs/windows-app.zh-CN.md)
- [历史研究与基准说明](docs/archive/research-v0.5.zh-CN.md)
