"""The kit's golden tool's approved-acts mode, from Python (L-kit-D060). The kit's own golden stills and the
two-baseline rule are in tests/engine_kit/test_golden_stills.py.

Kit changes are additive and opt-in: without its new prop a component draws exactly as before, so an approved act never
changes under a kit change. `slate engine golden --approved <film>` proves it before a change lands: every approved
act's boundary frames and one frame per shot, rendered with the engine's kit and compared with the approved render.

The plan and the approved-act rules need no browser. The real comparison renders a small act (320 x 180, 10 frames, 2
samples), and it's the proof the knowledge check runs for this lesson, so it runs whenever headless Chrome is installed
and skips, saying why, where it isn't: an opt-in kit prop passes and a changed default fails, on a fixture film with one
approved act.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from slate import jsonio, paths
from slate.cli import run_cli

KIT_FILM = paths.ENGINE_SRC / "tests" / "core" / "fixtures" / "kit-film"
KIT_BRAND = paths.ENGINE_SRC / "tests" / "core" / "fixtures" / "kit-brand"
CHROME = paths.ENGINE_SRC / "node_modules" / ".remotion" / "chrome-headless-shell"


def run(*argv):
    code, out, err = run_cli(list(argv))
    return code, (json.loads(out) if out.strip().startswith("{") else out), err


def _has_browser() -> bool:
    return bool(os.environ.get("SLATE_CHROME")) or any(CHROME.glob("*/chrome-headless-shell-*/chrome-headless-shell"))


@pytest.fixture
def kit_film(slate_home, monkeypatch):
    """The engine's kit fixture film (act 1 drawn with the kit, 10 frames over two shots), with its brand pack."""
    monkeypatch.delenv("SLATE_ENGINE_DIR", raising=False)
    dest = slate_home["films"] / "kit-e2e"
    shutil.copytree(KIT_FILM, dest)
    shutil.copytree(KIT_BRAND, slate_home["brands"] / "kit-e2e-brand")
    return dest


def _register_and_approve(film: Path, *, frames=(0, 10), samples=2, data: bytes | None = None,
                          approve: bool = True) -> str:
    """Put a render of act 1 in the registry (its bytes `data`, or the real render already there) and, when asked, a
    person's approval of exactly those bytes in approvals.json. Returns the render's SHA-256."""
    render = film / "renders" / "act1.mov"
    render.parent.mkdir(parents=True, exist_ok=True)
    if data is not None:
        render.write_bytes(data)
    sha = jsonio.sha256(render)
    jsonio.write_atomic(film / "renders" / "registry.json", {"entries": [{
        "act": 1, "engine": "remotion", "file": "renders/act1.mov", "frames": list(frames), "samples": samples,
        "sha256": sha, "made_at": "2026-10-01T00:00:00+00:00", "bundle_hash": None}]})
    approvals = {"schema": 1, "approvals": [], "questions": [], "revisions": []}
    if approve:
        approvals["approvals"].append({"id": "ap_0123456789", "what": "hero:act1", "kind": "hero",
                                       "path": "renders/act1.mov", "sha256": sha, "basis": "file",
                                       "approved_by": "Test", "at": "2026-10-01T00:00:00+00:00", "sitting": "packet",
                                       "blocking": False})
    jsonio.write_atomic(film / "approvals.json", approvals)
    return sha


def test_the_plan_lists_each_approved_act_with_its_boundaries_and_one_frame_per_shot(kit_film):
    _register_and_approve(kit_film, data=b"an approved render")
    code, out, err = run("engine", "golden", "--approved", str(kit_film), "--plan", "--json")
    assert code == 0, err
    assert out["plan"] is True and out["checked"] == 0
    [act] = out["acts"]
    # act 1 is film frames 0-9 over E01 (0-4) and E02 (5-9): both boundaries, and each shot's middle frame
    assert act["act"] == 1 and act["frames"] == [0, 2, 7, 9]
    assert act["samples"] == 2 and act["approval"]["what"] == "hero:act1"
    assert "problem" not in act


def test_only_a_person_approved_render_counts_and_a_moved_act_fails(kit_film):
    # registered but never approved: nothing a kit change could break here
    _register_and_approve(kit_film, data=b"a render nobody approved", approve=False)
    code, out, err = run("engine", "golden", "--approved", str(kit_film), "--plan", "--json")
    assert code == 0, err
    assert out["acts"] == [] and "no approved acts" in out["note"]
    # approved, then changed on disk: the approval no longer covers these bytes
    _register_and_approve(kit_film, data=b"approved bytes")
    (kit_film / "renders" / "act1.mov").write_bytes(b"other bytes")
    code, out, _ = run("engine", "golden", "--approved", str(kit_film), "--plan", "--json")
    assert code == 1 and "changed on disk" in out["acts"][0]["problem"]
    # approved, but the act has moved since: its frames can't line up with the approved render any more
    _register_and_approve(kit_film, data=b"approved bytes", frames=(0, 12))
    code, out, _ = run("engine", "golden", "--approved", str(kit_film), "--plan", "--json")
    assert code == 1 and "moved since its approved render" in out["acts"][0]["problem"]


def _engine_copy(root: Path, edit) -> Path:
    """A copy of this engine (its sources; node_modules linked) with its kit edited by `edit(kit_dir)`."""
    dest = root / "engine"
    dest.mkdir(parents=True)
    for name in ("cli.mjs", "package.json", "package-lock.json", "tsconfig.json"):
        shutil.copy2(paths.ENGINE_SRC / name, dest / name)
    for folder in ("remotion", "web", "ae"):
        shutil.copytree(paths.ENGINE_SRC / folder, dest / folder)
    (dest / "node_modules").symlink_to(paths.ENGINE_SRC / "node_modules")
    edit(dest / "remotion" / "kit")
    return dest


@pytest.mark.skipif(not _has_browser(), reason="no headless Chrome here (slate doctor --install sets it up)")
def test_an_opt_in_prop_keeps_approved_acts_and_a_changed_default_fails(kit_film, tmp_path, monkeypatch):
    from slate.engine import bridge

    # the approved render: act 1 at 2 samples, with the engine as it is
    res = bridge.call("render", {"film": str(kit_film), "act": 1, "sub": 2, "out": str(kit_film / "renders" /
                                                                                       "act1.mov")}, timeout=900)
    assert res["frame_count"] == 10
    _register_and_approve(kit_film, samples=2)

    def opt_in(kit: Path):
        # a new prop the act doesn't pass: without it, Text draws exactly as before
        f = kit / "type.tsx"
        src = f.read_text(encoding="utf-8")
        old = "export function Text({role = 'body', color, style, children}: {role?: string; color?: string; style?: React.CSSProperties; children?: React.ReactNode}) {\n  const s = useType(role, color);\n  return <span style={{...s, ...style}}>{children}</span>;"
        assert old in src
        f.write_text(src.replace(old, "export function Text({role = 'body', color, style, underline, children}: {role?: string; color?: string; style?: React.CSSProperties; underline?: boolean; children?: React.ReactNode}) {\n  const s = useType(role, color);\n  return <span style={{...s, ...(underline ? {textDecoration: 'underline'} : {}), ...style}}>{children}</span>;"), encoding="utf-8")

    def changed_default(kit: Path):
        # the h2 role, which the act's title uses, set larger by default
        f = kit / "core" / "tokens.ts"
        src = f.read_text(encoding="utf-8")
        head, sep, tail = src.partition("  h2: {")
        assert sep, "the type scale has no h2 role"
        size = tail.split("size: ", 1)[1].split(",", 1)[0]
        f.write_text(head + sep + tail.replace(f"size: {size},", f"size: {float(size) * 1.5:.4f},", 1),
                     encoding="utf-8")

    for name, edit, want in (("opt-in", opt_in, 0), ("default", changed_default, 1)):
        engine = _engine_copy(tmp_path / name, edit)
        monkeypatch.setenv("SLATE_ENGINE_DIR", str(engine))
        code, out, err = run("engine", "golden", "--approved", str(kit_film), "--json")
        assert code == want, (name, out, err[-2000:])
        [act] = out["acts"]
        assert len(act["frames"]) == 4
        if want == 0:
            assert all(f["pass"] for f in act["frames"])
        else:
            assert not act["pass"] and any(not f["pass"] for f in act["frames"])
