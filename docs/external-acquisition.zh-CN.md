# 外部采集程序存储与入库规范

适用版本：EEGQC 0.6.15。推荐 EEG-BIDS 保存信号、通道与事件，附 `session.json` 保存人员平台编号和范式代码。采集程序负责落盘，EEGQC 负责评分、上传及入库。

## 必需的身份信息

在采集目录中放 `session.json`，旁边建立 `eeg/` 目录。例子中的编号与时间需要替换为真实值。

```json
{
  "schema_version": 1,
  "participant_id": "00000001",
  "session_id": "7a65d04271984c1099a085a44c3fc301",
  "paradigm_code": "RSVP-20260924",
  "started_at": "2026-09-30T09:00:00+08:00",
  "ended_at": "2026-09-30T09:30:00+08:00",
  "recording_status": "complete",
  "stop_reason": "planned_end"
}
```

- 被试编号必须是平台已有的 8 位编号，以字符串保留前导零。
- 来源 Session ID 用稳定 UUID；重试上传不更换，新采集使用新 ID。允许 1–128 位字母、数字、下划线、连字符。
- `paradigm_code` 从平台范式目录取得，不使用可变的展示名称。视频为 `AVEEG-20260923`，RSVP 为 `RSVP-20260924`。其他已启用范式同样支持。
- 起止时间使用带时区的 ISO 8601 和 24 小时制。不能用上传时间或文件修改时间代替采集时间。
- 来源 Session ID 和平台 Session ID 是不同标识。EEGQC 上传成功后匹配被试并提交来源记录，外部程序不需要连接数据库或预先伪造平台 Session。

## 目录结构

```text
dataset/
  dataset_description.json
  participants.tsv
  sub-00000001/
    ses-7a65d04271984c1099a085a44c3fc301/
      session.json
      eeg/
        sub-00000001_ses-7a65d04271984c1099a085a44c3fc301_task-things_run-01_eeg.edf
        sub-00000001_ses-7a65d04271984c1099a085a44c3fc301_task-things_run-01_eeg.json
        sub-00000001_ses-7a65d04271984c1099a085a44c3fc301_task-things_run-01_channels.tsv
        sub-00000001_ses-7a65d04271984c1099a085a44c3fc301_task-things_run-01_events.tsv
        sub-00000001_ses-7a65d04271984c1099a085a44c3fc301_task-things_run-02_eeg.edf
```

同一个被试、同一范式的多段连续记录可以放在同一 Session 下，每段使用不同 run 编号。断连恢复后开始新 run，采样点和事件时间从新文件首样本重新计算。各 run 时长按实际样本相加，不把中间断连间隙算成数据。

`session.json` 是公司扩展，BIDS 验证时通过 `.bidsignore` 排除。质量报告也属于派生数据，不能把完整目录未经验证就称为严格符合 BIDS。

## 格式支持

| 存储形式 | 识别方式 |
|---|---|
| `session.json` + EDF/BDF | 显式读取被试、范式、来源 Session，评分 `eeg/` 中每个主记录 |
| 标准 BIDS EDF/BDF | 从 `sub`、`ses`、`task` 识别，`*_scans.tsv` 中 `acq_time` 提供实际起始时间 |
| 博睿康 float32 | 保留 `.float32.json` 中的实际采样率、形状、通道名、微伏单位和封存状态 |
| NPY 与 `metadata.json` | 支持 `participant_id` / `participant_number` / `subject_id`；采样率、单位和排列必须明确 |
| 单独 EDF/BDF/NPY/float32 | 可以评分和上传；自动入库仍需要完整身份与采集时间 |

标准 BIDS 的 `task-av` 对应视频，`task-things` / `task-rsvp` 对应 RSVP。其他范式提供显式 `paradigm_code` 或在界面选择。相互冲突的范式会明确提示；不同范式应分别导出目录。

无 `session.json` 时，BIDS 来源 ID 为 `bids-sub-编号-ses-标签`；同一个被试、同一范式的独立采集必须使用不同 Session 标签。

## 通道、时序和事件

EEG 文件头必须写实际采样率、物理单位与通道标签。`*_channels.tsv` 的顺序与原始信号一致，正确区分 EEG、EOG、ECG 和 Trigger；默认评分只选择标为 EEG 的通道。32 导和 64 导使用同一约定，以真实记录配置为准。

NPY 的 JSON 写明 `sfreq` 或 `SamplingFrequency`、`unit`、`channels_first`，建议提供 `channel_names`。也支持 BIDS 同记录通道表和逐层继承的 `*_eeg.json`。NPY/float32 是支持的公司数据格式，并非标准 EEG-BIDS 原始格式。

保存连续 EEG，在 `*_events.tsv` 中按刺激保存 `onset`、`duration`、`trial_type`、`stimulus_id`、`trial_id`、`sample_index`、`trigger_value`、`completed`、`timing_source`。额外字段在 `events.json` 解释。前两列单位为秒；onset 对应该文件首个样本，sample_index 采用从 0 开始的公司约定。

未完成刺激记录真实呈现时长，不补成计划时长。软件显示时间与硬件 Trigger 时间分开记录；未知硬件延迟保留未知，不填 0 冒充校准。

## 多 run 评分、上传和入库

每个 run 单独评分并保存报告，Session 总分按实际记录时长加权；总有效时长是各及格 run 可用时长之和。Session 只提交一次，保留每个 run 的分数和算法版本。未知评分保留为空，不将失败记录伪装成及格，也不阻止原始数据上传。

单记录报告为 `eegqc-report.json`；多记录保存 `*.eegqc-report.json` 及 `eegqc-session-report.json`。事件文件会上传保留，本入口执行整 run 评分，不自动按每次刺激切片评分。

文件封存后在 EEGQC 主界面拖入目录或 ZIP，核对身份与范式。平台返回上传目的地，EEGQC 处理续传和入库回执。外部采集程序无需持有云存储密钥或 PostgreSQL 密码。

文件上传成功和数据库入库成功分别显示。缺少采集时间、时区或时长时，文件仍可上传，入库提示具体缺项；补齐源元数据后重新导入。

## 更新与部署

安装版和便携版优先读取公司 `/updates/eegqc/eegqc-stable.json`。旧 `/updates/av-capture/eegqc-stable.json` 与 GitHub Releases 保留。旧清单继续使用旧路径的下载地址，供已安装旧版升级。

其他部署可以通过 `OI_EEGQC_RELEASES_URL` 配置 HTTPS 更新清单，通过 `OI_EEGQC_UPLOAD_ORIGIN` 配置 HTTPS 上传服务。具体清单结构见[英文接口说明](external-acquisition.md)。

## 当前限制

自动公司入库要求已有的 8 位人员编号与带时区起止时间。混合范式不能作为一个 Session 提交。BrainVision、EEGLAB 暂需导出 EDF/BDF。存在某个 run 未评分时，Session 总分和总有效时长为空，已评分 run 的明细仍保留。

参考：[EEG-BIDS](https://bids-specification.readthedocs.io/en/stable/modality-specific-files/electroencephalography.html)、[BIDS Events](https://bids-specification.readthedocs.io/en/stable/modality-agnostic-files/events.html)。
