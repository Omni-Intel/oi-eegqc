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
