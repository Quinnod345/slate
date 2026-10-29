"""Demucs must stay local by default and never accept old outputs after a failed process."""

from __future__ import annotations

import contextlib
import hashlib
import types
import wave
from pathlib import Path

import pytest

from slate.analyze import stems
from slate.errors import MissingPrerequisite


def wav_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 80)
    return path


@pytest.fixture
def setup(tmp_path, monkeypatch):
    wav = wav_file(tmp_path / "audio" / "mix.wav")
    out = tmp_path / "stems"
    checkpoint = tmp_path / "torch" / "hub" / "checkpoints" / stems.CHECKPOINT
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"model marker; no inference occurs in this test")
    monkeypatch.setenv("TORCH_HOME", str(tmp_path / "torch"))
    monkeypatch.setattr(stems.procs, "which", lambda tool: "/usr/bin/true")
    monkeypatch.setattr(stems, "_lock", contextlib.nullcontext)
    measured = []
    monkeypatch.setattr(stems, "_loudness", lambda path: measured.append(path) or {"integrated_lufs": -20.0})
    return wav, out, checkpoint, measured


def outputs(wav: Path, out: Path):
    folder = out / stems.MODEL / wav.stem
    return [wav_file(folder / "vocals.wav"), wav_file(folder / "no_vocals.wav")]


@pytest.mark.parametrize("returncode", [1, 2, -9])
def test_failed_process_cannot_accept_preexisting_stems(setup, returncode):
    wav, out, checkpoint, measured = setup
    old = outputs(wav, out)
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in old}
    result, data = stems.run(
        wav, out, duration_s=0.01,
        runner=lambda argv, **kwargs: types.SimpleNamespace(
            returncode=returncode, stdout="", stderr="separation failed"),
    )
    assert result.status == "UNCHECKED" and data == {}
    assert str(returncode) in result.summary
    assert measured == []
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in old} == before


@pytest.mark.parametrize("cache_mode", ["absolute", "relative", "xdg"])
def test_cached_checkpoint_uses_local_repository_without_remote_resolution(setup, monkeypatch, tmp_path, cache_mode):
    wav, out, checkpoint, measured = setup
    if cache_mode == "relative":
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("TORCH_HOME", "torch")
    elif cache_mode == "xdg":
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "other-cache"))
    seen = {}

    def backend(argv, **kwargs):
        if "--repo" not in argv:
            pytest.fail("Remote model resolution attempted despite default offline analysis")
        repo = Path(argv[argv.index("--repo") + 1])
        seen["repo"] = repo
        assert "--offline" in argv
        assert argv[argv.index("-n") + 1] == "htdemucs"
        assert (repo / "htdemucs.yaml").read_bytes() == b"models: ['955717e8']\n"
        alias = repo / stems.CHECKPOINT
        assert alias.is_symlink() and alias.resolve() == checkpoint.resolve()
        assert alias.read_bytes() == checkpoint.read_bytes()
        outputs(wav, out)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    result, data = stems.run(wav, out, duration_s=0.01, runner=backend)
    assert result.status == "PASS"
    assert data["vocals"] == "stems/htdemucs/mix/vocals.wav"
    assert len(measured) == 2
    assert not seen["repo"].exists()
    assert checkpoint.exists()


def test_cache_disappearance_fails_locally_and_preserves_old_stems(setup):
    wav, out, checkpoint, measured = setup
    old = outputs(wav, out)
    before = [path.read_bytes() for path in old]
    seen = {}

    def backend(argv, **kwargs):
        if "--repo" not in argv:
            pytest.fail("Remote fallback attempted after a cache race")
        repo = Path(argv[argv.index("--repo") + 1])
        seen["repo"] = repo
        checkpoint.unlink()
        assert not (repo / stems.CHECKPOINT).exists()
        return types.SimpleNamespace(returncode=1, stdout="", stderr="local model missing")

    result, data = stems.run(wav, out, duration_s=0.01, runner=backend)
    assert result.status == "UNCHECKED" and data == {}
    assert measured == [] and [path.read_bytes() for path in old] == before
    assert not seen["repo"].exists()


def test_missing_cache_refuses_before_starting_backend(setup):
    wav, out, checkpoint, measured = setup
    checkpoint.unlink()

    def backend(argv, **kwargs):
        pytest.fail("Backend launched without cached weights or download consent")

    result, data = stems.run(wav, out, duration_s=0.01, runner=backend)
    assert result.status == "UNCHECKED" and data == {}
    assert measured == [] and not out.exists()


def test_explicit_download_branch_retains_remote_loader(setup):
    wav, out, checkpoint, measured = setup
    checkpoint.unlink()

    def backend(argv, **kwargs):
        assert "--repo" not in argv and "--offline" not in argv
        outputs(wav, out)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    result, data = stems.run(wav, out, duration_s=0.01, allow_download=True, runner=backend)
    assert result.status == "PASS" and data["model"] == "htdemucs"


def test_success_without_complete_stems_is_unchecked(setup):
    wav, out, checkpoint, measured = setup
    result, data = stems.run(
        wav, out, duration_s=0.01,
        runner=lambda argv, **kwargs: types.SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    assert result.status == "UNCHECKED" and data == {} and measured == []


def test_backend_refusal_does_not_accept_old_stems(setup):
    wav, out, checkpoint, measured = setup
    old = outputs(wav, out)

    def backend(argv, **kwargs):
        raise MissingPrerequisite("model unavailable", hint="Provide the cached model.")

    result, data = stems.run(wav, out, duration_s=0.01, runner=backend)
    assert result.status == "UNCHECKED" and data == {}
    assert measured == [] and all(path.exists() for path in old)


def test_local_repository_cleanup_after_process_failure(setup):
    wav, out, checkpoint, measured = setup
    seen = {}

    def backend(argv, **kwargs):
        if "--repo" not in argv:
            pytest.fail("Offline backend lacks a local repository")
        seen["repo"] = Path(argv[argv.index("--repo") + 1])
        return types.SimpleNamespace(returncode=1, stdout="", stderr="failed")

    result, data = stems.run(wav, out, duration_s=0.01, runner=backend)
    assert result.status == "UNCHECKED" and data == {}
    assert not seen["repo"].exists() and checkpoint.exists()
