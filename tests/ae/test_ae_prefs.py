"""slate.ae.prefs: the byte-safe AE preferences tool, exercised only on temporary copies.

Covers every rule the film pipeline's ae_pref.py enforced (byte-level, one key, CR endings kept, the protected
scripting key, damaged files, missing keys, unchanged values, AE/aerender running, proofs, backups, atomic writes and
re-reads) plus the restore planner and the disk-cache toggle masters use.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from slate import procs
from slate.ae import prefs
from slate.errors import Busy, MissingPrerequisite, Refused, SlateError, UsageError

from ae_helpers import prefs_bytes, prefs_lines


@pytest.fixture
def copy(tmp_path) -> Path:
    p = tmp_path / "copy Prefs.txt"
    p.write_bytes(prefs_bytes())
    return p


# ---- reading -------------------------------------------------------------------------------------------------


def test_check_reports_endings_scripting_and_cache(copy):
    info = prefs.check(copy)
    assert info["lf"] == 0 and info["cr"] == copy.read_bytes().count(b"\r")
    assert info["ok"] and not info["damaged"]
    assert info["scripting"] == "on" and info["scripting_on"] is True
    assert info["disk_cache"] == {"enabled": "1", "folder": "~/Library/Caches", "max_gb": "92", "on": True}
    assert info["live"] is False and info["running"] == []


@pytest.mark.parametrize("raw,word,on", [(b'"0"', "off", False), (b"01", "on", True), (b"00", "off", False)])
def test_check_reads_the_scripting_setting_in_any_style(tmp_path, raw, word, on):
    p = tmp_path / "p.txt"
    p.write_bytes(prefs_bytes(scripting=raw))
    info = prefs.check(p)
    assert info["scripting"] == word and info["scripting_on"] is on


def test_check_without_the_scripting_key_says_not_set(tmp_path):
    lines = [ln for ln in prefs_lines() if b"SCRIPTING" not in ln]
    p = tmp_path / "p.txt"
    p.write_bytes(b"\r".join(lines))
    assert prefs.check(p)["scripting"] == "not set (off)"


def test_get_values_and_missing_key(copy):
    assert prefs.get("Disk Cache Controls", "Enabled 2", file=copy) == "1"
    assert prefs.get("Main Pref Section v2", "Some Bare Number", file=copy) == "01"
    with pytest.raises(Refused) as exc:
        prefs.get("Disk Cache Controls", "No Such Key", file=copy)
    assert exc.value.exit_code == 1


def test_find_handles_a_section_on_the_first_line():
    data = b'["First"]\r\t"a" = "1"\r\r["Second"]\r\t"a" = "2"'
    assert prefs.value_of(data, "First", "a") == "1"
    assert prefs.value_of(data, "Second", "a") == "2"
    assert prefs.value_of(data, "Third", "a") is None


def test_find_refuses_duplicates_and_wrapped_values(copy):
    dup = b'["S"]\r\t"k" = "1"\r\t"k" = "2"'
    with pytest.raises(Refused, match="more than once"):
        prefs.find(dup, "S", "k")
    with pytest.raises(Refused, match="wraps"):
        prefs.find(copy.read_bytes(), "Main Pref Section v2", "Wrapped Value")


# ---- set: one key, bytes only --------------------------------------------------------------------------------


def test_set_changes_exactly_one_value_and_keeps_cr(copy):
    before = copy.read_bytes()
    res = prefs.set("Disk Cache Controls", "Enabled 2", "0", file=copy)
    after = copy.read_bytes()
    assert res["changed"] and res["old"] == '"1"' and res["new"] == '"0"'
    assert after.count(b"\r") == before.count(b"\r") and b"\n" not in after
    assert len(after) == len(before)
    diffs = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(diffs) == 1 and after[diffs[0]:diffs[0] + 1] == b"0"
    assert prefs.value_of(after, *prefs.SCRIPTING) == prefs.value_of(before, *prefs.SCRIPTING)


def test_set_keeps_the_quoting_style(copy):
    prefs.set("Main Pref Section v2", "Some Bare Number", "02", file=copy)
    assert b'\t"Some Bare Number" = 02\r' in copy.read_bytes()
    prefs.set("Concurrent Frame Rendering", "Reserved CPU Power", "10", file=copy)
    assert b'\t"Reserved CPU Power" = "10"\r' in copy.read_bytes()


def test_set_with_the_same_value_writes_nothing(copy):
    stat = copy.stat()
    res = prefs.set("Disk Cache Controls", "Enabled 2", "1", file=copy)
    assert res["changed"] is False
    assert copy.stat().st_mtime_ns == stat.st_mtime_ns


def test_set_refuses_the_scripting_security_setting(copy):
    before = copy.read_bytes()
    with pytest.raises(Refused, match="security setting") as exc:
        prefs.set(*prefs.SCRIPTING, "0", file=copy)
    assert exc.value.exit_code == 1
    assert copy.read_bytes() == before


def test_set_refuses_a_damaged_lf_file(tmp_path):
    p = tmp_path / "lf.txt"
    p.write_bytes(prefs_bytes().replace(b"\r", b"\n"))
    before = p.read_bytes()
    with pytest.raises(Refused, match="LF line endings"):
        prefs.set("Disk Cache Controls", "Enabled 2", "0", file=p)
    assert p.read_bytes() == before
    assert prefs.check(p)["damaged"] is True and prefs.check(p)["ok"] is False


def test_set_refuses_a_missing_key(copy):
    before = copy.read_bytes()
    with pytest.raises(Refused, match="existing settings only"):
        prefs.set("Disk Cache Controls", "Enabled 3", "0", file=copy)
    with pytest.raises(Refused):
        prefs.set("No Such Section", "Enabled 2", "0", file=copy)
    assert copy.read_bytes() == before


def test_set_refuses_values_that_would_break_the_file(copy):
    for bad in ('a"b', "a\rb", "a\nb", "café"):
        with pytest.raises(UsageError):
            prefs.set("Disk Cache Controls", "Enabled 2", bad, file=copy)


def test_set_leaves_no_temp_file(copy):
    prefs.set("Disk Cache Controls", "Enabled 2", "0", file=copy)
    assert [p.name for p in copy.parent.iterdir() if p.name.endswith(".tmp")] == []


def test_set_on_a_copy_ignores_running_ae_and_makes_no_backup(copy, slate_home, monkeypatch):
    monkeypatch.setattr(procs, "ae_running", lambda: True)
    res = prefs.set("Disk Cache Controls", "Enabled 2", "0", file=copy)
    assert res["changed"] and res["backup"] is None
    assert not (slate_home["data"] / "ae-prefs-backups").exists()


# ---- the live file (a stand-in under SLATE_AE_PREFS_ROOT) --------------------------------------------------


def test_live_prefs_picks_the_newest_version_numerically(live_prefs):
    live_prefs("9.0")
    live_prefs("26.5")
    newest = live_prefs("26.10")
    assert prefs.live_prefs() == newest
    assert [d.name for d in prefs.version_dirs()] == ["26.10", "26.5", "9.0"]


def test_live_prefs_missing_is_a_missing_prerequisite():
    with pytest.raises(MissingPrerequisite) as exc:
        prefs.live_prefs()
    assert exc.value.exit_code == 3


def test_live_variant_file(live_prefs, ae_isolation):
    live_prefs("26.5")
    variant = ae_isolation["prefs_root"] / "26.5" / "Adobe After Effects 26.5 Prefs-indep-output.txt"
    variant.write_bytes(prefs_bytes())
    assert prefs.live_prefs(variant="indep-output") == variant


def test_live_set_is_busy_while_after_effects_runs(live_prefs, monkeypatch):
    f = live_prefs()
    before = f.read_bytes()
    monkeypatch.setattr(procs, "ae_running", lambda: True)
    with pytest.raises(Busy) as exc:
        prefs.set("Disk Cache Controls", "Enabled 2", "0")
    assert exc.value.exit_code == 5
    assert "save your work in After Effects and quit it, then run this again" in exc.value.message
    assert f.read_bytes() == before


def test_live_set_is_busy_while_aerender_runs(live_prefs, monkeypatch):
    live_prefs()
    monkeypatch.setattr(procs, "aerender_running", lambda: True)
    with pytest.raises(Busy):
        prefs.set("Disk Cache Controls", "Enabled 2", "0")


def test_live_check_reports_what_is_running(live_prefs, monkeypatch):
    live_prefs()
    monkeypatch.setattr(procs, "ae_running", lambda: True)
    info = prefs.check()
    assert info["live"] is True and info["running"] == ["After Effects"]


def test_live_set_backs_up_with_a_timestamp_and_rereads(live_prefs, slate_home):
    f = live_prefs()
    before = f.read_bytes()
    res = prefs.set("Disk Cache Controls", "Enabled 2", "0")
    backup = Path(res["backup"])
    assert backup.parent == slate_home["data"] / "ae-prefs-backups"
    assert backup.read_bytes() == before
    assert backup.name.endswith(f.name) and backup.name[:8].isdigit()
    # a second edit in the same second keeps both backups
    res2 = prefs.set("Disk Cache Controls", "Enabled 2", "1")
    assert Path(res2["backup"]).exists() and Path(res2["backup"]) != backup
    assert f.read_bytes() == before


def test_the_scripting_value_survives_every_edit(live_prefs):
    f = live_prefs()
    sc = prefs.value_of(f.read_bytes(), *prefs.SCRIPTING)
    for v in ("0", "1", "0", "1"):
        prefs.set("Disk Cache Controls", "Enabled 2", v)
    prefs.set("Concurrent Frame Rendering", "Reserved CPU Power", "40")
    assert prefs.value_of(f.read_bytes(), *prefs.SCRIPTING) == sc


def test_env_file_override_counts_as_live(tmp_path, monkeypatch):
    p = tmp_path / "stand-in.txt"
    p.write_bytes(prefs_bytes())
    monkeypatch.setenv("SLATE_AE_PREFS_FILE", str(p))
    path, live = prefs.resolve()
    assert path == p and live is True


# ---- restore -------------------------------------------------------------------------------------------------


def _reset_file() -> bytes:
    """What AE writes after a reset: defaults back, many entries gone, the report section still there."""
    lines = [
        b"", b"# Synthetic After Effects preferences after a reset", b"",
        b'["Coachmarks Settings"]',
        b'\t"Enabled" = "1"',
        b"",
        b'["Disk Cache Controls"]',
        b'\t"Enabled 2" = "0"',
        b"",
        b'["Main Pref Section v2"]',
        b'\t"Pref_LAST_DATE_TIME" = "2026-09-30 09:00:00"',
        b'\t"Pref_SCRIPTING_FILE_NETWORK_SECURITY" = "0"',
        b"",
        b'["Most Recently Used (MRU) Search v2"]',
        b'\t"MRU Project Path ID # 0, File Path" = "/Users/someone/new.aep"',
        b"",
    ]
    return b"\r".join(lines)


def _old_file() -> bytes:
    report = [b'\t"run.id" = "r1"', b'\t"run.status" = "OK"']
    lines = prefs_lines(report=report, extra=[b'["Only In Old"]', b'\t"x" = "1"', b""])
    mru = lines.index(b'\t"MRU Project Path ID # 0, File Path" = "/Users/someone/film.aep"')
    lines.insert(mru + 1, b'\t"MRU Project Path ID # 1, File Path" = "/Users/someone/older.aep"')
    return b"\r".join(lines)


def test_parse_and_serialize_round_trip_exactly():
    for data in (prefs_bytes(), _old_file(), _reset_file()):
        assert prefs.serialize(*prefs.parse(data)) == data


def test_restore_plan_adds_lost_settings_and_never_the_protected_ones(tmp_path):
    cur, old = tmp_path / "cur.txt", tmp_path / "old.txt"
    cur.write_bytes(_reset_file())
    old.write_bytes(_old_file())
    plan = prefs.restore(old, file=cur)
    assert plan["applied"] is False and cur.read_bytes() == _reset_file()
    added = {(a["section"], a["key"]) for a in plan["added"]}
    assert ('["Concurrent Frame Rendering"]', '"Reserved CPU Power"') in added
    assert ('["Only In Old"]', '"x"') in added
    assert ('["Coachmarks Settings"]', '"Simple.Coachmarks.Seen"') in added
    assert not any("SCRIPTING" in k or "LAST_DATE_TIME" in k or "GPU" in k for _s, k in added)
    assert not any(s in ('["Disk Cache Controls"]', '["Settings_Slate"]') for s, _k in added)
    assert plan["changed"] == [{"section": '["Coachmarks Settings"]', "key": '"Enabled"', "now": '"1"', "was": '"0"'}]
    assert plan["skipped_mru"] and plan["skipped_mru"][0]["section"].startswith('["Most Recently Used')


def test_restore_apply_keeps_security_cache_and_current_entries(tmp_path):
    cur, old = tmp_path / "cur.txt", tmp_path / "old.txt"
    cur.write_bytes(_reset_file())
    old.write_bytes(_old_file())
    prefs.restore(old, file=cur, apply=True)
    merged = cur.read_bytes()
    assert b"\n" not in merged
    assert prefs.value_of(merged, *prefs.SCRIPTING) == "0"  # the person's current choice, untouched
    assert prefs.value_of(merged, *prefs.DISK_CACHE) == "0"
    assert prefs.value_of(merged, "Main Pref Section v2", "Pref_LAST_DATE_TIME") == "2026-09-30 09:00:00"
    assert prefs.value_of(merged, "Concurrent Frame Rendering", "Reserved CPU Power") == "25"
    assert prefs.value_of(merged, "Coachmarks Settings", "Enabled") == "0"
    assert b"Settings_Slate" not in merged
    assert prefs.value_of(merged, "Most Recently Used (MRU) Search v2",
                          "MRU Project Path ID # 0, File Path") == "/Users/someone/new.aep"


def test_restore_can_skip_extra_report_sections(tmp_path):
    cur, old = tmp_path / "cur.txt", tmp_path / "old.txt"
    cur.write_bytes(_reset_file())
    old.write_bytes(_old_file() + b"\r" + b'["Settings_Other_Pipeline"]\r\t"k" = "v"\r')
    plan = prefs.restore(old, file=cur, skip_sections=("Settings_Other_Pipeline",))
    assert not any(a["section"] == '["Settings_Other_Pipeline"]' for a in plan["added"])


def test_restore_refuses_damaged_files(tmp_path):
    cur, old = tmp_path / "cur.txt", tmp_path / "old.txt"
    cur.write_bytes(_reset_file())
    old.write_bytes(_old_file().replace(b"\r", b"\n"))
    with pytest.raises(Refused):
        prefs.restore(old, file=cur)


def test_live_restore_apply_needs_ae_closed_and_backs_up(live_prefs, tmp_path, monkeypatch, slate_home):
    f = live_prefs(data=_reset_file())
    old = tmp_path / "old.txt"
    old.write_bytes(_old_file())
    monkeypatch.setattr(procs, "ae_running", lambda: True)
    assert prefs.restore(old)["applied"] is False  # a dry run is fine with AE open
    with pytest.raises(Busy):
        prefs.restore(old, apply=True)
    assert f.read_bytes() == _reset_file()
    monkeypatch.setattr(procs, "ae_running", lambda: False)
    res = prefs.restore(old, apply=True)
    assert res["applied"] and Path(res["backup"]).read_bytes() == _reset_file()


# ---- the disk-cache toggle -----------------------------------------------------------------------------------


def test_disk_cache_off_toggles_and_restores(live_prefs):
    f = live_prefs()
    with prefs.disk_cache_off() as state:
        assert prefs.value_of(f.read_bytes(), *prefs.DISK_CACHE) == "0"
        assert state["was"] == "1" and state["toggled"] is True
    assert prefs.value_of(f.read_bytes(), *prefs.DISK_CACHE) == "1"
    assert state["restored"] is True


def test_disk_cache_is_restored_even_when_the_render_fails(live_prefs):
    f = live_prefs()
    with pytest.raises(RuntimeError):
        with prefs.disk_cache_off():
            raise RuntimeError("aerender fell over")
    assert prefs.value_of(f.read_bytes(), *prefs.DISK_CACHE) == "1"


def test_disk_cache_off_is_busy_with_ae_open(live_prefs, monkeypatch):
    live_prefs()
    monkeypatch.setattr(procs, "ae_running", lambda: True)
    with pytest.raises(Busy):
        with prefs.disk_cache_off():
            pass


def test_disk_cache_restore_failure_names_the_command(live_prefs, monkeypatch, capsys):
    f = live_prefs()
    with prefs.disk_cache_off() as state:
        monkeypatch.setattr(procs, "ae_running", lambda: True)  # the person opened AE mid-render
    assert state["restored"] is False
    assert prefs.value_of(f.read_bytes(), *prefs.DISK_CACHE) == "0"
    assert 'slate ae prefs set "Disk Cache Controls" "Enabled 2" 1' in capsys.readouterr().err


def test_disk_cache_missing_key_leaves_the_cache_alone(live_prefs):
    lines = [ln for ln in prefs_lines() if b"Enabled 2" not in ln]
    f = live_prefs(data=b"\r".join(lines))
    before = f.read_bytes()
    with prefs.disk_cache_off() as state:
        assert state["toggled"] is False and state["was"] is None
    assert f.read_bytes() == before


def test_disk_cache_off_refuses_a_damaged_file(live_prefs):
    live_prefs(data=prefs_bytes().replace(b"\r", b"\n"))
    with pytest.raises(Refused):
        with prefs.disk_cache_off():
            pass


def test_internal_proof_failures_are_errors_not_writes(copy, monkeypatch):
    before = copy.read_bytes()
    real = prefs.value_of
    calls = {"n": 0}

    def flaky(data, section, name):
        calls["n"] += 1
        if (section, name) == prefs.SCRIPTING and calls["n"] > 1:
            return "tampered"
        return real(data, section, name)

    monkeypatch.setattr(prefs, "value_of", flaky)
    with pytest.raises(SlateError, match="scripting security setting"):
        prefs.set("Disk Cache Controls", "Enabled 2", "0", file=copy)
    assert copy.read_bytes() == before
