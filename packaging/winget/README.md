# WinGet 社区源说明

WinGet **不能直接上传安装包**。公开目录的做法是向 [microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs) 提交 YAML 清单的 Pull Request，由微软机器人下载安装器、做静默安装测试后再合并。合并后用户才能：

```powershell
winget install OmniIntel.OIEEGQC
winget upgrade OmniIntel.OIEEGQC
```

## 现在适不适合上架

| 条件 | 现状 |
|---|---|
| 静默安装 | 已支持：Inno `/VERYSILENT /NORESTART /CURRENTUSER` |
| 稳定 HTTPS 直链 | 用 GitHub Releases 直链 `https://github.com/Omni-Intel/oi-eegqc/releases/download/v<tag>/OI-EEGQC-Setup-Windows-x64.exe` |
| 代码签名 | 没有。社区源允许未签名 EXE，但 SmartScreen / Defender 声誉检查可能拖几天，也更容易误报 |
| 采集点网络差 | **不适合作为主通道**。WinGet 还要访问微软源，安装包仍然要从外网拉完整安装器 |

采集电脑应优先用仓库脚本从 GitHub 静默安装，不必先上架 WinGet：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-desktop.ps1
```

## 以后若要提交

1. 从 GitHub Releases 抄出版本、安装器 URL 和 SHA-256，填进本目录三个 YAML。
2. 本机验证：`winget validate packaging/winget` 然后 `winget install --manifest packaging/winget`。
3. 安装 [wingetcreate](https://github.com/microsoft/winget-create)，用 GitHub 账号提交 PR，不要把安装器本体推到 `winget-pkgs`。

本目录 YAML 是模板，`InstallerSha256` 必须在发版后改成真实摘要，不能带着占位符提交。
