# EEGQC 桌面版

Qt / PySide6 原生桌面界面，复用现有 Python 评分核心，完全本地处理。

## Windows 使用

解压 `OI-EEGQC-Windows-x64.zip`，打开文件夹中的 `OI-EEGQC.exe`。
保留整个文件夹（包含 `_internal`），无需另装 Python。当前构建未做代码签名。

多选或拖入 EDF / BDF / NPY 文件，或直接拖入一个或多个文件夹，再点击「评分」。
文件夹会在后台递归扫描子文件夹，自动筛选支持的文件；扫描期间可取消。
「选择文件」「选择文件夹」为两个独立的原生按钮。再次选择会追加，重复路径自动去重。
同名文件显示相对路径以区分。目录链接（含符号链接与 Windows 联接）不会递归进入；
无法访问的目录会跳过并提示。空目录会显示未找到支持的文件。
列表只显示文件名、0–100 质量分数与中文可用性。应用界面的文件名以小写显示，
实际文件名及读取路径不变；系统文件选择器仍显示文件原名。
EDF/BDF 自动读取采样率与单位。NPY 需确认采样率、单位和轴方向，
可勾选将同一组参数应用于其余数组文件；参数不同的文件应分别确认。

默认始终使用核心自适应评分，不再提供设置，也不读取旧版固定模式偏好。
文件按顺序在后台评分，单个失败不影响后续；再次点击评分只处理未完成或失败的文件。
「停止」会在当前文件完成后停止队列，保留已完成结果。评分中关闭窗口也会先完成当前文件。
可多选列表行后按删除键或右键「移除」，也可右键「清空」列表；均不删除磁盘文件。

## 开发与打包

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -e '.[desktop,packaging,dev]'
.venv/Scripts/python -m oi_eegqc.desktop
powershell -ExecutionPolicy Bypass -File scripts/build-desktop.ps1
```

macOS / Linux 可安装相同依赖后运行 `oi-eegqc-desktop`。
二进制需在目标系统运行 `python -m PyInstaller eegqc.spec` 分别构建；
当前交付包仅为 Windows x64，尚未在其他系统验证。

窗口标题使用小写公司名与中文产品名，窗口和可执行文件图标沿用品牌 ICO。
内容区域保留极简列表，不增加圆环或页脚。按钮采用平台原生样式。Qt 使用动态库随包分发；
第三方包元数据与许可证位于 `_internal`，分发时需保留。
