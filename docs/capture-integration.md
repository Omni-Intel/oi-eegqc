# AV Capture 后台接口

EEGQC 独立负责脑电评分、评分缓存、文件校验和 COS 上传。AV Capture 独立负责采集、BIDS 导出、补采计划和人员平台 Session 统计。主试可在 AV Capture 的记录页完成整轮 EEG 评分和原始文件上传；EEGQC 窗口只用于深入复核。

便携发行包中的 `quality-api.json` 声明 `capture_complete: oi-eegqc-capture-complete-v1`。采集端调用 `OI-EEGQC.exe --capture-complete-request <JSON> --capture-complete-output <JSON> --capture-complete-progress <JSON>`。请求包含协议版本、唯一请求 ID、已完成的采集目录。响应包含同一请求 ID、完成状态、上传编号和整轮分数；进度文件原子替换。调用方应将三个协议文件放在采集目录外，避免污染待上传文件清单。

重复交接同一采集目录时，EEGQC 先核对已完成的本地上传状态与文件指纹；完全相同时直接返回原上传编号，不再创建云端轮次。若已上传的目录后来发生变化，自动上传会停止并提示人工核查，以免同一实验被重复入库。未完成的上传仍使用原状态续传。

服务先验证真实采集的 Session、逐视频质检和 BIDS 状态，再使用与窗口完全相同的 `ScoreCache`、`BatchStore`、`UploadSession` 和上传锁。中断后再次调用会复用缓存和续传状态，源文件保留。云端凭据只由原有签名服务掌握。协议变更使用新的 schema，不能悄悄改变现有字段含义。
