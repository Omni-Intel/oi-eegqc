from oi_eegqc.score_cache import ScoreCache
from oi_eegqc.types import REPORT_SCHEMA_VERSION


def report(score=90):
    return {"schema_version": REPORT_SCHEMA_VERSION, "gqi": score}


def test_digest_reuses_unchanged_file_without_reading_it_again(tmp_path, monkeypatch):
    source = tmp_path / "data.npy"
    source.write_bytes(b"eeg-data")
    cache = ScoreCache(tmp_path / "cache.sqlite3")
    first, identity = cache.digest(source)
    def no_read(*args, **kwargs):
        raise AssertionError("unchanged file should use its stored content hash")

    monkeypatch.setattr(type(source), "open", no_read)
    second, same_identity = cache.digest(source)
    assert second == first
    assert same_identity == identity


def test_report_cache_depends_on_content_parameters_and_algorithm(tmp_path):
    cache = ScoreCache(tmp_path / "cache.sqlite3")
    digest = "a" * 64
    metadata = {"sfreq": 250, "unit": "uV", "line_hz": 50}
    assert cache.lookup(digest, metadata) is None
    cache.store(digest, metadata, report())
    assert cache.lookup(digest, metadata)["gqi"] == 90
    assert cache.lookup(digest, {**metadata, "line_hz": 60}) is None


def test_changed_file_gets_a_new_digest(tmp_path):
    source = tmp_path / "data.npy"
    source.write_bytes(b"first")
    cache = ScoreCache(tmp_path / "cache.sqlite3")
    first, _ = cache.digest(source)
    source.write_bytes(b"second-and-longer")
    second, _ = cache.digest(source)
    assert first != second


def test_old_algorithm_report_is_not_reused_after_clipping_fix(tmp_path, monkeypatch):
    import oi_eegqc.score_cache as module
    cache = ScoreCache(tmp_path / "cache.sqlite3")
    with monkeypatch.context() as old:
        old.setattr(module, "SCORING_ALGORITHM_VERSION", "oi-eegqc-score-v1")
        cache.store("a" * 64, {}, report(0))
    assert cache.lookup("a" * 64, {}) is None


def test_recording_report_reuses_score_without_reading_signal_or_directory(tmp_path,monkeypatch):
    from oi_eegqc.score_reports import write_report,read_report,report_path
    source=tmp_path/'run.npy';source.write_bytes(b'eeg')
    stat=source.stat();identity=(stat.st_size,stat.st_mtime_ns)
    meta=dict(sfreq=250,unit='uV',line_hz=50)
    write_report(source,meta,report(),identity)
    assert report_path(source).is_file()
    monkeypatch.setattr(type(source),'iterdir',lambda *a:(_ for _ in ()).throw(AssertionError('directory rescan')))
    assert read_report(source,meta)['gqi']==90
    assert read_report(source,dict(meta,sfreq=500)) is None
    source.write_bytes(b'changed')
    assert read_report(source,meta) is None


def test_intake_respects_current_channel_parameters(tmp_path):
    import numpy as np
    import json
    from oi_eegqc.quick import IntakeWorker
    from oi_eegqc.score_reports import write_report
    from oi_eegqc.intake import inspect_file
    path=tmp_path/'data.npy';np.save(path,np.zeros((2,1000)))
    path.with_suffix('.json').write_text(json.dumps(dict(sfreq=250,unit='uV')))
    stat=path.stat();metadata=dict(inspect_file(path),line_hz=50)
    write_report(path,metadata,dict(gqi=90),(stat.st_size,stat.st_mtime_ns))
    worker=IntakeWorker([path],set(),tmp_path/'imports',keep=['Ch1'])
    worker.run()
    assert worker.items and not worker.reports
    worker=IntakeWorker([path],set(),tmp_path/'imports')
    worker.run()
    assert worker.reports[str(path)]['gqi']==90
