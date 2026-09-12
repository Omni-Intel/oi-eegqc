# 上传采集文件夹

## 操作与数据位置

1. 添加采集文件夹并完成扫描、评分，点击右下角「上传文件夹」。确认页列出实际文件夹、完整路径、全部文件数和大小；图片、视频、配置及子文件夹中的普通文件都会上传。
2. 一个本地采集文件夹长期复用同一个 `uploadId`，对象固定写入 `tos://xiekp/eeg/inbox/<uploadId>/`。第二天重新上传时，同路径覆盖、新路径追加；本地删除的文件不会删除云端旧对象。只有「作为新采集上传」会申请新的 `uploadId`。
3. 每次点击上传会开启独立 `roundId`。服务端先删除旧 `_COMPLETE` 并写 `_UPDATING`；文件清单携带内容 SHA-256，服务端自动把同路径、同大小、同内容的已有对象标记为完成，只上传新增或变化的文件。只有本轮全部文件成功后，才重新写 `_COMPLETE` 并移除 `_UPDATING`。同一 `uploadId` 同时只能有一个活动轮次。
4. 普通文件通过预签名 PUT 从客户端直传 TOS。大于 5 GiB 的文件自动使用 TOS 分片上传；取消不会终止服务端分片任务，再次上传会查询已存在的分片并只补传缺失部分。

`uploadId` 是跨日复用的采集标识，`roundId` 是一次上传操作，`multipartUploadId` 是 TOS 为某一个大文件创建的分片任务标识，三者不可互换。

父子目录会去重，不同采集文件夹各有自己的 `uploadId`。保留原文件名与内容；目录链接、特殊文件和传输期间的文件变化会阻止本轮完成。

## 网络与授权

固定签名入口为 `https://eeg-upload.kunpeng.blog`，使用系统默认 TLS 校验。客户端不保存或发送 TOS AK/SK，也不需要 `tos-credentials.json`、SSH、私有证书或 Cloudflare 凭据。签名地址只保存在内存，不写入状态。

文件字节直接发送到签名服务返回的 TOS HTTPS 地址，不经过签名服务器。客户端只接受桶 `xiekp` 的北京 TOS 主机，以及与清单匹配的最终对象键或本轮专属暂存对象键。

服务端新增接口如下；旧 `/v1/uploads/sign` 和 `/v1/uploads/<uploadId>/complete` 保留给旧客户端。某个采集已有活动轮次时，旧接口返回 409，避免绕过轮次完成条件。

- `POST /v1/uploads/rounds`：用稳定的 `clientRequestId` 创建或找回轮次，可选传已有 `uploadId`。替换未完成轮次时附带旧请求的 `replacesRequestId`；只允许替换指定的当前轮次，重试同一个新请求不重复创建。
- `POST /v1/uploads/<uploadId>/rounds/<roundId>/files`：登记文件清单，每批最多 5000 个。
- `POST /v1/uploads/<uploadId>/rounds/<roundId>/seal`：用总文件数封存清单。
- `GET /v1/uploads/<uploadId>/rounds/<roundId>`：查询轮次和文件完成状态。
- `POST .../sign` 与 `POST .../files/complete`：签名单次 PUT，并在成功后向服务端确认。
- `POST .../multipart`：初始化或找回某个文件的 TOS 分片任务。
- `POST .../multipart/parts/sign`：每批为最多 1000 个分片签名。
- `GET .../multipart/parts?path=...`：查询 TOS 已保存的分片，用于重启续传。
- `POST .../multipart/complete`：确认分片数量和大小后合并文件。
- `POST .../complete`：仅当封存清单里的文件全部完成时结束本轮。

创建轮次示例：

```json
POST /v1/uploads/rounds
{"uploadId":"20260911T132500Z-a1b2c3d4","clientRequestId":"2b174eadc4464ef9a10b145c248edc17"}

200
{"uploadId":"20260911T132500Z-a1b2c3d4","roundId":"r_20260912T083000Z_1a2b3c4d","state":"updating"}
```

登记和封存示例：

```json
POST /v1/uploads/20260911T132500Z-a1b2c3d4/rounds/r_20260912T083000Z_1a2b3c4d/files
{"files":[{"path":"session_03/continuous_eeg.npy","size":5368709121,"directory":false,"sha256":"<64位小写十六进制>"}]}

POST /v1/uploads/20260911T132500Z-a1b2c3d4/rounds/r_20260912T083000Z_1a2b3c4d/seal
{"fileCount":1}
```

分片初始化响应中的 `multipartUploadId` 只用于该文件：

```json
{"path":"session_03/continuous_eeg.npy","objectKey":"eeg/inbox/20260911T132500Z-a1b2c3d4/session_03/continuous_eeg.npy","multipartUploadId":"TOS-multipart-id","partSize":67108864,"partCount":81}
```

## 本地状态与故障恢复

上传窗口默认提供上传/继续、暂停、关闭（上传中为收起）；采集编号、新采集、恢复编号和重置记录收进“更多”。普通断网应重试原任务，不需要重置记录。

轮次接口遇到断线、超时或暂时服务错误时最多尝试三次，间隔 1、2 秒；鉴权、参数和轮次冲突不会自动重试。暂停会中断已建立的网络连接，等待连接建立或 DNS 返回仍受网络超时约束。创建轮次前先持久化请求编号，进程退出后继续使用同一编号。

未完成上传的文件夹如发生新增或内容变化，先暂停上传，再重新选择文件夹并评分、上传。客户端保留采集编号，用 `replacesRequestId` 幂等替换旧轮次；已确认且内容不变的文件不重传，未变化大文件的已上传分片继续复用。旧轮次不再能签名或确认完成，云端旧文件不删除。首次申请或轮次替换的响应丢失时，客户端先恢复原请求，再依次替换，不会另建采集编号。仍不支持一边持续写入同一个文件一边确认它上传完成；变化的 EEG 必须重评。

单次 PUT 签名增加 `uploadKey`，指向 `eeg/inbox/.staging/<uploadId>/<roundId>/<path>`；`objectKey` 仍为最终路径。客户端直传暂存对象，服务端确认活动轮次后通过 TOS 内部复制发布，旧 PUT 链接不会覆盖最终文件。确认记录落盘后删除对应暂存对象；清理失败或旧链接迟到产生的残留不影响完成状态，需后续单独清理，不改变桶生命周期设置。下游只读取采集编号目录并按 `_COMPLETE` 判断完成，不扫描 `.staging`。此协议需同步更新上传服务与客户端，尚未部署。

应用数据目录为 `%LOCALAPPDATA%\\Omni-Intelligence\\EEGQC`。`uploads/<本地任务编号>/state.json` 保存 `uploadId`、`roundId`、稳定的轮次请求编号、目录快照及进度；不会保存预签名地址或云端凭据。`uploads/folders/` 保存本地完整路径到采集记录的关联。

首次创建轮次的响应丢失时，客户端重试同一个 `clientRequestId`，服务端返回原来的 `uploadId` 和 `roundId`，不会重复分配。文件确认或整轮完成的响应丢失时，客户端重启后以服务端状态为准。分片上传重启后查询 TOS 已有分片并续传。

客户端在后台建立内容 SHA-256；路径、大小和修改时间未变时复用本地摘要，不重复读取文件。摘要既用于复用评分结果，也用于服务端判断是否需要重传。上传开始前和完成前都会重新核对快照；文件有变化时不会写完成状态。

界面中的「重置客户端记录」只删除所选文件夹的本地上传关联，需连续确认两次；它不删除本地采集文件、TOS 对象或历史前缀。移动、改名或换电脑时，应迁移应用数据目录，否则不能仅凭文件夹名称推断原 `uploadId`。

服务端状态使用 SQLite 持久化。部署端从已有 tosutil 配置读取凭据；目标桶和前缀固定，不修改桶设置，不触碰 `eeg-external-2026-09-11/`。

## 验证范围

本地测试覆盖轮次幂等恢复、清单分批登记、失败/取消后续传、签名过期重试、完成门禁、跨日增量上传、变化文件覆盖、新文件追加、旧云端对象保留、分片查询和缺片拒绝合并。真实 TOS 联调和上传后端部署需在单独批准后执行。
