"""Upload destinations supplied by the personnel database API."""
import json
import threading
import time
from pathlib import Path
from urllib.request import urlopen

from .cos_backend import CosStorage
from .service import UploadCoordinator


class ParadigmRegistry:
    def __init__(self, url, state, default_storage):
        self.url, self.state, self.default_storage = url, Path(state), default_storage
        self.rows, self.loaded_at, self.routes = [], 0, {}
        self.lock = threading.RLock()

    def list(self):
        with self.lock:
            if time.monotonic() - self.loaded_at > 30:
                with urlopen(self.url, timeout=8) as response:
                    rows = json.load(response)['paradigms']
                self.rows = rows
                self.loaded_at = time.monotonic()
            return self.rows

    def route(self, code):
        with self.lock:
            row = next((r for r in self.list() if r['code'] == code), None)
            if row is None:
                raise ValueError('unknown paradigm')
            target = row['destination']
            if target['provider'] != 'cos':
                raise ValueError('unsupported storage provider')
            identity = (target['bucket'], target['region'], target['prefix'])
            if code in self.routes:
                prior, coordinator, storage = self.routes[code]
                if prior != identity:
                    raise ValueError('destination changed; finish existing uploads before changing storage')
                return coordinator, storage
            storage = CosStorage.from_env(bucket=target['bucket'], region=target['region'], prefix=target['prefix'])
            # The video collection ledger already exists and must keep its identity.
            database = self.state if code == 'AVEEG-20260923' else self.state.with_name(code + '.sqlite3')
            coordinator = UploadCoordinator(database, storage)
            self.routes[code] = identity, coordinator, storage
            return coordinator, storage
