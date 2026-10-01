"""The film-state lessons from knowledge/GAPS.md, each with its proof:

- L-method-002: supplied inputs are copied into the film, SHA-256-verified, recorded with their origin, and never
  silently replaced (`slate film input`);
- L-method-011: every autonomy grant is recorded with its scope in the owner's words, and `slate status` lists it
  (`slate approve grant --scope …`);
- L-render-026: a film that has to live in iCloud Drive can keep its heavy folders out of sync, with the person's yes
  (`slate film icloud --yes`);
- L-sound-D018: a palette change makes the mix stale (`slate make`'s mix node reads the brand's sound block and the
  effect cues, through lock.hash_inputs).
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from slate import jsonio
from slate.cli import run_cli
from slate.errors import Refused, UsageError
from slate.state import approvals, film, inputs, lock, make, production, scaffold, status


def _run(*argv: str) -> tuple[int, dict | str, str]:
    code, out, err = run_cli([*argv, "--json"])
    try:
        return code, json.loads(out.strip().splitlines()[-1]), err
    except (ValueError, IndexError):
        return code, out, err


# ---------------------------------------------------------------------------------------------------------------
# L-method-002: supplied inputs


def test_a_supplied_input_is_copied_verified_recorded_and_never_replaced(new_film, tmp_path, monkeypatch):
    fd = new_film("inputs")
    src = tmp_path / "Downloads" / "reference cut.mov"
    src.parent.mkdir()
    src.write_bytes(b"the original film, as supplied")
    code, out, err = _run("film", "input", str(src), "--source", "Test Person", "--film", str(fd))
    assert code == 0, err
    rel = "brief/inputs/reference cut.mov"
    assert out["path"] == rel and out["sha256"] == jsonio.sha256(src) and not out["already"]
    assert (fd / rel).read_bytes() == src.read_bytes()
    e = lock.entry(fd, rel)
    assert e["producer"] == "slate film input" and e["sha256"] == out["sha256"] and e["locked"]
    assert "supplied by Test Person" in e["note"] and str(src.resolve()) in e["note"]
    assert lock.explain(fd, rel)["state"] == "locked"
    # the source changing later changes nothing: tools read the film's copy
    src.write_bytes(b"a newer export")
    assert lock.explain(fd, rel)["state"] == "locked"
    code, out, err = _run("film", "input", str(src), "--source", "Test Person", "--film", str(fd))
    assert code == 1 and "never replaces a supplied input" in out["message"] and "--as" in out["hint"]
    assert (fd / rel).read_bytes() == b"the original film, as supplied"
    code, out, _ = _run("film", "input", str(src), "--as", "reference cut v2.mov", "--film", str(fd))
    assert code == 0 and out["path"] == "brief/inputs/reference cut v2.mov"
    first = tmp_path / "Downloads" / "logo.svg"
    first.write_text("<svg/>")
    assert inputs.add(fd, first)["already"] is False and inputs.add(fd, first)["already"] is True  # same bytes: kept
    listed = {i["path"]: i for i in inputs.listing(fd)}
    assert set(listed) == {rel, "brief/inputs/reference cut v2.mov", "brief/inputs/logo.svg"}
    assert all(i["intact"] for i in listed.values())
    code, out, _ = _run("film", "show", "--film", str(fd))
    assert {i["path"] for i in out["inputs"]} == set(listed)
    with pytest.raises(UsageError):
        inputs.add(fd, tmp_path / "Downloads")  # one file at a time
    with pytest.raises(UsageError):
        inputs.add(fd, first, as_name="../escape.svg")
    # a copy that doesn't match its source is never kept
    bad = tmp_path / "Downloads" / "still syncing.mov"
    bad.write_bytes(b"complete bytes")
    monkeypatch.setattr(shutil, "copyfile", lambda a, b: Path(b).write_bytes(b"half"))
    with pytest.raises(Refused, match="doesn't match its source"):
        inputs.add(fd, bad)
    assert not (fd / "brief/inputs/still syncing.mov").exists()


# ---------------------------------------------------------------------------------------------------------------
# L-method-011: autonomy grants


def test_an_autonomy_grant_is_recorded_with_its_scope_and_listed(new_film):
    fd = new_film("grants")
    code, out, err = _run("approve", "grant", "--scope", "creative decisions on act 3", "--by", "Test Person",
                          "--film", str(fd))
    assert code == 0, err
    assert out["what"] == "grant" and out["kind"] == "grant" and out["note"] == "creative decisions on act 3"
    assert out["path"] == "brief/grants/g1.txt" and not out["blocking"]
    assert (fd / out["path"]).read_text() == "creative decisions on act 3\n"
    assert out["sha256"] == jsonio.sha256(fd / out["path"])  # the approval covers exactly those words
    approvals.grant(fd, scope="pick the score take", approved_by="Test Person")
    grants = approvals.grants(fd)
    assert [g["scope"] for g in grants] == ["creative decisions on act 3", "pick the score take"]
    assert all(g["standing"] and g["by"] == "Test Person" for g in grants)
    st = status.compute(fd)
    assert [g["scope"] for g in st["grants"]] == ["creative decisions on act 3", "pick the score take"]
    human = status.format_human(st)
    assert "“creative decisions on act 3” (by Test Person" in human and "“pick the score take”" in human
    assert "grant" in (fd / production.PRODUCTION).read_text()  # the tracker's approvals table carries it
    (fd / "brief/grants/g1.txt").write_text("every decision\n")  # someone widened the words after the fact
    assert not approvals.grants(fd)[0]["standing"]
    assert "its words changed since it was granted" in status.format_human(status.compute(fd))
    code, out, _ = _run("approve", "grant", "--by", "Test Person", "--film", str(fd))
    assert code == 2 and "--scope" in out["message"]
    code, out, _ = _run("approve", "grant", "brief/GOAL.md", "--by", "Test Person", "--film", str(fd))
    assert code == 2
    code, out, _ = _run("approve", "script", "--by", "Test Person", "--film", str(fd))
    assert code == 2 and "Name the file" in out["message"]  # every other approval still names its file


# ---------------------------------------------------------------------------------------------------------------
# L-render-026: iCloud Drive


def test_a_film_in_icloud_keeps_its_heavy_folders_out_of_sync_with_a_yes(new_film, state_env, monkeypatch):
    icloud = state_env["tmp"] / "Mobile Documents"
    icloud.mkdir()
    monkeypatch.setattr(film, "icloud_roots", lambda: [icloud])
    fd = new_film("cloudy", films_dir=icloud / "films")
    (fd / "plates").mkdir()
    marked: dict[str, list[str]] = {}

    def xattr(args):
        if args[0] == "-p":
            return (0, "1") if args[-1] in marked.get("-w", []) else (1, "No such xattr")
        marked.setdefault(args[0], []).append(args[-1])
        assert args[1] == film.ICLOUD_IGNORE_ATTR and args[2] == "1"
        return 0, ""

    monkeypatch.setattr(film, "_xattr", xattr)
    code, out, _ = _run("film", "icloud", "--film", str(fd))
    assert code == 0 and out["icloud"] == str(icloud) and out["applied"] == [] and "-w" not in marked
    assert set(out["would"]) == {"renders", "masters", "captures", "plates", "archive"}  # nothing without a yes
    code, out, _ = _run("film", "icloud", "--yes", "--film", str(fd))
    assert code == 0 and set(out["applied"]) == {"renders", "masters", "captures", "plates", "archive"}
    assert sorted(Path(p).name for p in marked["-w"]) == sorted(out["applied"])
    assert "xattr -d" in out["undo"]
    code, out, _ = _run("film", "icloud", "--yes", "--film", str(fd))
    assert out["applied"] == [] and len(out["already"]) == 5  # asked again: nothing to do
    away = new_film("dry-land")
    assert film.icloud_protect(away, yes=True)["icloud"] is None


@pytest.mark.skipif(not Path("/usr/bin/xattr").exists(), reason="macOS's xattr isn't here")
def test_the_ignore_attribute_really_lands_on_this_mac(new_film, state_env, monkeypatch):
    icloud = state_env["tmp"] / "Mobile Documents"
    icloud.mkdir()
    monkeypatch.setattr(film, "icloud_roots", lambda: [icloud])
    fd = new_film("cloudy-real", films_dir=icloud / "films")
    out = film.icloud_protect(fd, yes=True)
    assert "renders" in out["applied"]
    code, value = film._xattr(["-p", film.ICLOUD_IGNORE_ATTR, str(fd / "renders")])
    assert code == 0 and value == "1"


def test_new_films_ignore_dotenv_files():
    assert "\n.env\n" in scaffold.GITIGNORE  # the ElevenLabs CLI loads a .env from the folder it runs in


# ---------------------------------------------------------------------------------------------------------------
# L-sound-D018: a palette change makes the mix stale


def _mixed(new_film, state_env) -> tuple[Path, Path]:
    fd = new_film("palette", brand="onboarded-brand", format="fixture-reel")
    mix = fd / "sound" / "mix" / "final_mix.wav"
    mix.parent.mkdir(parents=True, exist_ok=True)
    mix.write_bytes(b"RIFF a mix")
    lock.record(fd, "sound/mix/final_mix.wav", producer="slate mix", inputs=[])  # what `slate mix` records
    return fd, state_env["brands"] / "onboarded-brand" / "brand.json"


def _edit_brand(path: Path, change) -> None:
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data, indent=1))


def test_a_palette_change_makes_the_mix_stale(new_film, state_env):
    fd, brand = _mixed(new_film, state_env)
    node = make.NODES["mix"]
    assert "timing/vo_cues.json" in node.inputs  # what `slate mix` records itself
    assert make.declared_inputs(fd, node) == ["sound/sound_cues.json", "brand:onboarded-brand#sound"]  # make tracks
    assert make.node_state(fd, node)["state"] == "fresh"
    assert make.adopt_inputs(fd, node) == ["sound/mix/final_mix.wav"]  # fresh against them: hash them from now on
    recorded = lock.entry(fd, "sound/mix/final_mix.wav")["inputs"]
    assert {"brand:onboarded-brand#sound", "sound/sound_cues.json"} <= set(recorded)
    _edit_brand(brand, lambda d: d.update(note="a new note, nothing to do with sound"))
    assert make.node_state(fd, node)["state"] == "fresh"  # only the sound block counts
    _edit_brand(brand, lambda d: d.update(sound={"palette": {"ui": "hollow wood clicks"}}))
    st = make.node_state(fd, node)
    assert st["state"] == "stale" and "brand:onboarded-brand#sound changed" in st["reasons"][0]


def test_new_effect_cues_make_the_mix_stale(new_film, state_env):
    fd, _brand = _mixed(new_film, state_env)
    node = make.NODES["mix"]
    make.adopt_inputs(fd, node)
    cues = fd / "sound" / "sound_cues.json"
    cues.write_text(json.dumps({"cues": []}))
    st = make.node_state(fd, node)
    assert st["state"] == "stale" and "sound/sound_cues.json" in st["reasons"][0]


def test_before_make_has_seen_it_a_newer_palette_counts_as_a_change(new_film, state_env):
    fd, brand = _mixed(new_film, state_env)
    node = make.NODES["mix"]
    later = time.time() + 5
    os.utime(brand, (later, later))  # the pack changed after the mix was made, before make ever looked
    st = make.node_state(fd, node)
    assert st["state"] == "stale" and "the brand pack's sound changed after it was made" in st["reasons"][0]


def test_a_brand_block_is_a_named_input_of_its_own(new_film, state_env):
    fd, brand = _mixed(new_film, state_env)
    before = lock.hash_inputs(fd, ["brand:onboarded-brand#sound", "brand:onboarded-brand"])
    _edit_brand(brand, lambda d: d.update(note="changed"))
    after = lock.hash_inputs(fd, ["brand:onboarded-brand#sound", "brand:onboarded-brand"])
    assert after["brand:onboarded-brand#sound"] == before["brand:onboarded-brand#sound"]  # no sound block, still none
    assert after["brand:onboarded-brand"] != before["brand:onboarded-brand"]  # the whole pack did change
    assert lock.hash_inputs(fd, ["brand:no-such-brand#sound"]) == {"brand:no-such-brand#sound": None}
