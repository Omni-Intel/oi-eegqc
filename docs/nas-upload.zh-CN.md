# RustFS NAS 上传

上传服务支持 `UPLOAD_BACKEND=s3`，通过 S3 SigV4 预签名 URL 将文件直接上传到 RustFS。
当前 NAS API 地址为 `http://172.16.4.2:9000`，桶为 `oi-data`，region 为 `us-east-1`，使用 path-style。
`omni-oss:oi-data` 中的 `omni-oss` 是 rclone remote 名称；它不是 endpoint 或桶名。

## 范式与目录

按 `acq-task-template` 的 README，将每个试验的 `metadata.toml` 中的 `unique_name`
配置为 `destination.prefix`。不要将刺激素材桶 `oi-task-assets` 用作采集归档桶。
范式代码 `code` 继续使用人员平台已有代码；它与 `unique_name` 是不同的字段。

2026-11-05，维护者确认三个没有 `metadata.toml` 的旧范式以现有编号作为 NAS `unique_name`：
`AVEEG-20260923`、`RSVP-20260924`、`DoRSVP-20261011`。这是旧范式的明确命名约定，
不从显示名称推断；新范式仍取自身的 `metadata.toml`。

上传后的目录为：

```text
oi-data/<unique_name>/<uploadId>/<导入目录名及内部文件>
```

这里 `uploadId` 是 EEGQC 生成且在重试时复用的归档记录编号，作为此上传器的
`recording-id`。原采集 `session_id` 保存在采集元数据和平台入库信息中，不会被改写。
如果另一套归档系统要求原采集 ID 直接作为顶层目录名，应先建立 ID 映射，不能直接改名
已上传目录或删除上传状态。一个范式不能通过修改显示名称来推断其 `unique_name`。

范式目标可由人员平台的 `/v1/capture/paradigms` 返回，也可在上传服务器设置
`UPLOAD_PARADIGMS_FILE`，使用 JSON 文件提供完整范式列表。文件格式见
[`configs/rustfs-upload.example.json`](../configs/rustfs-upload.example.json)。
复制成 `configs/rustfs-upload.local.json` 并替换范式代码、名称及前缀后使用；本机文件被 Git 忽略。
服务器只向客户端返回代码、名称和公共存储目标，不返回访问密钥。

## 上传服务器配置

先安装依赖：

```powershell
python -m pip install -e '.[s3-server]'
```

PowerShell 示例（填入真实范式配置、状态目录和监听地址）：

```powershell
$env:UPLOAD_BACKEND = 's3'
$env:S3_ENDPOINT = 'http://172.16.4.2:9000'
$env:S3_BUCKET = 'oi-data'
$env:S3_REGION = 'us-east-1'
$env:S3_PREFIX = '<metadata.toml 中的 unique_name>'
$env:UPLOAD_PARADIGMS_FILE = (Resolve-Path 'configs/rustfs-upload.local.json').Path
$env:UPLOAD_STATE = 'D:\EEG_Data\upload-state\signer.sqlite3'
$env:LISTEN_HOST = '127.0.0.1'
$env:LISTEN_PORT = '8443'
# 在服务器运行环境注入 S3_ACCESS_KEY_ID、S3_SECRET_ACCESS_KEY；也支持标准 AWS 凭据链。
oi-eegqc-upload-signer
```

`S3_ENDPOINT`、`S3_BUCKET` 和 `S3_REGION` 的默认值就是上述 NAS 配置；`S3_PREFIX` 必须显式指定。
这些变量设置无范式请求的默认目标；带有范式代码的请求使用 JSON/API 中该范式的目标。
两处前缀应按实际部署保持一致。保持已有的 HTTPS 反向代理或配置 `TLS_CERT` / `TLS_KEY`，
客户端访问的是上传服务 API，S3 endpoint 则用于访问 NAS。默认监听示例仅供本机代理使用。

凭据只加载到上传服务器。不要把真实密钥写入范式 JSON、采集程序或版本管理文件。
服务器不会检查或创建桶，适用于已有的 `oi-data` 桶以及不具备建桶权限的账号。
应用需要目标前缀内的读写（含覆盖写入、复制及读取复制源），以及发起、列举和完成分片的权限。
NAS 上传不调用删除对象或终止分片接口，不需要 `s3:DeleteObject` 或 `s3:AbortMultipartUpload`。

## 客户端与后台采集交接

部署本分支构建的客户端后，刷新上传范式，确认显示
`s3://oi-data/<unique_name>/ · http://172.16.4.2:9000`，然后准备上传。
旧客户端只支持 COS 地址，不能使用 NAS 签名目标。

从 0.7.2 起，客户端向签名服务声明 NAS 能力，并携带已准备任务的目标标识。
部署时将切换前的 COS 范式列表保存为只读 JSON，通过 `UPLOAD_LEGACY_PARADIGMS_FILE` 提供。
旧客户端继续获取和使用 COS 目标，新客户端获取当前 NAS 目标；新版重试已准备的 COS 任务时仍路由到旧目标。
保留两个账本和旧存储，直至对应任务完成，不需要所有电脑同时空闲或逐台清空队列。

若需要更换上传服务 API 地址，设置 `OI_EEGQC_UPLOAD_ORIGIN` 为该服务的 HTTPS origin。
不要将此变量设置成 NAS 的 S3 endpoint。

后台 `--capture-complete-request` 可以在请求 JSON 中增加 `paradigm_code`，或在客户端环境设置
`OI_EEGQC_UPLOAD_PARADIGM`。该入口会读取同一范式目标并使用隔离队列。
两者都未设置时，保留原有 COS 采集交接行为；S3 服务会拒绝未绑定目标的旧上传任务，
应配置范式后重新准备，不能把 COS 回执当作 NAS 回执。

## 分片与切换

- S3 文件超过 128 MiB 时使用分片，默认每片 64 MiB；非常大的文件会自动增大片大小以满足分片数限制。
- 保留现有串行文件/分片发送和服务端并行确认；rclone 的 `upload_concurrency=8` 不直接控制此上传器。
- Python HTTP 客户端使用 HTTP/1.1；不需要额外配置 `disable_http2`。
- 客户端队列和服务端账本按 provider、endpoint、bucket、region、prefix 隔离。更换 endpoint 或前缀后分配新状态，旧状态文件保留。
- 混合范式上传按 Session 元数据路由到各自目标；普通上传和混合上传共享同一目标的子任务编号、续传记录和锁。
  任何 NAS 目标变化都会选择新的混合队列，旧 COS 队列和完成记录保留。
- 在运行中的上传服务里更换同一范式的目标会被拒绝；应先完成旧任务，备份账本，更新配置并重启服务。
- NAS 小文件先上传 `.staging/<uploadId>/<roundId>/` 下的暂存对象，再由服务端复制到最终路径。
  发布成功后保留暂存副本，不请求删除；迟到的重试 PUT 只会影响暂存路径，不会覆盖正式文件。
  这些副本会额外占用空间，但管理员不必清理它们才能继续上传，不能批量清理正在上传的暂存对象。
- NAS 每个上传目录只使用一个 `_UPLOAD_STATE.json`，通过覆盖写入记录 `version=1`、
  `state=updating|complete`、`uploadId` 和 `roundId`，不再生成或删除 `_COMPLETE` / `_UPDATING`。
  新一轮开始时先覆盖为 `updating`，完成全部文件后覆盖为 `complete`。
  读取者必须检查 JSON 的 `state` 和对应的 `roundId`，不能以文件存在判断完成；
  人员平台仍以签名服务上传账本的完成回执为准。已有 COS 标记协议保持原样。
  切换到此实现不要求删除已有 NAS 对象；旧标记不作为 NAS 完成依据。

归档到 NAS 后仍调用原有平台入库接口。平台服务位于其他仓库：在生产切换前，需同步其范式目标、
NAS 访问和账本查询路径，确认统计核验支持 S3。仅配置本仓库不能修改已经部署的人员平台。
历史 COS 数据不会自动迁移；应另行复制、核验内容并保留定位映射，不能复用旧完成标记。

客户端升级与存储切换须分别验收。`0.7.1` 增加定时检查和错峰空闲安装，详见[桌面更新说明](desktop.md)。
发布时须同步 `/updates/eegqc/eegqc-stable.json` 与 `/updates/av-capture/eegqc-stable.json`，
先上传并核验安装器、便携包及其大小和 SHA-256，再原子替换清单。只发布 GitHub Release 不能推动优先使用内网的客户端升级。
一直运行的旧版仍需等待其下一次自然启动检查；新版本的定时检查不会追溯改变旧进程的行为。

## 验证

```powershell
python -m pip install -e '.[desktop,dev,s3-server,s3-test]'
python -m pytest -q tests/test_s3_upload.py tests/test_paradigm_upload.py tests/test_cos_upload.py
python scripts/check.py
```

S3 集成测试在本机启动临时模拟服务，覆盖 HTTP 直传、空目录、完成标记、文件复用以及服务重启后的分片续传，
不写入真实 NAS。测试通过不能代替生产 RustFS 与人员平台的联调。
