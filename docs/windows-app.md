# Windows QC 应用

当前交付是 **Qt / PySide6 原生窗口**，和 CLI / `serve --stdio` 共用 `oi_eegqc.intake.score_file`。
Windows 进程不读波形；评分在本地 Python 里完成，不接云。

不要 Electron。也不把人读 CLI 的终端字刮进界面。

## 交付形态

两条包，都走 GitHub Releases：

- `OI-EEGQC-Setup-Windows-x64.exe`：Inno Setup 安装器，写入 `%LOCALAPPDATA%\Omni-Intelligence\EEGQC`，不要求管理员。
- `OI-EEGQC-Windows-x64.zip`：便携目录，解压后保留 `_internal`。

不做代码签名。设置 → 检查更新会请求
`https://api.github.com/repos/Omni-Intel/oi-eegqc/releases/latest`，
优先打开安装器资源，没有安装器再打开 zip 或 release 页面。检查不上传脑电。

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

NPY 缺采样率或单位时弹窗；EDF/BDF 读文件头。设置里可改数组排列、50/60 Hz 电网，并检查更新。

详细操作见 [desktop.md](desktop.md)。
