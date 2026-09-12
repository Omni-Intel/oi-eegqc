# Windows QC 应用

当前交付是 **Qt / PySide6 原生窗口**，和 CLI / `serve --stdio` 共用 `oi_eegqc.intake.score_file`。
Windows 进程不读波形；评分在本地 Python 里完成，不接云。

不要 Electron。也不把人读 CLI 的终端字刮进界面。

## 交付形态

仅提供 GitHub Releases 安装版：

- `OI-EEGQC-Setup-Windows-x64.exe`：Inno Setup 安装器，写入 `%LOCALAPPDATA%\Omni-Intelligence\EEGQC`，不要求管理员。

不做代码签名。设置 → 检查更新会请求
`https://api.github.com/repos/Omni-Intel/oi-eegqc/releases/latest`，
使用安装器资源，没有安装器则打开 release 页面。检查不上传脑电。

本地：`scripts/build-desktop.ps1`。远程：打 `v*` tag 或手动跑
`.github/workflows/windows-release.yml`。

## 和 sidecar 的关系

`oi-eegqc serve --stdio` 仍是给其他壳（脚本、以后的 WinUI）用的协议入口，
参考 [`examples/sidecar_session.py`](../examples/sidecar_session.py)。
已打包的 Qt 应用走同一套 `score_file`，不另写评分。

信封 `schema_version` 必须是 `oi-eegqc-protocol-v1`；报告体 `oi-eegqc-report-v1`。

## 界面

一个窗口、一张表：文件、GQI（0–100）、状态。
字母分和可用性仍写在报告 JSON 里，界面不当入库判定；入库线不写进软件。

NPY 缺采样率或单位时弹窗；EDF/BDF 读文件头。已知 SDK 的行序见
[channel-layouts.md](channel-layouts.md)，可在 sidecar 里写 `channel_layout`。
设置里可改数组排列、50/60 Hz 电网、是否全通道（关掉后勾选导联），并检查更新。
评分后点「查看」或双击该行，看子项分数和哪一路出了问题。
强脑 session 文件夹（`continuous_eeg.npy` + `metadata.json`）会自动对上通道名。
批量摘要是平均 GQI，外加按时长加权的可用时间比例。

详细操作见 [desktop.md](desktop.md)。
