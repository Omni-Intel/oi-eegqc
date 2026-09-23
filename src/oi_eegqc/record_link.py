"""Omni shared records v2. One authoritative local database for both products.

No EEG decoding, scoring, uploading or source-file deletion belongs here.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from datetime import datetime,timezone


def default_path():
    return Path(os.environ.get('OMNI_RECORD_HUB',str(Path(os.environ.get('LOCALAPPDATA',Path.home()))/'Omni-Intelligence'/'records.sqlite')))


def record_id(producer, source_id):
    return producer+':'+hashlib.sha256(str(source_id).encode('utf-8')).hexdigest()


def contains(root, path):
    root,path=Path(root).resolve(),Path(path).resolve()
    return path==root or root in path.parents


class RecordHub:
    def __init__(self,path=None):
        self.path=Path(path or default_path());self.path.parent.mkdir(parents=True,exist_ok=True)
        legacy=self.path.parent/'RecordLink'/'records.sqlite'
        migrated=False
        if self.path==default_path() and not self.path.exists() and legacy.exists():
            source=sqlite3.connect(legacy);target=sqlite3.connect(self.path)
            try:source.backup(target);migrated=True
            finally:source.close();target.close()
        with self.connect() as db:
            version=db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0,1,2):raise ValueError('公共名单版本不受支持')
            db.execute('''CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY, root TEXT NOT NULL UNIQUE, producer TEXT NOT NULL,
                payload TEXT NOT NULL,
                removed INTEGER NOT NULL DEFAULT 0,
                qc TEXT NOT NULL DEFAULT '{}', upload TEXT NOT NULL DEFAULT '{}',
                updated REAL NOT NULL)''')
            if version==1:
                db.execute('ALTER TABLE records DROP COLUMN requested')
                db.execute('ALTER TABLE records DROP COLUMN removal_ack')
            db.execute('PRAGMA user_version=2')
        if migrated:
            archive=self.path.parent/'backups';archive.mkdir(exist_ok=True)
            legacy.rename(archive/f'records-v1-{time.time_ns()}.sqlite')

    def connect(self):
        db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row
        return db

    def publish(self,identifier,root,producer,payload):
        root=str(Path(root).resolve());data=json.dumps(payload,ensure_ascii=False,sort_keys=True)
        with self.connect() as db:
            existing=db.execute('SELECT * FROM records WHERE id=? OR root=?',(identifier,root)).fetchone()
            if existing:
                if existing['root']!=root:raise ValueError('记录编号对应的来源路径已改变')
                # An owner refresh must not resurrect removed records or overwrite QC state.
                if existing['producer']==producer and existing['payload']!=data:
                    db.execute('UPDATE records SET payload=?,updated=? WHERE id=?',(data,time.time(),existing['id']))
                return existing['id']
            db.execute('INSERT INTO records(id,root,producer,payload,updated) VALUES(?,?,?,?,?)',(identifier,root,producer,data,time.time()))
        return identifier

    def rows(self):
        with self.connect() as db:rows=[dict(r) for r in db.execute('SELECT * FROM records ORDER BY updated DESC')]
        for r in rows:
            for key in ('payload','qc','upload'):r[key]=json.loads(r[key])
        def order(row):
            try:
                moment=datetime.fromisoformat(row['payload'].get('date','').replace('Z','+00:00'))
                stamp=moment.replace(tzinfo=timezone.utc).timestamp() if moment.tzinfo is None else moment.timestamp()
            except (ValueError,TypeError,OverflowError):stamp=float('-inf')
            return (-stamp,row['id'])
        return sorted(rows,key=order)

    def get(self,identifier):
        return next((r for r in self.rows() if r['id']==identifier),None)

    def remove(self,identifier):
        with self.connect() as db:db.execute('UPDATE records SET removed=1,updated=? WHERE id=? AND removed=0',(time.time(),identifier))

    def restore(self,identifier):
        with self.connect() as db:db.execute('UPDATE records SET removed=0,updated=? WHERE id=?',(time.time(),identifier))

    def status(self,identifier,kind,value):
        if kind not in ('qc','upload'):raise ValueError('Invalid status kind')
        value=json.dumps(value,ensure_ascii=False,sort_keys=True)
        with self.connect() as db:db.execute(f'UPDATE records SET {kind}=?,updated=? WHERE id=? AND {kind}<>?',(value,time.time(),identifier,value))
