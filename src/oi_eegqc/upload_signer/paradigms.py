"""Upload destinations supplied by the personnel database API."""
import json
import re
import threading
import time
from pathlib import Path
from urllib.request import urlopen

from .cos_backend import CosStorage
from .service import UploadCoordinator, NotFound, Conflict
from .s3_backend import S3Storage
from ..upload_destination import destination_id, normalize_destination


class ParadigmRegistry:
    def __init__(self, url, state, default_storage, config_file=None, legacy_config_file=None):
        self.url, self.state, self.default_storage = url, Path(state), default_storage
        self.rows, self.loaded_at, self.routes = [], 0, {}
        self.lock = threading.RLock()
        self.config_file = Path(config_file) if config_file else None
        self.legacy = (ParadigmRegistry(None, state, default_storage, config_file=legacy_config_file)
                       if legacy_config_file else None)

    def route_for_client(self, code, target_id=None, supports_s3=False):
        if target_id:
            for registry in (self, self.legacy):
                if registry is None:
                    continue
                row = next((r for r in registry.list() if r['code'] == code), None)
                if row and destination_id(row['destination']) == target_id:
                    return registry.route(code)
            raise ValueError('storage target is not configured for this paradigm')
        return (self if supports_s3 or self.legacy is None else self.legacy).route(code)

    def list(self):
        with self.lock:
            if time.monotonic() - self.loaded_at > 30:
                if self.config_file:
                    rows = json.loads(self.config_file.read_text(encoding="utf-8"))["paradigms"]
                else:
                    with urlopen(self.url, timeout=8) as response:
                        rows = json.load(response)['paradigms']
                public_rows = [dict(code=r["code"], name=r["name"],
                                    destination=normalize_destination(r["destination"])) for r in rows]
                codes = [r["code"] for r in public_rows]
                if len(set(codes)) != len(codes) or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", c) for c in codes):
                    raise ValueError("invalid or duplicate paradigm code")
                self.rows = public_rows
                self.loaded_at = time.monotonic()
            return self.rows

    def route(self, code):
        with self.lock:
            row = next((r for r in self.list() if r['code'] == code), None)
            if row is None:
                raise ValueError('unknown paradigm')
            target = normalize_destination(row['destination'])
            identity = destination_id(target)
            if code in self.routes:
                prior, coordinator, storage = self.routes[code]
                if prior != identity:
                    raise ValueError('destination changed; finish existing uploads before changing storage')
                return coordinator, storage
            if target["provider"] == "s3":
                storage = S3Storage.from_env(**{k: v for k, v in target.items() if k != "provider"})
            else:
                storage = CosStorage.from_env(bucket=target['bucket'], region=target['region'], prefix=target['prefix'])
            # The video collection ledger already exists and must keep its identity.
            database = self.state if code == 'AVEEG-20260923' else self.state.with_name(code + '.sqlite3')
            if target["provider"] == "s3":
                database = self.state.with_name(f"{code}-s3-{identity}.sqlite3")
            coordinator = UploadCoordinator(database, storage)
            self.routes[code] = identity, coordinator, storage
            return coordinator, storage

    def round_status(self, code, upload_id, round_id):
        # Initialize the current target, then search only this paradigm's canonical
        # ledgers. Old COS/NAS receipts retain the target recorded in their ledger.
        self.route(code)
        legacy = self.state if code == 'AVEEG-20260923' else self.state.with_name(code + '.sqlite3')
        databases = {legacy, *self.state.parent.glob(code + '-s3-*.sqlite3')}
        found = []
        for database in sorted(databases):
            if not database.is_file():
                continue
            try:
                found.append(UploadCoordinator.readonly_round_status(database, upload_id, round_id))
            except NotFound:
                continue
        if not found:
            raise NotFound('upload round not found')
        if len(found) != 1:
            raise Conflict('upload round exists in multiple storage ledgers')
        return found[0]
