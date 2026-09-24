"""Crash-safe state replacement, tolerant of short Windows sharing conflicts."""
import json
import os
import time
import uuid
from pathlib import Path


def atomic_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,allow_nan=False,indent=2)
            stream.flush();os.fsync(stream.fileno())
        for attempt in range(6):
            try:
                os.replace(temporary,path)
                return
            except PermissionError as exc:
                if getattr(exc,'winerror',None) not in (5,32,33) or attempt==5:raise
                time.sleep(.05*2**attempt)
    finally:
        try:temporary.unlink(missing_ok=True)
        except OSError:pass  # Preserve the original failure, not a cleanup exception.
