"""Read the lossless JellyFish sample-major recording and its acquisition sidecar."""
import json
from pathlib import Path

import numpy as np

from ..config import default_config
from ..types import RecordingInput


def load_neuracle_float32(path):
    path = Path(path)
    metadata = json.loads(path.with_suffix('.float32.json').read_text(encoding='utf-8-sig'))
    shape = tuple(int(v) for v in metadata['shape'])
    names = list(metadata['channel_names'])
    if metadata.get('dtype') != '<f4' or metadata.get('layout') != 'sample_major':
        raise ValueError('不支持的博睿康原始数据布局')
    if len(shape) != 2 or shape[0] < 1 or shape[1] != len(names):
        raise ValueError('博睿康通道数量或采样点数量不一致')
    if not metadata.get('complete') or path.stat().st_size != shape[0] * shape[1] * 4:
        raise ValueError('博睿康原始文件尚未封存或采样点数量不一致')
    units = metadata['channel_units']
    aux = {n.upper() for n in default_config().default_aux_names}
    if len(units) != len(names) or any(str(unit).lower() not in ('uv', 'µv', 'μv') for name,unit in zip(names,units) if name.upper() not in aux):
        raise ValueError('博睿康 EEG 物理单位需要明确为微伏')
    return RecordingInput(data=np.memmap(path, dtype='<f4', mode='r', shape=shape).T,
                          sfreq=float(metadata['sampling_rate_hz']), ch_names=names, unit='uV',
                          expected_n_channels=len(names), clip_id=path.stem,
                          meta={'source_path': str(path), 'device_type': 'neuracle',
                                'event_sample_index_base': metadata.get('event_sample_index_base'),
                                'recording_start_monotonic_ns': metadata.get('recording_start_monotonic_ns')})
