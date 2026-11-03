"""One local queue with canonical, independently resumable paradigm batches."""
import json
import re
import secrets
from pathlib import Path

from .desktop_upload import BatchStore, ID_PATTERN, UploadError, normalize_roots, plain_path
from .local_files import atomic_json

MIXED_CODE = '__mixed__'


def paradigm_directory(data_directory, code):
    if not re.fullmatch(ID_PATTERN, code):
        raise UploadError('平台返回了无效的范式编号')
    data_directory = Path(data_directory)
    return (data_directory/'uploads-cos-beijing' if code == 'AVEEG-20260923'
            else data_directory/'uploads'/code)


def aggregate_status(parts):
    states = {p['status'] for p in parts}
    if states == {'completed'}:
        return 'completed'
    for state in ('uploading', 'failed', 'paused'):
        if state in states:
            return state
    return 'ready'


class MixedBatchStore(BatchStore):
    def __init__(self, data_directory, paradigms):
        super().__init__(Path(data_directory)/'uploads-mixed')
        self.paradigms = {p['code']: p for p in paradigms}
        self.stores = {
            code: BatchStore(paradigm_directory(data_directory, code), row)
            for code, row in self.paradigms.items()
        }
        self.groups = {}
        self.routes = {}
        self._written = {}
        self._group_written = {}

    def configure(self, roots, sessions):
        """Keep old roots when possible; never scan a mixed parent into one bucket."""
        roots = normalize_roots(roots)
        identified = []
        for row in sessions:
            code = row.get('paradigm_code')
            if code not in self.stores:
                raise UploadError('采集 Session 的范式未识别或平台未启用，无法自动分流')
            identified.append((plain_path(row['session_directory']).resolve(), code))
        for directory, code in identified:
            if any(directory != other and directory in other.parents and code != other_code
                   for other, other_code in identified):
                raise UploadError('不同范式的 Session 目录相互嵌套，请核对采集元数据')
        groups = {}
        for root in roots:
            members = [(directory, code) for directory, code in identified
                       if directory == root or root in directory.parents or directory in root.parents]
            codes = {code for _, code in members}
            if not codes:
                raise UploadError('混合上传目录中存在未关联 Session 的文件，请补齐采集元数据')
            if len(codes) == 1:
                groups.setdefault(next(iter(codes)), []).append(root)
            else:
                from .desktop_import import discover_files
                files, errors = discover_files([root])
                if errors or any(not any(directory in Path(p).resolve().parents
                                         for directory, _ in members) for p in files):
                    raise UploadError('混合目录中有无法关联范式的脑电文件，请核对 Session 元数据')
                for directory, code in members:
                    groups.setdefault(code, []).append(directory)
        self.groups = {code: normalize_roots(values) for code, values in sorted(groups.items())}
        self.routes = {str(root): code for code, values in self.groups.items() for root in values}
        return list(self.routes)

    def unchanged_completed(self, root, cancelled=lambda: False):
        return self.stores[self.routes[str(Path(root).resolve())]].unchanged_completed(root, cancelled)

    def prepare(self, roots, scored, cancelled=lambda: False, progress=lambda name: None,
                new_acquisition=False, completed=None):
        parts, groups = [], []
        for code, group_roots in self.groups.items():
            store = self.stores[code]
            group = store.prepare(group_roots, scored, cancelled, progress, new_acquisition,
                                  completed=completed)
            members = group.get('children') or [group]
            for part in members:
                if part.get('paradigm_code') not in (None, code):
                    raise UploadError('既有上传记录的范式与采集元数据不一致，请联系维护人员')
                part['paradigm_code'] = code
                if part.get('destination') is None:
                    part['destination'] = dict(self.paradigms[code]['destination'])
            parts.extend(members)
            groups.append(dict(code=code, local_id=group['local_id']))
        if not parts:
            raise UploadError('没有可上传的采集 Session')
        if len({p['local_id'] for p in parts}) != len(parts):
            raise UploadError('本机子任务编号冲突，请保留上传状态并联系维护人员')
        batch = dict(version=2, local_id=secrets.token_hex(16), upload_id=None,
                     allocation_pending=False, mixed_paradigms=True, groups=groups,
                     roots=[r for part in parts for r in part['roots']],
                     status=aggregate_status(parts), error='', current='', children=parts,
                     entries=[e for part in parts for e in part['entries']])
        self.save(batch)
        return batch

    def _write_record(self, batch):
        signature = json.dumps(batch, sort_keys=True)
        if self._written.get(batch['local_id']) == signature:
            return
        code = batch.get('paradigm_code')
        if code:
            if code not in self.stores:
                raise UploadError('混合续传状态包含平台未启用的范式')
            # Persist allocation/round IDs canonically on every child checkpoint.
            self.stores[code]._write_record(batch)
        super()._write_record(batch)
        self._written[batch['local_id']] = signature

    def _read_group(self, code, local_id):
        try:
            if code not in self.stores or not isinstance(local_id, str) or not re.fullmatch(ID_PATTERN, local_id):
                raise ValueError()
            value = json.loads((self.stores[code].directory/local_id/'state.json').read_text(encoding='utf-8'))
            if value['local_id'] != local_id or value['version'] != 2:
                raise ValueError()
            return value
        except (OSError, KeyError, TypeError, ValueError):
            raise UploadError('混合上传续传状态无法读取，请保留状态并联系维护人员') from None

    def save(self, batch):
        super().save(batch)
        for ref in batch.get('groups', []):
            parts = [p for p in batch.get('children', []) if p.get('paradigm_code') == ref['code']]
            if not parts:
                continue
            group = self._read_group(ref['code'], ref['local_id'])
            if group.get('children'):
                group.update(children=parts, entries=[e for p in parts for e in p['entries']],
                             roots=[r for p in parts for r in p['roots']], status=aggregate_status(parts))
            else:
                group = parts[0]
            key = (ref['code'], ref['local_id'])
            signature = json.dumps(group, sort_keys=True)
            if self._group_written.get(key) != signature:
                store = self.stores[ref['code']]
                store._write_record(group)
                atomic_json(store.directory/'current.json', {'local_id': group['local_id']})
                self._group_written[key] = signature

    def load(self):
        batch = super().load()
        if batch is None:
            return None
        parts = []
        for snapshot in batch.get('children', []):
            code = snapshot.get('paradigm_code')
            part = self._read_group(code, snapshot['local_id'])
            if (part.get('paradigm_code') != code or part.get('roots') != snapshot.get('roots')
                    or part.get('destination') != snapshot.get('destination')):
                raise UploadError('混合上传的范式或来源目录不一致，请保留状态并联系维护人员')
            if part['status'] == 'uploading':
                part['status'] = 'paused'
            parts.append(part)
        if not parts:
            raise UploadError('混合上传续传记录缺少子任务')
        batch.update(children=parts, entries=[e for p in parts for e in p['entries']],
                     roots=[r for p in parts for r in p['roots']], status=aggregate_status(parts))
        return batch

    def reset_folder(self, root):
        raise UploadError('请切换到该 Session 对应的范式后，再重置单个上传记录')
