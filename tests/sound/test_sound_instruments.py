"""Tonal roles on Apple's installed sampled instruments: found in place, reported, never faked, never copied."""

import json
import os
import struct
from pathlib import Path

import pytest

from slate.cli import run_cli
from slate.errors import MissingPrerequisite
from slate.sound import instruments
from slate.sound.effects.palette import STARTING_PALETTE


def _chunk(ctype: int, name: str, content: bytes) -> bytes:
    return struct.pack("<IIII4s64s", (ctype << 24) | 0x0101, len(content), 0, 0, b"TBOS",
                       name.encode().ljust(64, b"\x00")) + content


def write_exs(path: Path, sample_folder: str, files: list[str]) -> Path:
    """A minimal Logic Sampler zone map: a header, one group, one zone and one sample chunk per file."""
    parts = [_chunk(0, "Instrument", b"\x00" * 16), _chunk(2, "soft", bytes([0, 0, 0, 0, 0, 0, 127, 0]))]
    for i, f in enumerate(files):
        zone = bytearray(96)
        zone[1], zone[6], zone[7], zone[9], zone[10] = 60 + i, 60 + i, 60 + i, 0, 127
        struct.pack_into("<I", zone, 88, 0)
        struct.pack_into("<I", zone, 92, i)
        parts.append(_chunk(1, f"zone {i}", bytes(zone)))
    for f in files:
        sample = bytearray(592)
        struct.pack_into("<III", sample, 4, 48000, 44100, 24)
        sample[80:80 + len(sample_folder)] = sample_folder.encode()
        sample[336:336 + len(f)] = f.encode()
        parts.append(_chunk(3, f, bytes(sample)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(parts))
    return path


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / "Library"
    monkeypatch.setenv("SLATE_APPLE_LIBRARY", str(root))
    return root


def _install(root: Path, exs_rel: str, folder_name: str, files: list[str], make_samples: bool = True):
    folder = f"/Library/Application Support/GarageBand/Instrument Library/Sampler/Sampler Files/{folder_name}"
    write_exs(root / "Application Support/Logic/Sampler Instruments" / exs_rel, folder, files)
    real = root / "Application Support/GarageBand/Instrument Library/Sampler/Sampler Files" / folder_name
    real.mkdir(parents=True, exist_ok=True)
    if make_samples:
        for f in files:
            (real / f).write_bytes(b"not really audio; only its presence matters here")
    return real


def test_parser_reads_a_zone_map(tmp_path):
    p = write_exs(tmp_path / "x.exs", "/some/folder", ["a.aif", "b.aif"])
    zones, groups, samples = instruments.parse_exs(p)
    assert [z["root"] for z in zones] == [60, 61]
    assert [s["file"] for s in samples] == ["a.aif", "b.aif"] and samples[0]["folder"] == "/some/folder"
    assert groups[0]["name"] == "soft"


def test_a_missing_instrument_is_reported_with_what_to_download(library, slate_home):
    _install(library, "10 World/01 African/African Marimba.exs", "African Marimba", ["m1.aif", "m2.aif"])
    rep = instruments.report()
    by = {r["role"]: r for r in rep["roles"]}
    assert by["felt_note"]["status"] == "ok" and by["felt_note"]["samples"] == 2
    assert by["pad_low"]["status"] == "missing" and "Sound Library" in by["pad_low"]["fix"]
    assert not rep["ok"] and "GarageBand" in rep["download"]
    code, out, _ = run_cli(["sound", "instruments", "--json"])
    assert code == 3 and json.loads(out)["ok"] is False


def test_missing_samples_make_an_instrument_incomplete(library, slate_home):
    real = _install(library, "10 World/01 African/African Marimba.exs", "African Marimba", ["m1.aif", "m2.aif"])
    (real / "m2.aif").unlink()
    r = instruments.check_role("felt_note", STARTING_PALETTE["instruments"]["felt_note"])
    assert r["status"] == "incomplete" and r["missing_samples"] == 1


def test_there_is_no_synthesized_stand_in(library, slate_home):
    _install(library, "10 World/01 African/African Marimba.exs", "African Marimba", ["m1.aif"])
    assert instruments.require("felt_note")["status"] == "ok"
    with pytest.raises(MissingPrerequisite) as exc:
        instruments.require("pad_mid")
    assert "won't synthesize" in exc.value.message and exc.value.exit_code == 3


def test_everything_installed_passes_and_nothing_is_copied(library, slate_home):
    for role, spec in STARTING_PALETTE["instruments"].items():
        _install(library, spec["exs"], role, [f"{role}.aif"])
    before = sorted(str(p) for p in slate_home["tmp"].rglob("*"))
    rep = instruments.report()
    assert rep["ok"] and rep["download"] is None
    assert sorted(str(p) for p in slate_home["tmp"].rglob("*")) == before  # read in place: nothing written or copied
    code, _, _ = run_cli(["sound", "instruments"])
    assert code == 0
    rows = instruments.doctor_check()
    assert {r["status"] for r in rows} == {"green"} and all(r["id"].startswith("sound.instrument.") for r in rows)


def test_this_macs_own_library_is_read_in_place(monkeypatch):
    monkeypatch.delenv("SLATE_APPLE_LIBRARY", raising=False)
    base = Path("/Library/Application Support/Logic/Sampler Instruments")
    if not base.is_dir():
        pytest.skip("no Logic Sampler library on this machine")
    rep = instruments.report()
    present = all((base / spec["exs"]).is_file() for spec in STARTING_PALETTE["instruments"].values())
    assert rep["ok"] == present or not present
    for r in rep["roles"]:
        assert r["status"] in ("ok", "missing", "incomplete") and r["path"].startswith(str(base))


def test_the_brand_palette_names_its_own_instruments(library, slate_home):
    palette = {"instruments": {"motif": {"library": "logic-sampler", "exs": "Custom/Kalimba.exs"}}}
    rep = instruments.report(palette)
    assert [r["role"] for r in rep["roles"]] == ["motif"] and rep["roles"][0]["status"] == "missing"
    assert os.environ["SLATE_APPLE_LIBRARY"] in rep["library_base"]
