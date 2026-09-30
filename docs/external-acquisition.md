# External acquisition files

EEGQC 0.6.15 identifies a participant, paradigm and source Session independently of the acquisition application. Import a directory or ZIP from the main window. Each Session is scored across all its recordings, uploaded and registered once.

## Company manifest

Place `session.json` beside `eeg/`. Use the platform's eight-digit participant number as a string. Keep the source Session ID unchanged when retrying an upload. Each newly acquired Session needs a new ID.

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

`paradigm_code` is read explicitly and selects a matching platform paradigm. It may be any enabled platform code, not only the built-in video and RSVP aliases. A conflicting selection is reported before upload. Put different paradigms in separate Session directories.

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

Use standard EEG-BIDS sidecars to document sampling, reference, filters, channel types and event timing. `session.json` is a company extension; exclude it with `.bidsignore` when validating BIDS. Preserve a continuous recording per run. A reconnection starts a new run with its own time origin; never remove the gap and call the joined signal continuous.

## Other supported layouts

| Layout | Identification and scoring |
|---|---|
| EEG-BIDS EDF/BDF | `sub-########`, optional `ses-...`, and `task-...`; all runs in `eeg/` are included. `*_scans.tsv` supplies `acq_time` when no manifest supplies times. |
| Session manifest + EDF/BDF | Uses explicit personnel ID, source Session and paradigm; filenames need not be BIDS. |
| Session manifest + Neuracle float32 | Uses `.float32.json` sampling, dimensions, labels and units. A same-stem EDF export is not scored twice. |
| Legacy `metadata.json` + NPY | Recognizes `participant_id`, `participant_number` or `subject_id`, plus `session_id` and timing fields. Existing array sampling/unit metadata remains supported. |
| Standalone EDF/BDF/NPY/float32 | Can be scored and uploaded. Automatic personnel database registration requires identified personnel and Session metadata. |

NPY sampling rate and physical units must be declared. BIDS `SamplingFrequency`, inherited `*_eeg.json` and same-recording `*_channels.tsv` are read. Only channels marked `EEG` in an available channel table are included in the default score. Specify array orientation explicitly with `channels_first`; array files remain a supported company format, not standard EEG-BIDS raw files.

Known BIDS task aliases are `av` → `AVEEG-20260923`, `things`/`rsvp` → `RSVP-20260924`. Other paradigms use explicit `paradigm_code` or operator selection. A manifest-free BIDS Session has a stable source ID `bids-sub-<number>-ses-<label>`; use unique Session labels for separate acquisitions by the same participant and paradigm.

## Multi-run scores and database registration

Every run retains its own report. The Session score is weighted by each run's recorded duration. Usable duration is the sum of usable time from passing runs. Gaps between runs are not recorded time. If a run cannot be scored, the aggregate score and aggregate usable time remain unknown; individual successful reports are retained. Raw upload is still allowed.

The source Session is submitted once, so run count and durations are not duplicated. Upload retry preserves acknowledged database receipts. Missing timestamps, time zones or duration produce a specific database-registration message after file upload; filesystem modification time is never substituted for acquisition time. Keep full date/time values with UTC offsets.

Reports are stored as `eegqc-report.json` for a single recording, or per-recording `*.eegqc-report.json` plus `eegqc-session-report.json` for multiple runs. Reports retain the scoring algorithm version. `events.tsv` is retained for downstream use; this importer scores whole runs, not individual stimulus epochs.

## Deployment

Company builds check `https://personnel.intra.omni-intel.cn/updates/eegqc/eegqc-stable.json`, then the previous `/updates/av-capture/eegqc-stable.json`, then GitHub Releases. Both portable and installed builds use this order. Old manifests continue publishing old-path download URLs because older clients validate those paths.

Set `OI_EEGQC_RELEASES_URL` to a self-hosted release manifest. Its HTTPS directory can serve `eegqc-<version>.zip` and `eegqc-<version>-setup.exe`. Manifests follow GitHub's `tag_name`, `html_url`, `assets` structure; assets contain `name`, `browser_download_url`, `size`, and `digest`. The existing update size/digest verification applies to both package types.

Set `OI_EEGQC_UPLOAD_ORIGIN` to an HTTPS upload API origin for another deployment. Its API supplies paradigm codes and destination buckets. Credentials belong in the signing service, not acquisition packages or the source repository.

## Limits

Automatic company registration requires an existing eight-digit personnel number, timezone-aware times and a source Session ID of 1–128 letters, digits, underscores or hyphens. EEGQC does not create personnel accounts. BrainVision and EEGLAB files are not supported by this importer; export EDF/BDF instead. A BIDS-shaped directory alone is not a claim of full BIDS validation.
