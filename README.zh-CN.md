# OI-EEGQC 统一评分与上传软件

[English](README.md) · [下载已发布 Windows 安装包](https://github.com/Omni-Intel/oi-eegqc/releases/latest/download/OI-EEGQC-Setup-Windows-x64.exe)

面向采集现场的原生桌面软件：本地 EEG 评分、结果缓存、采集文件夹增量上传。正式界面统一使用 Qt Quick / PySide6；NPY、EDF、BDF 进入同一个评分核心，照片、视频和其他采集文件随文件夹上传。

## 使用流程

选择采集文件夹 → 确认必要采样参数 → 评分 → 上传。未变化的数据复用评分；跨日上传复用采集编号，只传新增或变化文件。未完成上传时继续采集，先暂停，再选择文件夹、评分并继续。

检查更新走 green-hk HTTPS 镜像；文件直接上传 TOS，签名服务器不转发文件内容。详见[操作说明](docs/desktop.md)和[上传说明](docs/folder-upload.md)。

## 本地开发与交付

Windows 构建统一使用 Python 3.12。在仓库根目录创建并激活虚拟环境，然后：

```sh
python -m pip install -e '.[desktop,dev,packaging]'
oi-eegqc-desktop
python scripts/check.py --calibrate
```

安装 Inno Setup 6 后执行 `scripts/build-desktop.ps1`。本地与 CI 都调用这个脚本完成测试、注入故障校验、打包、打包后启动及评分验证、安装器生成。CI 负责触发和交付，不承载独有的产品逻辑。

## 评分规则与架构

v0.6 开发版使用报告格式 v3、评分算法 v2、阈值版本 v0.6.0。疑似削顶按原始信号的时间窗检查，短暂异常不再把整路导联全程判坏；报告保存窗口证据、通道名和实际配置。界面分开展示通过窗口比例与“时长评分”。升级后旧评分缓存失效，上传记录保留。发布前仍需 Windows 实机验证及真实采集数据复核。

- [架构与模块职责](docs/architecture.md)
- [评分与削顶规则](docs/scoring.md)
- [Windows 构建](docs/windows-app.md)
- [通道布局](docs/channel-layouts.zh-CN.md)
- [更新镜像](docs/update-mirror.md)
- [历史研究与 CLI 说明](docs/archive/research-v0.5.zh-CN.md)

CLI、数据集适配器和 Python API 继续用于自动化与算法验证，共用评分核心。旧 QWidget 界面仅作为源码回归测试保留，不进入安装包。
