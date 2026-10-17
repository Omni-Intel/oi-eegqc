# EEGQC 0.6.17

- EOG and other auxiliary channels are excluded from EEG quality metrics, including EDF/BDF labels such as `EOG IO` and channels declared by BIDS metadata.
- The BCIGO 32-channel layout classifies IO as an ocular auxiliary channel; it is no longer counted in the EEG montage or score denominator.
- Scoring algorithm version is `oi-eegqc-score-v7` so reports and caches distinguish results produced with corrected channel selection.
