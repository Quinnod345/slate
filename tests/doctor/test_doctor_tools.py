"""Binaries, versions, Whisper's model and the Node engine: each check's green, yellow and red paths."""

from __future__ import annotations

from slate.doctor import core, tools
from slate.doctor.env import Ran, macho_archs

from doctor_helpers import HF_PKG, by_id


def rows_for(denv, fn):
    return by_id(fn(denv, core.make_ctx(denv)))


# ---------------------------------------------------------------------------------------------------------------
# ffmpeg and ffprobe


def test_ffmpeg_green_records_keg_and_ignored_copies(denv):
    denv.envvars["PATH"] = "/usr/local/bin:/usr/bin"
    denv.present.add("/usr/local/bin/ffmpeg")
    row = rows_for(denv, tools.check_ffmpeg)["tool.ffmpeg"]
    assert row["status"] == "green"
    assert row["details"]["keg"] == "/opt/homebrew/Cellar/ffmpeg/9.0.2"
    assert row["details"]["version"] == "9.0.2"
    assert "/usr/local/bin/ffmpeg" in row["details"]["shadowed"]
    assert "ignores /usr/local/bin/ffmpeg" in row["summary"]
    assert row["details"]["drawtext"] is False  # the slim build: captions are burned in by Remotion


def test_ffmpeg_full_build_keg_is_recorded(denv):
    denv.present_dirs.add("/opt/homebrew/opt/ffmpeg-full")
    denv.real["/opt/homebrew/opt/ffmpeg-full"] = "/opt/homebrew/Cellar/ffmpeg-full/9.0.2"
    row = rows_for(denv, tools.check_ffmpeg)["tool.ffmpeg"]
    assert row["details"]["full_build_keg"] == "/opt/homebrew/Cellar/ffmpeg-full/9.0.2"
    assert "full build keg" in row["summary"]


def test_ffmpeg_missing_is_red_with_a_brew_install(denv):
    denv.tools["ffmpeg"] = None
    row = rows_for(denv, tools.check_ffmpeg)["tool.ffmpeg"]
    assert row["status"] == "red"
    assert row["install"]["argv"] == ["/opt/homebrew/bin/brew", "install", "ffmpeg"]


def test_ffmpeg_that_wont_load_its_libraries_is_red(denv):
    denv.set_response("ffmpeg", "-hide_banner", "-version", code=134,
                      err="dyld: Library not loaded: /opt/homebrew/opt/x265/lib/libx265.215.dylib")
    row = rows_for(denv, tools.check_ffmpeg)["tool.ffmpeg"]
    assert row["status"] == "red" and "doesn't run" in row["summary"] and "libx265" in row["summary"]
    assert row["install"]["argv"][1] == "upgrade"


def test_ffmpeg_older_than_nine_is_red(denv):
    denv.set_response("ffmpeg", "-hide_banner", "-version", out="ffmpeg version 7.1.1 Copyright")
    row = rows_for(denv, tools.check_ffmpeg)["tool.ffmpeg"]
    assert row["status"] == "red" and "older than 9" in row["summary"]


def test_an_intel_ffmpeg_on_an_arm_mac_is_red(denv):
    denv.arch_of["/opt/homebrew/bin/ffmpeg"] = ["x86_64"]
    row = rows_for(denv, tools.check_ffmpeg)["tool.ffmpeg"]
    assert row["status"] == "red" and "x86_64" in row["summary"]


def test_ffprobe_is_checked_on_its_own(denv):
    denv.tools["ffprobe"] = None
    got = rows_for(denv, tools.check_ffmpeg)
    assert got["tool.ffmpeg"]["status"] == "green" and got["tool.ffprobe"]["status"] == "red"


# ---------------------------------------------------------------------------------------------------------------
# Rubber Band, uv, Node, git, gh


def test_rubberband_paths(denv):
    assert rows_for(denv, tools.check_rubberband)["tool.rubberband"]["status"] == "green"
    denv.rubberband = {"path": "/usr/local/bin/rubberband", "version": "4.0.0", "arch": ["x86_64"],
                       "machine": "arm64", "native": False, "rosetta": True, "status": "rosetta"}
    row = rows_for(denv, tools.check_rubberband)["tool.rubberband"]
    assert row["status"] == "yellow" and "Rosetta" in row["summary"]
    assert row["fix"].startswith("brew install rubberband")
    assert row["install"]["argv"] == ["/opt/homebrew/bin/brew", "install", "rubberband"]
    denv.rubberband = {"path": None, "status": "missing", "machine": "arm64"}
    assert rows_for(denv, tools.check_rubberband)["tool.rubberband"]["status"] == "red"
    denv.rubberband = {"path": "/x/rubberband", "arch": ["ppc"], "machine": "arm64", "status": "foreign"}
    assert rows_for(denv, tools.check_rubberband)["tool.rubberband"]["status"] == "red"


def test_uv_paths(denv):
    assert rows_for(denv, tools.check_uv)["tool.uv"]["status"] == "green"
    denv.set_response("uv", "--version", code=1, err="boom")
    assert rows_for(denv, tools.check_uv)["tool.uv"]["status"] == "red"
    denv.tools["uv"] = None
    assert rows_for(denv, tools.check_uv)["tool.uv"]["status"] == "red"


def test_node_paths(denv):
    denv.present.add("/usr/local/bin/node")
    denv.envvars["PATH"] = "/usr/local/bin"
    row = rows_for(denv, tools.check_node)["tool.node"]
    assert row["status"] == "green" and "npm 11.19.0" in row["summary"] and "ignores" in row["summary"]
    denv.set_response("node", "--version", out="v21.6.2")
    row = rows_for(denv, tools.check_node)["tool.node"]
    assert row["status"] == "red" and "older than 24" in row["summary"]
    denv.set_response("node", "--version", out="v26.8.1")
    denv.tools["npm"] = None
    assert rows_for(denv, tools.check_node)["tool.node"]["status"] == "red"
    denv.tools["node"] = None
    assert rows_for(denv, tools.check_node)["tool.node"]["status"] == "red"


def test_apples_git_stub_is_never_run_without_the_command_line_tools(denv):
    denv.tools["git"] = "/usr/bin/git"
    denv.set_response("xcode-select", "-p", code=2, err="unable to get active developer directory")
    row = rows_for(denv, tools.check_git)["tool.git"]
    assert row["status"] == "red" and "command-line tools" in row["summary"]
    assert ["/usr/bin/git", "--version"] not in denv.calls  # running the stub would pop up Apple's installer


def test_git_and_gh_paths(denv):
    got = rows_for(denv, tools.check_git)
    assert got["tool.git"]["status"] == "green" and got["tool.gh"]["status"] == "green"
    denv.tools["gh"] = None
    assert rows_for(denv, tools.check_git)["tool.gh"]["status"] == "yellow"
    denv.tools["git"] = None
    assert rows_for(denv, tools.check_git)["tool.git"]["status"] == "red"


# ---------------------------------------------------------------------------------------------------------------
# Whisper


def test_whisper_and_its_model(denv):
    got = rows_for(denv, tools.check_whisper)
    assert got["tool.whisper"]["status"] == "green" and "20250625_6" in got["tool.whisper"]["summary"]
    assert got["tool.whisper-model"]["status"] == "green" and "1.6 GB" in got["tool.whisper-model"]["summary"]


def test_whisper_model_missing_or_partial_is_red(denv):
    model = str(denv.home / ".cache" / "whisper" / "large-v3-turbo.pt")
    denv.sizes.pop(model)
    row = rows_for(denv, tools.check_whisper)["tool.whisper-model"]
    assert row["status"] == "red" and row["install"]["id"] == "whisper-model"
    denv.sizes[model] = 400_000_000
    row = rows_for(denv, tools.check_whisper)["tool.whisper-model"]
    assert row["status"] == "red" and "incomplete" in row["summary"]


def test_whisper_missing_is_red(denv):
    denv.tools["whisper"] = None
    assert rows_for(denv, tools.check_whisper)["tool.whisper"]["status"] == "red"


def test_whisper_model_follows_xdg_cache_home(denv, tmp_path):
    denv.envvars["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    assert tools.whisper_model_path(denv) == tmp_path / "cache" / "whisper" / "large-v3-turbo.pt"


# ---------------------------------------------------------------------------------------------------------------
# The engine and Chrome


def test_engine_paths(denv):
    got = rows_for(denv, tools.check_engine)
    assert got["engine.remotion"]["status"] == "green" and got["engine.chrome"]["status"] == "green"
    denv.engine = (denv.engine_folder, "repo")
    row = rows_for(denv, tools.check_engine)["engine.remotion"]
    assert row["status"] == "yellow" and row["install"]["id"] == "engine"


def test_engine_off_the_pin_is_red(denv):
    (denv.engine_folder / "node_modules" / "remotion" / "package.json").write_text('{"version": "4.0.530"}')
    row = rows_for(denv, tools.check_engine)["engine.remotion"]
    assert row["status"] == "red" and "4.0.530" in row["summary"] and "4.0.521" in row["summary"]


def test_no_engine_is_red_for_both_rows(denv):
    from slate.errors import MissingPrerequisite

    denv.engine = MissingPrerequisite("Slate's Node engine isn't installed yet.")
    got = rows_for(denv, tools.check_engine)
    assert got["engine.remotion"]["status"] == "red" and got["engine.chrome"]["status"] == "red"


def test_chrome_missing_or_wrong_major_is_red(denv):
    root = denv.engine_folder / "node_modules" / ".remotion" / "chrome-headless-shell"
    (root / "VERSION").write_text("131.0.1\n")
    assert rows_for(denv, tools.check_engine)["engine.chrome"]["status"] == "red"
    (root / "VERSION").unlink()
    row = rows_for(denv, tools.check_engine)["engine.chrome"]
    assert row["status"] == "red" and row["install"]["id"] == "chrome"


# ---------------------------------------------------------------------------------------------------------------
# macOS tools, simctl, optional tools


def test_macos_tools(denv):
    got = rows_for(denv, tools.check_macos_tools)
    assert {got[f"tool.{n}"]["status"] for n in ("osascript", "caffeinate", "security")} == {"green"}
    denv.present.discard("/usr/bin/caffeinate")
    assert rows_for(denv, tools.check_macos_tools)["tool.caffeinate"]["status"] == "red"


def test_simctl_paths(denv):
    assert rows_for(denv, tools.check_simctl)["tool.simctl"]["status"] == "green"
    denv.set_response("xcrun", "--find", "simctl", code=72, err="unable to find utility simctl")
    assert rows_for(denv, tools.check_simctl)["tool.simctl"]["status"] == "yellow"
    denv.set_response("xcode-select", "-p", code=2)
    row = rows_for(denv, tools.check_simctl)["tool.simctl"]
    assert row["status"] == "yellow" and "iOS" in row["summary"]


def test_optional_tools_are_yellow_when_missing(denv):
    got = rows_for(denv, tools.check_optional_tools)
    assert got["tool.rsvg-convert"]["status"] == "green" and got["tool.potrace"]["status"] == "green"
    denv.tools["potrace"] = None
    row = rows_for(denv, tools.check_optional_tools)["tool.potrace"]
    assert row["status"] == "yellow" and "optional" in row["summary"]
    assert row["install"]["argv"][-1] == "potrace"


# ---------------------------------------------------------------------------------------------------------------
# The higgsfield CLI and Aside


def test_higgsfield_cli_paths(denv):
    got = rows_for(denv, tools.check_higgsfield_cli)
    assert got["tool.higgsfield"]["status"] == "green" and "1.1.26" in got["tool.higgsfield"]["summary"]
    denv.texts[f"{HF_PKG}/package.json"] = '{"name": "@higgsfield/cli", "version": "1.2.0"}'
    row = rows_for(denv, tools.check_higgsfield_cli)["tool.higgsfield"]
    assert row["status"] == "yellow" and "1.1.26" in row["summary"]
    assert row["install"]["argv"][-1] == "@higgsfield/cli@1.1.26"
    denv.tools["higgsfield"] = None
    got = rows_for(denv, tools.check_higgsfield_cli)
    assert got["tool.higgsfield"]["status"] == "red" and "tool.higgsfield-account" not in got


def test_higgsfield_account_in_replay_is_never_asked(denv):
    row = rows_for(denv, tools.check_higgsfield_cli)["tool.higgsfield-account"]
    assert row["status"] == "green" and row["details"]["replay"] is True
    assert not [c for c in denv.calls if "account" in c]


def test_higgsfield_account_live_shows_plan_never_the_email(denv):
    denv.is_replay = False
    row = rows_for(denv, tools.check_higgsfield_cli)["tool.higgsfield-account"]
    assert row["status"] == "green" and "free plan" in row["summary"] and "50 app credits" in row["summary"]
    assert "example.test" not in str(row)  # the account's email never reaches the report


def test_higgsfield_account_not_signed_in_prints_the_sign_in_and_never_runs_it(denv):
    (denv.home / ".config" / "higgsfield" / "credentials.json").unlink()
    denv.is_replay = False
    row = rows_for(denv, tools.check_higgsfield_cli)["tool.higgsfield-account"]
    assert row["status"] == "yellow" and "higgsfield auth login" in row["fix"] and "Dia" in row["fix"]
    assert not [c for c in denv.calls if "auth" in c or "account" in c]


def test_higgsfield_account_expired_is_yellow(denv):
    denv.is_replay = False
    denv.set_response("higgsfield", "account", "status", "--json", code=1, err="Error: not logged in (401)")
    row = rows_for(denv, tools.check_higgsfield_cli)["tool.higgsfield-account"]
    assert row["status"] == "yellow" and "expired" in row["summary"]


def test_aside_paths(denv):
    row = rows_for(denv, tools.check_aside)["tool.aside"]
    assert row["status"] == "green" and "1.26.916" in row["summary"]
    assert not [c for c in denv.calls if "aside" in c[0]]  # a browser: never started by doctor
    denv.tools["aside"] = None
    assert rows_for(denv, tools.check_aside)["tool.aside"]["status"] == "red"


# ---------------------------------------------------------------------------------------------------------------
# Mach-O headers


def test_macho_archs_reads_thin_and_universal_headers():
    thin_arm = (0xFEEDFACF).to_bytes(4, "little") + (0x0100000C).to_bytes(4, "little") + b"\0" * 8
    thin_x86 = (0xFEEDFACF).to_bytes(4, "little") + (0x01000007).to_bytes(4, "little") + b"\0" * 8
    fat = ((0xCAFEBABE).to_bytes(4, "big") + (2).to_bytes(4, "big")
           + (0x01000007).to_bytes(4, "big") + b"\0" * 16 + (0x0100000C).to_bytes(4, "big") + b"\0" * 16)
    java = (0xCAFEBABE).to_bytes(4, "big") + (52).to_bytes(4, "big")
    assert macho_archs(thin_arm) == ["arm64"]
    assert macho_archs(thin_x86) == ["x86_64"]
    assert macho_archs(fat) == ["x86_64", "arm64"]
    assert macho_archs(java) == []
    assert macho_archs(b"#!/bin/sh\necho hi\n") == []


def test_keg_of():
    assert tools.keg_of("/opt/homebrew/Cellar/ffmpeg/9.0.2/bin/ffmpeg") == "/opt/homebrew/Cellar/ffmpeg/9.0.2"
    assert tools.keg_of("/usr/local/bin/ffmpeg") is None


def test_ran_helpers():
    assert Ran(0, "a", "b").ok and Ran(0, "a", "b").text == "a\nb"
    assert not Ran(127).ok
