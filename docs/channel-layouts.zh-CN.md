# 通道布局

`oi-eegqc` 只使用记录里已经有的通道名。它**不会**从数组长度反推 montage，也不会把某一家 SDK 的行序写进 `evaluate_recording`。

厂商对照表是加载器旁边的 YAML（`src/oi_eegqc/layouts/*.yaml`）。新 SDK 按同一 schema 再放一个文件即可。评分核心始终只看见 `ch_names`。

## 名字从哪来

1. 显式 `channel_names`（sidecar、session 元数据、`--ch-names`，或 EDF/BDF 文件头）
2. 显式 `channel_layout` 编号（通道数必须一致）
3. 设备别名 **且** 通道数匹配（例如 BrainCo 32 导音视频 session）
4. 否则用占位名 `EEG00` … `EEG{n-1}`

同一厂商、通道数对不上时，仍走占位名。死导留在分母里；布局只负责贴标签。

桌面把 session 文件夹里的 `metadata.json` 当成这份 `continuous_eeg.npy` 的参数来源（采样率、`device_type`）。对上 `brainco` 且为 32 导时，详情里会写「P8 全程为 0」这种通道说明，而不是 `ch000`。

sidecar 示例：

```json
{"sfreq": 1000, "unit": "uV", "channel_layout": "bcigo_sdk_1.0.2"}
```

## bcigo-sdk 1.0.2

当前音视频采集（`device_type: brainco`，32 行）用下面这套**数据流顺序**。来源是 `bcigo-sdk` 1.0.2 原生库里的 EDF 标签表，**不是** [PyPI](https://pypi.org/project/bcigo-sdk/1.0.2/) 上按脑区罗列的 Channel Layout。

PyPI 按 10-20 分区写，并把 IO 放在最后；若把那份名单当行号，IO 会变成第 31 行。SDK 数组里 **第 0 行是 P8，第 28 行是 IO**。

| 行 | 名称 | EDF 标签 | 类型 |
| --: | ---- | --------- | ---- |
| 0 | P8 | EEG P8 | eeg |
| 1 | P7 | EEG P7 | eeg |
| 2 | T8 | EEG T8 | eeg |
| 3 | T7 | EEG T7 | eeg |
| 4 | F8 | EEG F8 | eeg |
| 5 | F7 | EEG F7 | eeg |
| 6 | O2 | EEG O2 | eeg |
| 7 | O1 | EEG O1 | eeg |
| 8 | P4 | EEG P4 | eeg |
| 9 | P3 | EEG P3 | eeg |
| 10 | C4 | EEG C4 | eeg |
| 11 | C3 | EEG C3 | eeg |
| 12 | F4 | EEG F4 | eeg |
| 13 | F3 | EEG F3 | eeg |
| 14 | Fp2 | EEG Fp2 | eeg |
| 15 | Fp1 | EEG Fp1 | eeg |
| 16 | TP10 | EEG TP10 | eeg |
| 17 | TP9 | EEG TP9 | eeg |
| 18 | FT10 | EEG FT10 | eeg |
| 19 | FT9 | EEG FT9 | eeg |
| 20 | CP6 | EEG CP6 | eeg |
| 21 | CP5 | EEG CP5 | eeg |
| 22 | FC6 | EEG FC6 | eeg |
| 23 | FC5 | EEG FC5 | eeg |
| 24 | CP2 | EEG CP2 | eeg |
| 25 | CP1 | EEG CP1 | eeg |
| 26 | FC2 | EEG FC2 | eeg |
| 27 | FC1 | EEG FC1 | eeg |
| 28 | IO | EOG IO | eog |
| 29 | Pz | EEG Pz | eeg |
| 30 | Cz | EEG Cz | eeg |
| 31 | Fz | EEG Fz | eeg |

IO 会入库评分。EDF 标签是 `EOG IO`，数组里用的名字是 `IO`，**不在**默认辅助导名单（`ECG`、`EOG` 等）里。除非记录自己把它标成辅助导，否则不要丢掉。

机器可读副本：[`src/oi_eegqc/layouts/bcigo_sdk_1.0.2.yaml`](../src/oi_eegqc/layouts/bcigo_sdk_1.0.2.yaml)。
