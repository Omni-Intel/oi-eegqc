# 视频片段质检接口 v1

`OI-EEGQC.exe --quality-request request.json --quality-output response.json` 无窗口运行，同步退出。入口同样可用 `python -m oi_eegqc.segment_service`。请求中的脑电文件必须已停止写入。便携包 `_internal/quality-api.json` 声明支持的协议。

请求 schema 为 `oi-eegqc-segments-v1`，字段：request_id、session_id、participant、recording（EDF/BDF 绝对路径）、threshold（默认 60）、timing_status、segments。每个 segment 包含唯一 trial_id、stimulus_id、onset、offset（相对于 EEG 文件起点的秒数）、event_valid、stimulus_duration_s。

接口按 `[onset, offset)` 读取片段，在片段内调用与桌面评分一致的 evaluate_recording；不读取整轮到内存。MNE 的电压数据用 V 声明。起止采样点使用向上取整，拒绝越界、非有限时间、未确认物理单位、无效事件和不足 1 秒片段。

响应原样关联 request_id、session_id；每段返回 score、state（passed、retry、needs_review）、完整报告、实际采样点边界。score < threshold 为 retry，等于阈值通过。无法评分时 score 为 null，不能当成通过或低分。summary 为各段统计、算术平均分和最低分，不是包含休息期的整轮 EEG 评分。保存评分算法与阈值版本、短窗证据。异常返回 error 和非零退出码，响应通过临时文件原子替换。

接口不改写原始 EEG，不决定实验排程，不上传数据。调用方维护重采队列。时间边界由调用方提供，信号质检分数不证明设备时钟或物理声画同步精度；未校准状态必须保留。
