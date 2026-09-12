# Windows 入库 QC 应用

当前交付是 **Qt 原生窗口**（`OI-EEGQC.exe`），评分走与 CLI 相同的 `intake.score_file`。
不做 Electron，不刮人读 CLI，不接云。

## 交付

GitHub Releases 仅提供安装器 [Windows 下载](https://github.com/Omni-Intel/oi-eegqc/releases/latest/download/OI-EEGQC-Setup-Windows-x64.exe)。
安装器装到用户目录，不要求管理员，不签名。
设置里的「检查更新」只读最新 release，有新版本就打开下载页。

## 一个窗口

表：文件 · 分数（GQI 0–100）· 状态。
字母和可用性留在报告里，不当入库线。悬停分数可看采样率、时长和主要原因。

拖入 EDF / BDF / NPY；数组缺采样率或单位再问。已知 SDK 的行序见
[channel-layouts.zh-CN.md](channel-layouts.zh-CN.md)。
强脑 session 文件夹（`continuous_eeg.npy` + `metadata.json`）会自动对上通道名。
设置：排列、电网 50/60 Hz、是否全通道（关掉后勾选导联）、检查更新。
评分后点「查看」看子项分数和哪一路出了问题。
批量摘要是平均 GQI，外加按时长加权的可用时间比例。

操作细节见 [desktop.md](desktop.md)。协议示例：[`examples/sidecar_session.py`](../examples/sidecar_session.py)。
