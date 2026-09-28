import json
import numpy as np
from oi_eegqc.adapters import pick_eeg_channels
from oi_eegqc.config import default_config
from oi_eegqc.io.neuracle import load_neuracle_float32
from oi_eegqc.intake import inspect_channel_names, score_file


def test_lossless_neuracle_units_aux_and_score(tmp_path):
    names = [f'EEG{i:02d}' for i in range(59)] + ['ECG','HEOR','HEOL','VEOU','VEOL','TRG']
    rng = np.random.default_rng(7)
    data = rng.normal(0, 15, (5000,65)).astype('<f4')
    data[:,64] = 240
    data[0,0] = 12.25
    path = tmp_path/'recording.float32'
    data.tofile(path)
    path.with_suffix('.float32.json').write_text(json.dumps({'dtype':'<f4','layout':'sample_major',
      'shape':list(data.shape),'sampling_rate_hz':1000,'channel_names':names,
      'channel_units':['uV']*64+['code'],'complete':True,'event_sample_index_base':1}),encoding='utf-8')
    recording = load_neuracle_float32(path)
    assert recording.data[0,0] == 12.25
    assert inspect_channel_names(path) == names
    eeg, _, dropped = pick_eeg_channels(recording.data, names, default_config().default_aux_names)
    assert eeg.shape == (59,5000) and 'TRG' in dropped
    report = score_file(path)
    assert np.isfinite(report.gqi)
