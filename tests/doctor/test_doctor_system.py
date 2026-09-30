"""The Mac and Slate's own parts: disk, the default browser, the job agent, OCR, modules, instruments, licence, fonts."""

from __future__ import annotations

import plistlib

import pytest

from slate.doctor import common, core, fonts, system
from slate.doctor.env import Env
from slate.errors import UsageError

from doctor_helpers import by_id, write_brand


def one(denv, fn):
    return fn(denv, core.make_ctx(denv))[0]


# ---------------------------------------------------------------------------------------------------------------
# Disk


def test_disk_thresholds(denv):
    assert one(denv, system.check_disk)["status"] == "green"
    denv.free_bytes = 20 * 10**9
    row = one(denv, system.check_disk)
    assert row["status"] == "yellow" and "10 GB" in row["summary"]
    denv.free_bytes = 3 * 10**9
    row = one(denv, system.check_disk)
    assert row["status"] == "red" and "won't start a job below 5 GB" in row["summary"]


# ---------------------------------------------------------------------------------------------------------------
# The default browser


def test_default_browser_is_read_from_launchservices(denv):
    row = one(denv, system.check_browser)
    assert row["status"] == "green" and "Dia (company.thebrowser.dia)" in row["summary"]
    steps = row["details"]["steps"]
    assert any(s["step"] == "higgsfield auth login" and s.get("who") == "you" for s in steps)
    assert all(s.get("verified") is not False for s in steps)


def test_no_handler_means_safari(denv):
    denv.ls = None
    assert common.default_browser(denv)["name"] == "Safari"


def test_a_guard_that_cant_be_confirmed_is_yellow(denv):
    denv.tools["aside"] = None
    row = one(denv, system.check_browser)
    assert row["status"] == "yellow" and "Web captures" in row["summary"]
    denv.tools["aside"] = str(denv.home / ".local" / "bin" / "aside")
    (denv.engine_folder / "remotion" / "remotion.config.ts").write_text("Config.setChromiumOpenGlRenderer('angle');")
    row = one(denv, system.check_browser)
    assert row["status"] == "yellow" and "Remotion Studio" in row["summary"]


def test_real_launchservices_plist_parsing(tmp_path):
    home = tmp_path / "home"
    plist = home / "Library" / "Preferences" / "com.apple.LaunchServices"
    plist.mkdir(parents=True)
    data = {"LSHandlers": [{"LSHandlerContentType": "public.html", "LSHandlerRoleAll": "com.apple.safari"},
                           {"LSHandlerURLScheme": "http", "LSHandlerRoleAll": "org.mozilla.firefox"},
                           {"LSHandlerURLScheme": "https", "LSHandlerRoleAll": "company.thebrowser.dia"}]}
    (plist / "com.apple.launchservices.secure.plist").write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_BINARY))
    info = common.default_browser(Env(home=home))
    assert info == {"bundle_id": "company.thebrowser.dia", "name": "Dia", "source": "LaunchServices"}


def test_host_app_labels(denv):
    denv.envvars["TERM_PROGRAM"] = "Apple_Terminal"
    assert common.host_app(denv, None) == "Terminal"
    assert common.host_app(denv, "codex") == "Codex in Terminal"
    denv.envvars.clear()
    assert common.host_app(denv, "openclaw") == "the OpenClaw gateway"
    assert common.host_app(denv, None) == "this Terminal"


# ---------------------------------------------------------------------------------------------------------------
# The job agent, OCR, modules, licence


def test_jobs_agent_paths(denv):
    assert one(denv, system.check_jobs_agent)["status"] == "green"
    denv.jobs = {**denv.jobs, "loaded": False}
    assert one(denv, system.check_jobs_agent)["status"] == "yellow"
    denv.jobs = {**denv.jobs, "installed": False}
    row = one(denv, system.check_jobs_agent)
    assert row["status"] == "yellow" and row["install"]["id"] == "jobs-agent" and "login item" in row["fix"]
    denv.jobs = RuntimeError("launchctl missing")
    assert one(denv, system.check_jobs_agent)["status"] == "yellow"


def test_jobs_row_reports_the_runner_and_the_queue_like_slate_jobs_status(denv):
    row = one(denv, system.check_jobs_agent)
    assert row["status"] == "green" and "runner is serving the queue (the queue is empty)" in row["summary"]
    assert row["details"]["runner_serving"] is True and row["details"]["queue"]["done"] == 3
    denv.queue = {"runner": True, "counts": {"queued": 2, "running": 1}, "next": "j-20260930-120000-abcd",
                  "running": ["j-20260930-115900-aaaa"]}
    row = one(denv, system.check_jobs_agent)
    assert "1 running, 2 queued" in row["summary"] and row["details"]["next"] == "j-20260930-120000-abcd"


def test_a_loaded_agent_with_no_runner_is_a_fault_not_an_idle_queue(denv):
    """The agent runs `slate jobs serve` with KeepAlive: loaded and not serving means the runner keeps exiting."""
    denv.jobs = {**denv.jobs, "runner_serving": False, "last_exit": "1"}
    denv.queue = {**denv.queue, "runner": False, "counts": {"queued": 1, "running": 0}}
    row = one(denv, system.check_jobs_agent)
    assert row["status"] == "yellow" and "no runner is serving the queue (its last exit: 1)" in row["summary"]
    assert "1 job is waiting with nothing running it" in row["summary"]
    assert "/logs/runner.launchd.log" in row["fix"] and "slate jobs serve --once" in row["fix"]


def test_without_the_agent_the_row_still_says_what_the_queue_is_doing(denv):
    denv.jobs = {**denv.jobs, "installed": False, "loaded": False, "runner_serving": False}
    denv.queue = {"runner": False, "counts": {"queued": 3}, "next": None, "running": []}
    row = one(denv, system.check_jobs_agent)
    assert row["status"] == "yellow" and "no runner is serving it (0 running, 3 queued)" in row["summary"]
    assert "3 jobs are waiting with nothing running them" in row["summary"]
    denv.queue = RuntimeError("queue folder unreadable")
    row = one(denv, system.check_jobs_agent)
    assert row["status"] == "yellow" and "the queue itself couldn't be read" in row["summary"]


def test_the_real_env_reads_the_queue_through_the_jobs_client(slate_home):
    """Env.jobs_summary is slate.jobs.client.summary (what `slate jobs status --json` prints); an empty queue here."""
    summary = Env().jobs_summary()
    assert set(summary) >= {"runner", "counts", "next"} and summary["counts"].get("queued", 0) == 0


def test_ocr_paths(denv):
    assert one(denv, system.check_ocr)["status"] == "green"
    denv.ocr = (False, {"reason": "ocrmac isn't importable in Slate's environment"})
    row = one(denv, system.check_ocr)
    assert row["status"] == "red" and "UNCHECKED" in row["summary"]
    assert row["install"]["argv"][:3] == ["/opt/homebrew/bin/uv", "pip", "install"]
    assert row["install"]["argv"][-1] == "ocrmac>=1.0"


def test_modules_paths(denv):
    assert one(denv, system.check_modules)["status"] == "green"
    denv.broken = ({"slate.demo": "not built yet"}, 23)
    assert one(denv, system.check_modules)["status"] == "yellow"
    denv.broken = ({"slate.voice.cli": "ImportError: cannot import name 'x'"}, 23)
    row = one(denv, system.check_modules)
    assert row["status"] == "red" and "slate.voice.cli" in row["summary"]


def test_real_broken_modules_reads_slate_cli(monkeypatch):
    broken, total = Env().broken_modules()
    from slate import cli

    assert total == len(cli.MODULES) and "slate.doctor" not in broken


def test_licence_reminder_is_info(denv):
    row = one(denv, system.check_licence)
    assert row["status"] == "green" and row.get("info") is True and "teams of up to 3" in row["summary"]


# ---------------------------------------------------------------------------------------------------------------
# Apple's sampled instruments (slate.sound's own rows)


def test_instruments_rows_are_adopted_and_red_ones_say_what_to_download(denv):
    assert one(denv, system.check_instruments)["status"] == "green"
    denv.instruments = [{"id": "sound.instrument.pad_low", "status": "red", "summary": "Basses Legato: not installed",
                         "fix": None}]
    row = one(denv, system.check_instruments)
    assert row["status"] == "red" and row["group"] == "sound"
    assert "GarageBand" in row["fix"] and "Sound Library" in row["fix"]
    assert "never synthesizes a stand-in" in row["summary"]


def test_instruments_that_cant_be_checked_are_red(denv):
    denv.instruments = RuntimeError("zone map unreadable")
    row = one(denv, system.check_instruments)
    assert row["status"] == "red" and "GarageBand" in row["fix"]


def test_real_instruments_missing_is_red(tmp_path, monkeypatch):
    """Through the sound module itself, with an empty stand-in for /Library: every tonal role is red."""
    monkeypatch.setenv("SLATE_APPLE_LIBRARY", str(tmp_path / "empty-library"))
    rows = system.check_instruments(Env(), core.Ctx(context="t"))
    assert rows and {r["status"] for r in rows} == {"red"}
    assert all("GarageBand" in (r["fix"] or "") for r in rows)


# ---------------------------------------------------------------------------------------------------------------
# Fonts for a brand


def font_rows(denv, brand):
    return by_id(fonts.check_fonts(denv, core.make_ctx(denv, brand=brand)))


def test_no_brand_is_an_info_row(denv):
    row = one(denv, fonts.check_fonts)
    assert row["id"] == "fonts.brand" and row.get("info") is True


def test_brand_font_paths(denv, slate_home):
    write_brand(slate_home["brands"], "testbrand", [
        {"role": "body", "family": "Helvetica Neue", "source": "installed", "licence": "system"},
        {"role": "display", "family": "Test Adobe Sans", "source": "https://use.typekit.net/abc.css"},
        {"role": "hero", "family": "Test Google Grotesk", "source": "google", "licence": "OFL"},
        {"role": "mono", "family": "Missing Mono", "source": "installed", "licence": "Commercial, per seat"},
        {"role": "logo", "family": "Missing Kit Face", "source": "https://use.typekit.net/xyz.css"},
    ])
    got = font_rows(denv, "testbrand")
    assert got["fonts.testbrand.helvetica-neue"]["status"] == "green"
    assert got["fonts.testbrand.test-adobe-sans"]["status"] == "green"  # Adobe Fonts only: After Effects sees it
    assert got["fonts.testbrand.test-google-grotesk"]["status"] == "yellow"
    row = got["fonts.testbrand.missing-mono"]
    assert row["status"] == "red" and "Commercial, per seat" in row["fix"] and "never ships font files" in row["fix"]
    assert got["fonts.testbrand.missing-kit-face"]["status"] == "red"


def test_brand_font_list_unreadable_is_yellow(denv, slate_home):
    write_brand(slate_home["brands"], "testbrand", [{"family": "Anything", "source": "installed"}])
    denv.fonts, denv.adobe_fonts = None, set()
    assert font_rows(denv, "testbrand")["fonts.testbrand.anything"]["status"] == "yellow"


def test_brand_without_families_is_yellow_and_unknown_brand_is_a_usage_error(denv, slate_home):
    write_brand(slate_home["brands"], "emptybrand", [])
    assert one(denv, lambda e, c: fonts.check_fonts(e, core.make_ctx(e, brand="emptybrand")))["status"] == "yellow"
    with pytest.raises(UsageError):
        fonts.check_fonts(denv, core.make_ctx(denv, brand="no-such-brand"))


def test_families_accepts_every_shape():
    assert [f["family"] for f in fonts.families({"type": {"families": ["A", {"family": "B"}, {"family": "a"}]}})] == \
        ["A", "B"]
    got = fonts.families({"type": {"families": {"body": {"family": "C", "source": "google"}}}})
    assert got == [{"family": "C", "role": "body", "source": "google", "licence": None}]
    assert fonts.families({}) == []


def test_real_adobe_manifest_parsing(tmp_path):
    home = tmp_path / "home"
    manifest = home / "Library/Application Support/Adobe/CoreSync/plugins/livetype/.c/entitlements.xml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("<fonts><font><familyName>Test &amp; Sans</familyName></font>"
                        "<font><familyName>Other</familyName></font></fonts>")
    assert Env(home=home).adobe_font_families() == {"Test & Sans", "Other"}
