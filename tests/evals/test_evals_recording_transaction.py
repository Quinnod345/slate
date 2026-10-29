"""Filesystem regressions for publishing a recorded eval without losing the prior fixture.

Every call goes directly to _record. No model, agent, vendor, renderer or child process is part of these fixtures.
"""

import json
import os
from pathlib import Path

import pytest

from slate.evals import runner, suite, transcript


@pytest.fixture(autouse=True)
def _no_processes(monkeypatch):
    overlay = os.environ.get("SLATE_RECORDING_OVERLAY")
    if overlay:
        assert Path(runner.__file__).resolve() == (Path(overlay) / "src/slate/evals/runner.py").resolve()
    def refused(*args, **kwargs):
        raise AssertionError("Recording filesystem tests must not start a process or a live eval.")

    for name in ("live", "run_pair"):
        monkeypatch.setattr(runner, name, refused)
    monkeypatch.setattr(runner.procs, "run", refused)
    monkeypatch.setattr(runner.subprocess, "Popen", refused)
    monkeypatch.setattr(runner.subprocess, "run", refused)


@pytest.fixture
def recording_paths(tmp_path, monkeypatch):
    pair = tmp_path / "source"
    pair.mkdir()
    messages = [
        {"type": "item.completed", "item": {"type": "reasoning", "text": "Private explanation."}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "A clear reply."}},
    ]
    (pair / "transcript.jsonl").write_text("".join(json.dumps(item) + "\n" for item in messages), encoding="utf-8")
    (pair / "run.json").write_text(json.dumps({"result": {"meta": {
        "systemPromptReport": {"private_setup": "Not part of a recording."}, "session": "fixture-session"}}}),
        encoding="utf-8")
    (pair / "probe.jsonl").write_text('{"kind":"child","browser":"none","stdin":"devnull"}\n', encoding="utf-8")
    (pair / "tripwire.log").write_text("", encoding="utf-8")
    (pair / "meta.json").write_text(json.dumps({
        "case": "recording-case", "config": "local-fixture", "format": "codex-jsonl",
        "synthetic": False, "exit_code": 0, "argv": ["local-fixture", "--case", "private-input"],
    }), encoding="utf-8")
    dest = tmp_path / "recorded" / "recording-case" / "local-fixture"
    monkeypatch.setattr(suite, "recorded_fixture", lambda case_id, config_id: dest)
    return pair, dest


def _files(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def _identities(folder):
    return {str(p.relative_to(folder)): (p.stat().st_ino, p.stat().st_mtime_ns, p.stat().st_mode)
            for p in folder.rglob("*")}


def _prior(dest):
    dest.mkdir(parents=True)
    (dest / "transcript.jsonl").write_bytes(b'{"old":"complete"}\n')
    (dest / "meta.json").write_bytes(b'{ "case": "recording-case", "synthetic": false }\n')
    (dest / "nested").mkdir()
    (dest / "nested" / "proof.bin").write_bytes(b"\x00\xffprior exact bytes\n")
    return _files(dest), _identities(dest)


def _histories(dest):
    return sorted(dest.parent.glob(f".{dest.name}.previous-*/fixture"))


def _pending(dest):
    return list(dest.parent.glob(f".{dest.name}.recording-*"))


def _record(pair):
    return runner._record(pair, "recording-case", "local-fixture", "codex-jsonl")


def _is_publish(source, target, dest):
    return (source.parent == dest.parent and source.name.startswith(f".{dest.name}.recording-")
            and Path(target) == dest)


def test_record_keeps_the_existing_sanitization_and_replay_path(recording_paths):
    pair, dest = recording_paths
    original = _files(pair)

    assert _record(pair) == dest

    assert transcript.load(dest, "codex-jsonl").final == "A clear reply."
    saved = [json.loads(line) for line in (dest / "transcript.jsonl").read_text().splitlines()]
    assert saved[0]["item"]["text"] == ""
    envelope = json.loads((dest / "run.json").read_text())
    assert envelope["result"]["meta"] == {"session": "fixture-session"}
    meta = json.loads((dest / "meta.json").read_text())
    assert "argv" not in meta and meta["case"] == "recording-case" and meta["synthetic"] is False
    assert (dest / "probe.jsonl").read_bytes() == original["probe.jsonl"]
    assert (dest / "tripwire.log").read_bytes() == b""
    assert _files(pair) == original and not _histories(dest) and not _pending(dest)


@pytest.mark.parametrize("failure", ["sanitize", "read", "write", "metadata"])
def test_record_staging_failures_leave_the_prior_fixture_exact(recording_paths, monkeypatch, failure):
    pair, dest = recording_paths
    before, identities = _prior(dest)

    def refused(*args, **kwargs):
        raise OSError(f"Injected {failure} failure.")

    if failure == "sanitize":
        monkeypatch.setattr(runner, "sanitize_recording", refused)
    elif failure == "read":
        read_text = Path.read_text

        def fail_read(self, *args, **kwargs):
            if self == pair / "run.json":
                return refused()
            return read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", fail_read)
    elif failure == "write":
        write_text = Path.write_text

        def fail_write(self, *args, **kwargs):
            if self.name == "run.json" and pair not in self.parents:
                return refused()
            return write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", fail_write)
    else:
        (pair / "meta.json").write_bytes(b'{"incomplete":')
    original = _files(pair)

    with pytest.raises(json.JSONDecodeError if failure == "metadata" else OSError):
        _record(pair)

    assert _files(dest) == before and _identities(dest) == identities
    assert _files(pair) == original
    assert not _histories(dest) and not _pending(dest)


def test_record_archive_failure_leaves_the_prior_fixture_exact(recording_paths, monkeypatch):
    pair, dest = recording_paths
    before, identities = _prior(dest)
    rename = getattr(runner, "_rename_recording", lambda source, target: source.rename(target))

    def fail_archive(self, target):
        if self == dest:
            raise OSError("Injected archive rename failure.")
        return rename(self, target)

    monkeypatch.setattr(runner, "_rename_recording", fail_archive, raising=False)

    with pytest.raises(OSError, match="archive rename"):
        _record(pair)

    assert _files(dest) == before and _identities(dest) == identities
    assert not _histories(dest) and not _pending(dest)


def test_record_publish_failure_restores_the_prior_fixture_exact(recording_paths, monkeypatch):
    pair, dest = recording_paths
    before, identities = _prior(dest)
    rename = getattr(runner, "_rename_recording", lambda source, target: source.rename(target))

    def fail_publish(self, target):
        if _is_publish(self, target, dest):
            raise OSError("Injected publication failure.")
        return rename(self, target)

    monkeypatch.setattr(runner, "_rename_recording", fail_publish, raising=False)

    with pytest.raises(OSError, match="publication"):
        _record(pair)

    assert _files(dest) == before and _identities(dest) == identities
    assert not _histories(dest) and not _pending(dest)


def test_record_rollback_does_not_remove_a_competing_destination(recording_paths, monkeypatch):
    pair, dest = recording_paths
    before, identities = _prior(dest)
    rename = getattr(runner, "_rename_recording", lambda source, target: source.rename(target))

    def competing_publish(self, target):
        if _is_publish(self, target, dest):
            dest.mkdir()
            (dest / "competing.txt").write_bytes(b"Another writer's fixture.\n")
            raise OSError("Injected publication race.")
        return rename(self, target)

    monkeypatch.setattr(runner, "_rename_recording", competing_publish, raising=False)

    with pytest.raises(OSError, match="publication race") as raised:
        _record(pair)

    assert _files(dest) == {"competing.txt": b"Another writer's fixture.\n"}
    history = _histories(dest)
    assert len(history) == 1 and _files(history[0]) == before and _identities(history[0]) == identities
    assert any(str(history[0]) in note for note in raised.value.__notes__)
    assert not _pending(dest)


def test_record_restore_failure_retains_the_prior_fixture_and_reports_its_path(recording_paths, monkeypatch):
    pair, dest = recording_paths
    before, identities = _prior(dest)
    rename = getattr(runner, "_rename_recording", lambda source, target: source.rename(target))

    def failed_restore(self, target):
        if _is_publish(self, target, dest):
            raise OSError("Injected publication failure.")
        if self.name == "fixture" and self.parent.name.startswith(f".{dest.name}.previous-"):
            raise OSError("Injected restore failure.")
        return rename(self, target)

    monkeypatch.setattr(runner, "_rename_recording", failed_restore, raising=False)

    with pytest.raises(OSError, match="publication") as raised:
        _record(pair)

    history = _histories(dest)
    assert len(history) == 1 and _files(history[0]) == before and _identities(history[0]) == identities
    assert not dest.exists() and not _pending(dest)
    assert any(str(history[0]) in note and "restore failure" in note for note in raised.value.__notes__)


def test_record_success_retains_the_prior_fixture_without_rewriting_it(recording_paths):
    pair, dest = recording_paths
    before, identities = _prior(dest)
    original = _files(pair)

    assert _record(pair) == dest

    history = _histories(dest)
    assert len(history) == 1 and _files(history[0]) == before and _identities(history[0]) == identities
    assert transcript.load(dest, "codex-jsonl").final == "A clear reply."
    assert _files(pair) == original and not _pending(dest)


def test_record_success_retains_each_prior_version(recording_paths):
    pair, dest = recording_paths
    first, _ = _prior(dest)
    _record(pair)
    second = _files(dest)
    text = (pair / "transcript.jsonl").read_text(encoding="utf-8").replace("A clear reply.", "A revised reply.")
    (pair / "transcript.jsonl").write_text(text, encoding="utf-8")

    assert _record(pair) == dest

    versions = [_files(folder) for folder in _histories(dest)]
    assert len(versions) == 2 and first in versions and second in versions
    assert transcript.load(dest, "codex-jsonl").final == "A revised reply."
    assert not _pending(dest)


def test_record_publication_does_not_replace_a_competing_empty_directory(recording_paths, monkeypatch):
    pair, dest = recording_paths
    before, identities = _prior(dest)
    rename = getattr(runner, "_rename_recording", lambda source, target: source.rename(target))
    competing_inode = None

    def competing_publish(source, target):
        nonlocal competing_inode
        if _is_publish(source, target, dest):
            dest.mkdir()
            competing_inode = dest.stat().st_ino
        return rename(source, target)

    monkeypatch.setattr(runner, "_rename_recording", competing_publish, raising=False)

    with pytest.raises(FileExistsError) as raised:
        _record(pair)

    history = _histories(dest)
    assert dest.is_dir() and dest.stat().st_ino == competing_inode and _files(dest) == {}
    assert len(history) == 1 and _files(history[0]) == before and _identities(history[0]) == identities
    assert any(str(history[0]) in note for note in raised.value.__notes__)
    assert not _pending(dest)


def test_record_restore_does_not_replace_a_competing_empty_directory(recording_paths, monkeypatch):
    pair, dest = recording_paths
    before, identities = _prior(dest)
    rename = getattr(runner, "_rename_recording", lambda source, target: source.rename(target))
    competing_inode = None

    def competing_restore(source, target):
        nonlocal competing_inode
        if _is_publish(source, target, dest):
            raise OSError("Injected publication failure.")
        if source.name == "fixture" and source.parent.name.startswith(f".{dest.name}.previous-"):
            dest.mkdir()
            competing_inode = dest.stat().st_ino
        return rename(source, target)

    monkeypatch.setattr(runner, "_rename_recording", competing_restore, raising=False)

    with pytest.raises(OSError, match="publication") as raised:
        _record(pair)

    history = _histories(dest)
    assert dest.is_dir() and dest.stat().st_ino == competing_inode and _files(dest) == {}
    assert len(history) == 1 and _files(history[0]) == before and _identities(history[0]) == identities
    assert any(str(history[0]) in note for note in raised.value.__notes__)
    assert not _pending(dest)


def test_record_unsupported_safe_rename_leaves_the_prior_fixture_exact(recording_paths, monkeypatch):
    pair, dest = recording_paths
    before, identities = _prior(dest)
    monkeypatch.setattr(runner.sys, "platform", "unsupported-recording-host")

    with pytest.raises(OSError, match="exclusive directory rename"):
        _record(pair)

    assert _files(dest) == before and _identities(dest) == identities
    assert not _histories(dest) and not _pending(dest)
