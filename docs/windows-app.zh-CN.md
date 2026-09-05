# Windows 入库 QC 壳层

桌面程序只做一件事：**把文件夹拖进去，看出 A/B/C/D**。
评分全部在 Python sidecar 里。Windows 进程不读波形、不刮人读 CLI、不接云。

参考客户端（协议用法，不是 UI）：[`examples/sidecar_session.py`](../examples/sidecar_session.py)。

## 为什么不是 Electron

Electron = Chromium + Node，空壳也要一百多 MB 内存，冷启动慢，和「极简、快速、轻量」相反。
QC 工作台是一张表，不是网站。

| 方案 | 体积 / 启动 | 结论 |
| --- | --- | --- |
| **WinUI 3 或 WPF** | 系统 WebView/控件，毫秒级 | **用这个** |
| Tauri 2 | 几 MB + 系统 WebView2 | 只有需要网页视觉时才考虑 |
| Electron | 内嵌 Chromium | 不做 |

UI 技术选你们熟的：C# WPF 足够。一个窗口、一个 `DataGrid`、一个进度条、一个取消按钮。

## 进程模型

应用启动时拉起 **一个** 常驻 sidecar，退出时再杀：

```text
python -m oi_eegqc serve --stdio
```

```text
[WinUI / WPF]
   拖放 / DataGrid
        │  JSON over stdin/stdout（一行一个对象）
        ▼
[oi-eegqc serve --stdio]  ← 唯一评分进程
        │
        ▼
 evaluate_recording / score_adapter
```

硬规则：

- 只说话协议，不 spawn `oi-eegqc bench ...` 再解析终端字。
- sidecar **启动一次**，按任务排队；不要每个文件起一个 Python。
- stdout 只读 JSON；sidecar 的 stderr 进日志文件，不进 UI。
- 信封 `schema_version` 必须是 `oi-eegqc-protocol-v1`，否则拒绝并提示升级。
- 报告体 `schema_version` 必须是 `oi-eegqc-report-v1`。
- 一行没读完不要 `json.loads`；用 `StreamReader.ReadLineAsync`。
- 工作目录、`PYTHONPATH`、venv 在启动参数里写死，不要依赖用户手工 `cd`。

打包（第一版）：旁边放 embeddable CPython + `oi-eegqc` wheel，PATH 指向它。
不要 PyInstaller 打成单个 2 GB 包，也不要捆绑完整 Conda。

## 一个窗口，三个状态

不要导航栏、不要登录、不要看板、不要波形浏览器。

```text
┌──────────────────────────────────────────┐
│  拖入会话文件夹 / BDF / npy                │
│  unit [V ▼]   sfreq [250]   [开始] [取消]  │
│  ████████░░░░  3 / 12   rest_full          │
├──────────────────────────────────────────┤
│  clip          字母  GQI   ODQ   可用性     │
│  rest_6s        A    96.2  98    Available │
│  nback_18s      B    81.4  84    Caution   │
│  td10_broken    D     0.0  12    Unavailable│
└──────────────────────────────────────────┘
```

| 状态 | 做什么 |
| --- | --- |
| Idle | 等待拖放；npy 目录才显示 sfreq/unit |
| Running | 用 `progress.done/total` 画条；Cancel 发 `{"op":"cancel","target_id": id}` |
| Done | 绑定 `reports[]`；点行只展开 `reasons`（可选）；导出 = 把 `done` 信封存盘 |

字母颜色：A 绿、B 默认、C 琥珀、D 红。GQI 雷达、频谱图、通道拓扑 **v1 不做**。

## 拖放怎么路由

Windows 只看路径，不猜科学含义：

| 拖入 | 发出的请求 |
| --- | --- |
| 目录且含 `session.json` | `score_dataset`，`dataset=hw`，`root=该目录` |
| 目录，里面是 `.npy` | `score_dataset`，`dataset=npy`，必须问 `sfreq` 和 `unit` |
| `.bdf` / `.edf` | `score_file`，`unit=V`（MNE 读进来是伏特） |
| 单个 `.npy` | `score_file` + `sfreq` |

`hw` 根目录可以是「一次会话」或「多次会话的父目录」，sidecar 自己分辨。
机器模式 **必须** 显式传 `root`，不要写死开发机路径。

## 会话期协议（最少集）

启动后先 `ping`，确认 `protocol == oi-eegqc-protocol-v1`。

```json
{"id":"boot","op":"ping"}
{"id":"job-1","op":"score_dataset","dataset":"hw","root":"D:\\sessions\\20260820-174347_rest_full"}
{"id":"job-1","op":"cancel","target_id":"job-1"}
{"id":"boot","op":"shutdown"}
```

回包按 `event` 分支：`pong` / `progress` / `done` / `error` / `ack`。
`progress` 只更新进度条；表格以最终 `done.reports` 为准（含 `extras`）。
`cancel` 在两条记录之间生效；当前这条会评完；已完成的行保留，`cancelled: true`。

错误只展示 `message`，逻辑只认 `code`：

`missing_root` · `missing_sfreq` · `unknown_unit` · `unknown_dataset` ·
`mne_required` · `file_not_found` · `invalid_request` · `unknown_op` · `eval_failed`

BDF 没装 mne extra 时会收到 `mne_required`：提示安装 `oi-eegqc[mne]`，不要在 C# 里重试解码。

## C# 启动骨架

```csharp
var psi = new ProcessStartInfo
{
    FileName = Path.Combine(AppContext.BaseDirectory, "python", "python.exe"),
    Arguments = "-m oi_eegqc serve --stdio",
    UseShellExecute = false,
    RedirectStandardInput = true,
    RedirectStandardOutput = true,
    RedirectStandardError = true,
    CreateNoWindow = true,
    StandardOutputEncoding = Encoding.UTF8,
    StandardInputEncoding = Encoding.UTF8,
};
psi.Environment["PYTHONPATH"] = Path.Combine(AppContext.BaseDirectory, "oi-eegqc", "src");
var proc = Process.Start(psi);
// 读 stdout 一行 → JsonSerializer.Deserialize<Envelope>
// 写 stdin 一行 + '\n'，立刻 Flush
```

`id` 用递增字符串。一次只跑一个 `score_*`；下一批等上一个 `done` 或 `error`。
不要并行开第二个 sidecar 抢 GPU/磁盘。

## 明确不做（v1）

- 登录、账号、云同步、SwanLab 上传
- 任务评估（N-back 正确率、ASSR、解码）
- 在 UI 里改评分阈值（改 YAML 是实验室的事）
- 把 `device` / `plan` 平铺进表格列——那些在 `extras` 里，需要再取
- 把开发机 `/vePFS-0x0e/...` 写进安装包

设置若有，只存 `%AppData%\oi-eegqc\ui.json`：上次的 `unit`、`sfreq`、窗口位置。
