# Channel layouts

`oi-eegqc` scores whatever names a recording already has. It does **not** infer
a montage from array length, and it does not bake one vendor’s row order into
`evaluate_recording`.

Vendor maps live as named YAML files next to the loader
(`src/oi_eegqc/layouts/*.yaml`). Add another SDK by adding another file with
the same schema. Scoring still only sees `ch_names`.

## How a recording gets names

1. Explicit `channel_names` (sidecar, session metadata, `--ch-names`, or EDF/BDF header)
2. Explicit `channel_layout` id (must match the channel count)
3. Device alias **and** matching channel count (for example BrainCo 32-ch AV sessions)
4. Otherwise positional names `EEG00` … `EEG{n-1}`

A vendor tag on the wrong channel count stays on the fallback. Dead electrodes
stay in the denominator; a layout only labels them.

The desktop app treats `metadata.json` beside `continuous_eeg.npy` as session
parameters (sampling rate, `device_type`). A matching BrainCo 32-channel stamp
gets named notes such as “P8 is all zeros”, not `ch000`.

Sidecar example:

```json
{"sfreq": 1000, "unit": "uV", "channel_layout": "bcigo_sdk_1.0.2"}
```

## bcigo-sdk 1.0.2

Current audiovisual sessions (`device_type: brainco`, 32 rows) use this stream
order. It is the native EDF label table inside `bcigo-sdk` 1.0.2, **not** the
grouped list on [PyPI](https://pypi.org/project/bcigo-sdk/1.0.2/).

PyPI prints electrodes by 10-20 region and puts IO last. Numbering that list
would put IO at row 31. The SDK array has **P8 at row 0** and **IO at row 28**.

| Row | Name | EDF label | Kind |
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

IO is recorded and scored. The EDF label is `EOG IO`; the array name we use is
`IO`, which is **not** on the default aux list (`ECG`, `EOG`, …). Do not drop it
unless a recording actually marks it as auxiliary.

Machine-readable copy: [`src/oi_eegqc/layouts/bcigo_sdk_1.0.2.yaml`](../src/oi_eegqc/layouts/bcigo_sdk_1.0.2.yaml).
