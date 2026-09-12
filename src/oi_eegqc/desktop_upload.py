"""Folder upload batches. No scoring, bucket administration, or remote deletion."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from pathlib import Path

BUCKET = "xiekp"
PREFIX = "eeg/inbox/"
ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
EEG_SUFFIXES = {".edf", ".edf+", ".bdf", ".npy"}
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class UploadError(Exception):
    """Only sanitized, user-facing messages belong in this exception."""


class UploadCancelled(Exception):
    pass


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def plain_path(path):
    """Reject reparse points, including junctions, along the entire source path."""
    path = Path(path).absolute()
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise UploadError("文件夹包含链接，请改选真实目录")
    return path


def fingerprint(path, cancelled=lambda: False):
    """Read lightweight file metadata; never scan the file contents."""
    if cancelled():
        raise UploadCancelled()
    path = plain_path(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise UploadError("存在不支持的特殊文件")
    return dict(size=before.st_size, mtime=before.st_mtime_ns)


def normalize_roots(roots):
    result = []
    for value in sorted({str(Path(p).absolute()) for p in roots}, key=lambda p: (len(Path(p).parts), p)):
        path = plain_path(value).resolve()
        if not path.is_dir():
            raise UploadError("源文件夹不存在")
        if not any(path == parent or parent in path.parents for parent in result):
            result.append(path)
    return result


def scan_sources(roots, cancelled=lambda: False, progress=lambda name: None, labels=None,
                 hash_cache=None):
    entries, used = [], set()
    for root in normalize_roots(roots):
        label = (labels or {}).get(str(root), root.name)
        if not label:
            raise UploadError("请选择采集文件夹，不要选择磁盘根目录")
        if label.casefold() in used:
            label += "-" + hashlib.sha256(str(root).encode()).hexdigest()[:8]
        while label.casefold() in used:
            label += "-2"
        used.add(label.casefold())

        def walk(folder):
            if cancelled():
                raise UploadCancelled()
            plain_path(folder)
            children = sorted(folder.iterdir(), key=lambda p: p.name)
            if not children:
                relative = folder.relative_to(root).as_posix()
                suffix = label + ("/" + relative if relative != "." else "")
                entries.append(dict(source=str(folder), relative=suffix.rstrip("/") + "/",
                                    directory=True, size=0, mtime=0, sha256=EMPTY_SHA256,
                                    done=False))
            for child in children:
                plain_path(child)
                if child.is_dir():
                    walk(child)
                else:
                    progress(str(child))
                    values = fingerprint(child, cancelled)
                    if hash_cache is not None:
                        from .score_cache import CacheCancelled

                        try:
                            digest, identity = hash_cache.digest(child, cancelled)
                        except CacheCancelled:
                            raise UploadCancelled() from None
                        if identity != (values["size"], values["mtime"]):
                            raise UploadError("文件在核验期间发生变化，请重试")
                        values["sha256"] = digest
                    entries.append(dict(source=str(child), relative=label + "/" + child.relative_to(root).as_posix(),
                                        directory=False, done=False, **values))
        walk(root)
    return entries


class BatchStore:
    def __init__(self, directory):
        self.directory = Path(directory)

    def save(self, batch):
        for child in batch.get("children", []):
            self._write_record(child)
        self._write_record(batch)
        atomic_json(self.directory / "current.json", {"local_id": batch["local_id"]})

    def _write_record(self, batch):
        batch_id = batch["local_id"]
        if not re.fullmatch(ID_PATTERN, batch_id):
            raise UploadError("上传状态中的批次编号无效")
        atomic_json(self.directory / batch_id / "state.json", batch)

    def load(self):
        pointer = self.directory / "current.json"
        if not pointer.exists():
            return None
        try:
            link = json.loads(pointer.read_text(encoding="utf-8"))
            batch_id = link.get("local_id") or link["upload_id"]
            if not re.fullmatch(ID_PATTERN, batch_id):
                raise ValueError()
            batch = json.loads((self.directory / batch_id / "state.json").read_text(encoding="utf-8"))
            if batch.get("children"):
                children = []
                for child in batch["children"]:
                    child_id = child["local_id"]
                    if not re.fullmatch(ID_PATTERN, child_id):
                        raise ValueError()
                    children.append(json.loads((self.directory / child_id / "state.json").read_text(encoding="utf-8")))
                batch["children"] = children
                batch["entries"] = [e for child in children for e in child["entries"]]
            if batch["version"] == 1:
                if any(e["done"] for e in batch["entries"]):
                    raise UploadError("旧认证批次已传过文件，请联系管理员迁移；旧状态已保留")
                batch.update(version=2, local_id=batch_id, upload_id=None, allocation_pending=False, error="")
                for entry in batch["entries"]:
                    entry.pop("crc64", None)
            if batch["version"] != 2 or batch["local_id"] != batch_id:
                raise ValueError()
            for part in batch.get("children") or [batch]:
                part.setdefault("round_id", None)
                part.setdefault("round_request_id", secrets.token_hex(16))
                part.setdefault("round_sealed", False)
            if batch["status"] == "uploading":
                batch["status"] = "paused"
            return batch
        except (OSError, ValueError, KeyError, TypeError):
            raise UploadError("上传状态无法读取，请保留应用数据目录并联系维护人员") from None

    def _folder_record(self, root):
        key = hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()
        pointer = self.directory / "folders" / (key + ".json")
        if pointer.exists():
            local_id = json.loads(pointer.read_text(encoding="utf-8"))["local_id"]
            if not re.fullmatch(ID_PATTERN, local_id):
                raise UploadError("文件夹上传记录无效，请联系管理员")
            record = json.loads((self.directory / local_id / "state.json").read_text(encoding="utf-8"))
            if [os.path.normcase(p) for p in record["roots"]] != [os.path.normcase(str(root))]:
                raise UploadError("文件夹上传记录不匹配，请联系管理员")
            return pointer, record
        # Adopt pre-index records without abandoning an already allocated cloud ID.
        for path in sorted(self.directory.glob("*/state.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True):
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("version") == 1 and os.path.normcase(str(root)) in [os.path.normcase(p) for p in record.get("roots", [])] and any(e.get("done") for e in record.get("entries", [])):
                raise UploadError("旧认证批次已传过文件，请联系管理员迁移；旧状态已保留")
            if record.get("version") != 2 or record.get("children"):
                continue
            if os.path.normcase(str(root)) not in [os.path.normcase(p) for p in record["roots"]]:
                continue
            members = [e for e in record["entries"] if root == Path(e["source"]) or root in Path(e["source"]).parents]
            if len(record["roots"]) != 1:
                record = dict(record, local_id=secrets.token_hex(16), roots=[str(root)], entries=members)
            label = members[0]["relative"].split("/")[0] if members else root.name
            record["labels"] = {str(root): label}
            return pointer, record
        return pointer, None

    def prepare(self, roots, scored, cancelled=lambda: False, progress=lambda name: None, new_acquisition=False):
        roots = normalize_roots(roots)
        scored = {os.path.normcase(str(Path(path).resolve())): value for path, value in scored.items()}
        for root in roots:
            if root == self.directory or root in self.directory.absolute().parents:
                raise UploadError("采集目录不能包含应用上传状态目录")
        from .score_cache import ScoreCache

        with ScoreCache(self.directory.parent / "score-cache.sqlite3") as hash_cache:
            entries = scan_sources(
                roots, cancelled, progress, hash_cache=hash_cache
            )
        signals = [e for e in entries if not e["directory"] and Path(e["source"]).suffix.lower() in EEG_SUFFIXES]
        if not signals or any(os.path.normcase(e["source"]) not in scored for e in signals):
            raise UploadError("文件夹中仍有未完成评分的数据，请重新添加并评分")
        for entry in signals:
            scored_identity = list(scored[os.path.normcase(entry["source"])])
            if scored_identity[:2] != [entry["size"], entry["mtime"]] or (
                len(scored_identity) > 2 and scored_identity[2] not in (None, entry["sha256"])
            ):
                raise UploadError("评分后数据已变化，请重新评分")
        children = []
        for root in roots:
            pointer, previous = self._folder_record(root)
            if new_acquisition:
                previous = None
            label = next(iter(previous.get("labels", {}).values()), root.name) if previous else root.name
            members = []
            for entry in entries:
                source = Path(entry["source"])
                if root != source and root not in source.parents:
                    continue
                suffix = source.relative_to(root).as_posix()
                relative = label + ("/" + suffix if suffix != "." else "")
                members.append(dict(entry, relative=relative.rstrip("/") + "/" if entry["directory"] else relative))
            identity = lambda e: (
                e["relative"], e["size"], e["mtime"], e["directory"], e.get("sha256")
            )
            unchanged = previous and [identity(e) for e in members] == [identity(e) for e in previous["entries"]]
            child = dict(previous) if previous else dict(version=2, local_id=secrets.token_hex(16), upload_id=None, allocation_pending=False)
            resume = unchanged and previous["status"] != "completed"
            if resume:
                for current, prior in zip(members, previous["entries"]):
                    current["done"] = prior["done"]
            child.update(roots=[str(root)], labels={str(root): label}, entries=members,
                         status=previous["status"] if resume else "ready", error=previous.get("error", "") if resume else "", current="")
            if not resume:
                # Keep unresolved allocations too: a lost response must not create a new collection.
                predecessors = list(child.get("superseded_requests", []))
                if previous and previous.get("round_request_id") and previous["status"] != "completed":
                    predecessors.append(previous["round_request_id"])
                child.update(round_id=None, round_request_id=secrets.token_hex(16), round_sealed=False,
                             allocation_pending=child.get("allocation_pending", False),
                             superseded_requests=predecessors)
            else:
                child.setdefault("round_id", None)
                child.setdefault("round_request_id", secrets.token_hex(16))
                child.setdefault("round_sealed", False)
            self._write_record(child)
            atomic_json(pointer, {"local_id": child["local_id"]})
            children.append(child)
        batch = children[0] if len(children) == 1 else dict(
            version=2, local_id=secrets.token_hex(16), upload_id=None, allocation_pending=False,
            roots=[str(p) for p in roots], status="ready", error="", children=children,
            entries=[e for child in children for e in child["entries"]])
        self.save(batch)
        return batch

    def reset_folder(self, root):
        """Remove only this folder's local associations, including legacy history."""
        root = str(plain_path(root).resolve())
        matches = lambda value: os.path.normcase(value) == os.path.normcase(root)

        def scrub(record):
            if record.get("children"):
                record["children"] = [p for p in record["children"] if scrub(p)]
            removed = [r for r in record.get("roots", []) if matches(r)]
            record["roots"] = [r for r in record.get("roots", []) if not matches(r)]
            for value in removed:
                record.get("labels", {}).pop(value, None)
            record["entries"] = [e for e in record.get("entries", [])
                                 if not any(matches(str(p)) for p in (Path(e["source"]), *Path(e["source"]).parents))]
            return bool(record["roots"])

        # Read all records before changing anything; malformed history must not be ignored.
        records = [(p, json.loads(p.read_text(encoding="utf-8"))) for p in self.directory.glob("*/state.json")]
        for path, record in records:
            if not any(matches(r) for r in record.get("roots", [])):
                continue
            if scrub(record):
                atomic_json(path, record)
            else:
                path.unlink()
        key = hashlib.sha256(os.path.normcase(root).encode("utf-8")).hexdigest()
        (self.directory / "folders" / (key + ".json")).unlink(missing_ok=True)
        pointer = self.directory / "current.json"
        if pointer.exists():
            local_id = json.loads(pointer.read_text(encoding="utf-8")).get("local_id", "")
            if re.fullmatch(ID_PATTERN, local_id) and not (self.directory / local_id / "state.json").exists():
                pointer.unlink()


def friendly_error(error):
    from .upload_http import NetworkUnavailable
    if isinstance(error, NetworkUnavailable):
        return "网络不可用，请联网后重试"
    if isinstance(error, UploadError):
        return str(error)
    if isinstance(error, PermissionError):
        return "无法读取本地文件或写入上传状态，请检查权限"
    if isinstance(error, FileNotFoundError):
        return "源文件已移动或删除，请恢复原路径后重试"
    if isinstance(error, TimeoutError):
        return "上传连接超时，请检查网络后重试"
    if isinstance(error, OSError) and getattr(error, "errno", None) == 28:
        return "本地磁盘空间不足，无法保存续传状态"
    code = getattr(error, "status_code", None)
    if code in (401, 403):
        return "上传请求被服务拒绝，请联系管理员检查接口权限"
    if code == 404:
        return "上传接口或目标不存在，请联系管理员"
    if code == 409:
        return "这个采集正在另一轮上传，请稍后重试"
    if code == 429 or (isinstance(code, int) and code >= 500):
        return "存储服务暂时不可用，请稍后重试"
    return "网络连接、文件读取或上传校验失败，请检查网络和本地文件后重试"


from .upload_session import UploadSession
