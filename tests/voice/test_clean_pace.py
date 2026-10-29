"""Clean-up and pacing: the floor is measured, pauses shorten from their middles, and Rubber Band's architecture is
recorded (arm64 is the target; an x86_64 build under Rosetta is flagged)."""

import stat
import struct
import sys

import numpy as np
import pytest

from slate import jsonio, procs
from slate.cli import run_cli
from slate.voice import audio, clean, pace
from slate.voice.film import VoiceFilm

SR = 48000


def tone(seconds, f=220.0, amp=0.3):
    t = np.arange(int(seconds * SR)) / SR
    return amp * np.sin(2 * np.pi * f * t)


def test_shorten_pauses_cuts_from_the_middle_and_keeps_edges():
    x = np.concatenate([np.zeros(int(0.3 * SR)), tone(0.5), np.zeros(int(1.0 * SR)), tone(0.5),
                        np.zeros(int(0.2 * SR)), tone(0.4), np.zeros(int(0.5 * SR))])
    y, cuts = pace.shorten_pauses(x, SR, pause_keep=0.8, min_pause=0.25, threshold_db=-45)
    assert len(cuts) == 1  # the 0.2 s pause is under min_pause; head and tail silences are never cut
    a, b = cuts[0]
    assert b - a == pytest.approx(0.2, abs=0.011)  # 1.0 s kept at 80 %
    assert 0.8 + 0.3 < a < 1.8 - 0.3  # from the middle of the pause, not its edges
    assert (len(x) - len(y)) / SR == pytest.approx(0.2, abs=0.011)
    assert pace.map_time(0.4, cuts, 1.0) == pytest.approx(0.4)
    assert pace.map_time(2.0, cuts, 1.0) == pytest.approx(1.8, abs=0.011)
    assert pace.map_time(2.0, cuts, 1.06) == pytest.approx(1.8 / 1.06, abs=0.011)
    with pytest.raises(Exception):
        pace.shorten_pauses(np.zeros(SR), SR)


def _macho(cpu: int) -> bytes:
    return struct.pack("<II", 0xFEEDFACF, cpu) + b"\0" * 24


def test_macho_architecture_detection():
    assert pace.macho_archs(_macho(0x0100000C)) == ["arm64"]
    assert pace.macho_archs(_macho(0x01000007)) == ["x86_64"]
    fat = struct.pack(">II", 0xCAFEBABE, 2) + struct.pack(">IIIII", 0x01000007, 3, 0, 0, 0) \
        + struct.pack(">IIIII", 0x0100000C, 0, 0, 0, 0)
    assert pace.macho_archs(fat) == ["x86_64", "arm64"]
    assert pace.macho_archs(b"#!/bin/sh\n") == []


def _fake_binary(folder, name, header: bytes):
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(header)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def test_rosetta_is_flagged_and_native_is_ok(tmp_path):
    x86 = _fake_binary(tmp_path / "x86", "rubberband", _macho(0x01000007))
    info = pace.rubberband_info(str(x86), host="arm64")
    assert info["arch"] == ["x86_64"] and info["rosetta"] and info["status"] == "rosetta"
    assert "Rosetta" in info["note"] and "arm64 is the target" in info["note"]
    arm = _fake_binary(tmp_path / "arm", "rubberband", _macho(0x0100000C))
    info = pace.rubberband_info(str(arm), host="arm64")
    assert info["native"] and info["status"] == "ok"
    assert pace.rubberband_info(None, host="arm64")["status"] in ("missing", "ok", "rosetta", "foreign")


FAKE_RUBBERBAND = r'''#!{python}
"""A stand-in Rubber Band for tests: prints a version, or copies its input to its output."""
import shutil, sys
if "--version" in sys.argv:
    print("9.9.9"); sys.exit(0)
shutil.copyfile(sys.argv[-2], sys.argv[-1])
'''


@pytest.fixture
def fake_rubberband(tmp_path, monkeypatch):
    folder = tmp_path / "rb"
    folder.mkdir()
    exe = folder / "rubberband"
    exe.write_text(FAKE_RUBBERBAND.replace("{python}", sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{folder}:{__import__('os').environ['PATH']}")
    # Slate puts Homebrew's folders ahead of the caller's PATH (the arm64 Rubber Band), so on a Mac that has the real
    # one the stand-in has to lead the preferred folders too.
    monkeypatch.setattr(procs, "PREFERRED_PATH", [str(folder), *procs.PREFERRED_PATH])
    return exe


def _voiced(voice_film):
    code, out, err = run_cli(["voice", "tts", "--film", str(voice_film), "--only", "S02"])
    assert code == 0, err
    return VoiceFilm(voice_film)


def test_pace_take_records_the_stage_and_the_binary(voice_film, fake_rubberband):
    vf = _voiced(voice_film)
    res = pace.pace_take(vf, "S02", tempo=1.06)
    assert res["file"] == "voice/takes/S02/t01.paced.wav"
    assert res["rubberband"]["path"] == str(fake_rubberband) and res["rubberband"]["version"] == "9.9.9"
    x, sr = audio.read_wav(voice_film / res["file"])
    assert sr == 48000
    stage = VoiceFilm(voice_film).take_json("S02", "t01")["stages"]["pace"]
    assert stage["tempo"] == 1.06 and stage["rubberband"]["path"] == str(fake_rubberband)
    assert VoiceFilm(voice_film).final_stage("S02") == "paced"


def test_pace_without_rubber_band_refuses_unless_pauses_only(voice_film, monkeypatch):
    vf = _voiced(voice_film)
    monkeypatch.setattr(pace, "rubberband_info", lambda *a, **k: {"path": None, "status": "missing",
                                                                   "machine": "arm64", "note": "not installed"})
    with pytest.raises(Exception) as exc:
        pace.pace_take(vf, "S02", tempo=1.06)
    assert getattr(exc.value, "exit_code", None) == 3
    res = pace.pace_take(vf, "S02", no_tempo=True)
    assert res["tempo"] == 1.0 and res["after_s"] <= res["before_s"]


def test_pace_cli_flags_rosetta(voice_film, fake_rubberband, monkeypatch):
    _voiced(voice_film)
    real = pace.rubberband_info

    def rosetta(*a, **k):
        info = real(*a, **k)
        info.update({"arch": ["x86_64"], "rosetta": True, "native": False, "status": "rosetta",
                     "note": "x86_64 build running under Rosetta: it works, but arm64 is the target"})
        return info

    monkeypatch.setattr(pace, "rubberband_info", rosetta)
    code, out, err = run_cli(["voice", "pace", "--film", str(voice_film), "--only", "S02"])
    assert code == 0 and "Rosetta" in out
    code, out, _ = run_cli(["voice", "pace", "--check-tools"])
    assert code == 1 and "Rosetta" in out


def test_diagnose_and_chain_track_the_floor(tmp_path):
    rng = np.random.default_rng(1)
    x = np.concatenate([tone(0.6), np.zeros(int(0.4 * SR)), tone(0.6)]) + rng.normal(0, 10 ** (-60 / 20),
                                                                                        int(1.6 * SR))
    audio.write_wav(tmp_path / "n.wav", x, SR)
    d = clean.diagnose(tmp_path / "n.wav")
    assert -66 < d["gap_floor_dbfs"] < -54 and d["speech_dbfs"] > -20
    assert "faint floor" in d["verdict"]
    chain = clean.chain_for({}, d["gap_floor_dbfs"])
    assert chain.startswith("afftdn=nr=18:nf=-6") and "agate" in chain
    assert "nf=-64" in clean.chain_for({}, None)
    assert "nf=-80" in clean.chain_for({}, float("-inf"))
    assert clean.chain_for({"clean": {"chain": "highpass=f=60"}}, -60) == "highpass=f=60"
    audio.write_wav(tmp_path / "z.wav", np.concatenate([tone(0.5), np.zeros(SR)]), SR)
    assert clean.diagnose(tmp_path / "z.wav")["gap_floor_dbfs"] <= -150  # digital silence: no NaN


@pytest.mark.skipif(not procs.which("ffmpeg"), reason="ffmpeg isn't installed")
def test_clean_take_runs_the_chain_and_records_it(voice_film):
    vf = _voiced(voice_film)
    res = clean.clean_take(vf, "S02")
    assert res["file"] == "voice/takes/S02/t01.clean.wav" and "afftdn" in res["chain"]
    x, sr = audio.read_wav(voice_film / res["file"])
    assert sr == 48000
    stage = VoiceFilm(voice_film).take_json("S02", "t01")["stages"]["clean"]
    assert stage["method"] == "dsp" and stage["sha256"] == jsonio.sha256(voice_film / res["file"])
    assert VoiceFilm(voice_film).final_stage("S02") == "clean"


def test_clean_with_the_isolator_in_replay(voice_film):
    vf = _voiced(voice_film)
    res = clean.clean_take(vf, "S02", method="isolator")
    assert res["method"] == "isolator" and (voice_film / res["file"]).is_file()
    from slate import ledger
    assert any(r["op"] == "isolate" for r in ledger.rows())


def test_remix_previews_are_measured(voice_film):
    from slate.vendors.elevenlabs import Client

    vf = _voiced(voice_film)
    report = clean.remix(vf, voice_id="demo-narrator-01", text="Every film starts with one line. " * 4,
                         runs=((0.25, 11),), out_dir=voice_film / "voice" / "clean" / "remix", client=Client())
    assert len(report["remixes"]) == 3 and all("gap_floor_dbfs" in r for r in report["remixes"])
    assert (voice_film / "voice" / "clean" / "remix" / "remix.json").is_file()
    with pytest.raises(Exception):
        clean.remix(vf, voice_id="v", text="too short", out_dir=voice_film / "x", client=Client())
