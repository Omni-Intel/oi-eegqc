"""Read EEG-BIDS sidecars and channel tables without loading the signal."""
import csv
import json
import re
from pathlib import Path


def eeg_metadata(path):
    path = Path(path)
    entities = dict(re.findall(r'(?:^|_)([a-z]+)-([A-Za-z0-9]+)', path.stem))
    directories = []
    for parent in path.parents:
        directories.append(parent)
        if (parent/'dataset_description.json').is_file():
            break
        if len(directories) == 4:
            break
    result = {}
    if path.stem.endswith('_eeg'):
        for directory in reversed(directories):
            candidates = []
            for candidate in directory.glob('*_eeg.json'):
                tags = dict(re.findall(r'(?:^|_)([a-z]+)-([A-Za-z0-9]+)', candidate.stem))
                if all(entities.get(k) == v for k,v in tags.items()):
                    candidates.append((len(tags), candidate))
            for _,candidate in sorted(candidates):
                result.update(json.loads(candidate.read_text(encoding='utf-8-sig')))
    sidecar = path.with_suffix('.json')
    if sidecar.is_file():
        result.update(json.loads(sidecar.read_text(encoding='utf-8-sig')))
    return result


def channel_rows(path):
    path = Path(path)
    if not path.stem.endswith('_eeg'):
        return []
    table = path.with_name(path.stem[:-4]+'_channels.tsv')
    if not table.is_file():
        return []
    with table.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream, delimiter='\t'))
