"""Durable operational events shared by portable and installed desktop builds."""
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
import traceback
import uuid
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from . import __version__

ENDPOINT = 'https://personnel.intra.omni-intel.cn/v1/capture/telemetry'


class Telemetry:
    def __init__(self, root, *, enabled=True, install_kind='portable'):
        root = Path(root)
        self.path = root / 'telemetry.sqlite'
        self.enabled = enabled
        self.install_kind = install_kind
        self.paradigm = None
        self.stop = threading.Event()
        self.lock = threading.Lock()
        try:
            root.mkdir(parents=True, exist_ok=True)
            with self.db() as db:
                db.execute('CREATE TABLE IF NOT EXISTS client(device TEXT NOT NULL)')
                saved = db.execute('SELECT device FROM client LIMIT 1').fetchone()
                self.device = saved[0] if saved else str(uuid.uuid4())
                if not saved:
                    db.execute('INSERT INTO client VALUES (?)', (self.device,))
                db.execute('CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, payload TEXT NOT NULL, created REAL DEFAULT (unixepoch()))')
        except (OSError, sqlite3.Error):
            self.enabled = False
            self.device = str(uuid.uuid4())
            return
        if enabled:
            threading.Thread(target=self._run, name='eegqc-telemetry', daemon=True).start()

    def db(self):
        return sqlite3.connect(self.path, timeout=1)

    def report(self, phase, *, exc=None, count=None):
        try:
            event = dict(id=str(uuid.uuid4()), device=self.device, app='EEGQC',
                         at=datetime.now(timezone.utc).isoformat(), version=__version__,
                         phase=phase, install_kind=self.install_kind)
            if self.paradigm:
                event['paradigm'] = self.paradigm
            if count is not None:
                event['count'] = count
            if exc is not None:
                event['exception'] = type(exc).__name__
                for key in ('errno', 'winerror'):
                    value = getattr(exc, key, None)
                    if isinstance(value, int):
                        event[key] = value
                status = getattr(exc, 'status_code', getattr(exc, 'code', None))
                if isinstance(status, int):
                    event['http_status'] = status
                event['frames'] = [dict(file=re.sub(r'[^\w. -]', '_', Path(f.filename).name)[:100],
                                        line=f.lineno, function=re.sub(r'[^\w<>.]', '_', f.name)[:80])
                                   for f in traceback.extract_tb(exc.__traceback__)[-8:]]
            with self.db() as db:
                db.execute('INSERT INTO events(id,payload) VALUES (?,?)', (event['id'], json.dumps(event)))
                db.execute('DELETE FROM events WHERE created < unixepoch()-2592000 OR rowid NOT IN (SELECT rowid FROM events ORDER BY rowid DESC LIMIT 1000)')
        except (OSError, sqlite3.Error):
            pass

    def install_hooks(self):
        import sys
        previous = sys.excepthook
        def uncaught(kind, exc, tb):
            self.report('unhandled_error', exc=exc)
            previous(kind, exc, tb)
        sys.excepthook = uncaught
        previous_thread = threading.excepthook
        def thread_error(args):
            self.report('thread_error', exc=args.exc_value)
            previous_thread(args)
        threading.excepthook = thread_error
        owner = self
        class Errors(logging.Handler):
            def emit(self, record):
                if record.name.startswith('oi_eegqc'):
                    owner.report('logged_error', exc=record.exc_info[1] if record.exc_info else None)
        logging.getLogger().addHandler(Errors(level=logging.ERROR))

    def flush(self, *, opener=urlopen):
        with self.lock:
            with self.db() as db:
                rows = db.execute('SELECT id,payload FROM events ORDER BY rowid LIMIT 20').fetchall()
            if not rows:
                return
            request = Request(ENDPOINT, data=json.dumps({'events': [json.loads(r[1]) for r in rows]}).encode(),
                              headers={'Content-Type': 'application/json'}, method='POST')
            with opener(request, timeout=8) as response:
                result = json.load(response)
            if result.get('accepted') == [r[0] for r in rows]:
                with self.db() as db:
                    db.executemany('DELETE FROM events WHERE id=?', [(r[0],) for r in rows])

    def _run(self):
        delay = 5
        while not self.stop.wait(delay):
            try:
                self.flush()
                delay = 30
            except (OSError, ValueError, sqlite3.Error):
                delay = min(delay * 2, 300)
