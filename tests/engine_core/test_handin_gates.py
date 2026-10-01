"""The hand-in check's newer gates, run the way agents run them (`slate check`, `slate emit-events`), each on a small
fixture act that passes and one that breaks the rule:

- handin.act<N>.never_claim   every on-screen line that uses something the film never claims (L-qa-D036)
- handin.act<N>.literals      literal film or act frames in the act's sources (L-timing-D041)
- handin.act<N>.margins       timing margins and the rig lint, and emit-events refusing while either is broken
                              (L-timing-D039)
- handin.act<N>.character     the character acceptance checks on each mark the act draws (L-kit-077, L-motion-D074,
                              D076, D077, D086, D088)
- handin.act<N>.markup        the act's static markup at every frame and four sub-frame samples, with --markup, and
                              against the kit at a git ref or in a folder: an opt-in prop passes, a changed default
                              fails (L-kit-D146, L-kit-S010)
- the rig lint                each combination rule the rig switches a gesture off for (L-kit-D081)
- emit-events' change report  what moved, was added, removed, re-kinded or reworded (L-timing-D028)
- handin.act<N>.determinism   frames rendered alone and in sequence (L-render-D004)
- handin.act<N>.fonts         a brand face the renderer can't load fails the check (L-kit-013)

The last two render real frames (a 320 x 180 act of a few frames, software GL). They're the proofs the knowledge check
runs for those lessons, so they run whenever headless Chrome is installed and skip, saying why, where it isn't.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from slate import jsonio, paths
from slate.cli import run_cli

CHROME = paths.ENGINE_SRC / "node_modules" / ".remotion" / "chrome-headless-shell"
FPS = 25


def run(*argv):
    code, out, err = run_cli(list(argv))
    return code, (json.loads(out) if out.strip().startswith("{") else out), err


def _has_browser() -> bool:
    return bool(os.environ.get("SLATE_CHROME")) or any(CHROME.glob("*/chrome-headless-shell-*/chrome-headless-shell"))


BRAND = {
    "name": "handin-brand",
    "palette": {"page": "#F2F1EE", "ink": "#1F1F1D", "accent": "#3456A6", "surface": "#FFFFFF"},
    "type": {"families": [{"role": "sans", "family": "Helvetica Neue", "weights": [400, 700], "source": "installed",
                           "licence": "macOS system font, used in place, never redistributed"}]},
    "motion": {"eases": {"enter": {"bezier": [0.2, 0.7, 0.2, 1], "lineage": "fixture"},
                         "glide": {"kind": "sine", "lineage": "fixture"}},
               "durations": {"tick": 0.2, "quick": 0.32, "base": 0.52, "broad": 0.8, "camera": 1.2},
               "roles": {"entrance": "enter", "move": "glide", "state": "glide", "exit": "glide"}},
    "truth": {"banned_words": ["guaranteed"], "never_claim": [{"text": "the best in the world"}]},
    "look": {"id": "flat"},
}

INDEX = """import React from 'react';
import {ActRoot, Text} from '@slate/kit';
import type {SceneProps} from '@film/data';
export {silent} from './beats';

export default function Act1({sub, act}: SceneProps) {
  return (
    <ActRoot act={act} sub={sub}>
      <Text role="h2">ON_SCREEN</Text>
    </ActRoot>
  );
}
"""

BEATS = """import {registerMotifTrace, shot} from '@slate/kit/core';

export const silent = 'a test act: its mark makes no sound yet';
const S = shot('C01').frameIn;
const E = shot('C02').frameOut;
BODY
"""


@pytest.fixture
def act_film(slate_home, monkeypatch):
    """A one-act film (C01 and C02, 60 frames each at 25 fps) built with the kit, in a brand with a never-claim."""
    monkeypatch.delenv("SLATE_ENGINE_DIR", raising=False)
    d = slate_home["films"] / "handin"
    (d / "timing").mkdir(parents=True)
    (d / "scenes" / "act1").mkdir(parents=True)
    jsonio.write_atomic(d / "film.json", {"id": "handin", "title": "Hand-in", "format": "test", "language": "en",
                                          "fps_num": FPS, "fps_den": 1, "width": 320, "height": 180,
                                          "brand": "handin-brand", "mode": "draft"})
    jsonio.write_atomic(d / "timing" / "shots.json", {
        "fps_num": FPS, "fps_den": 1, "width": 320, "height": 180, "total_frames": 120,
        "acts": [{"index": 1, "name": "Mark", "shots": ["C01", "C02"]}],
        "shots": [{"id": "C01", "act": 1, "frame_in": 0, "frame_out": 60, "frames": 60, "sec_in": 0.0, "sec_out": 2.4,
                   "vo": "", "on_screen": "A mark"},
                  {"id": "C02", "act": 1, "frame_in": 60, "frame_out": 120, "frames": 60, "sec_in": 2.4,
                   "sec_out": 4.8, "vo": "", "on_screen": "holds still"}]})
    brand = slate_home["brands"] / "handin-brand"
    brand.mkdir()
    jsonio.write_atomic(brand / "brand.json", BRAND)
    return d


def write_act(film: Path, *, body: str = "", on_screen: str = "A calm mark", index: str = INDEX) -> None:
    (film / "scenes" / "act1" / "index.tsx").write_text(index.replace("ON_SCREEN", on_screen), encoding="utf-8")
    (film / "scenes" / "act1" / "beats.ts").write_text(BEATS.replace("BODY", body), encoding="utf-8")


def check(film: Path, *extra: str) -> tuple[int, dict]:
    """emit-events, then the check. A refused emit (a broken margin, a rig lint finding) leaves the last events in
    place, and the check runs anyway: its `emit_refused` says so."""
    code, emitted, err = run("emit-events", "--film", str(film), "--json")
    code, out, err = run("check", "1", "--film", str(film), "--json", *extra)
    assert isinstance(out, dict), err[-2000:]
    out["emit_refused"] = None if code == 0 and isinstance(emitted, dict) and emitted.get("acts") else emitted
    return code, out


def gate(out: dict, name: str) -> dict:
    return next(g for g in out["gates"] if g["gate"] == f"handin.act1.{name}")


# ---------------------------------------------------------------------------------------------------- never-claim
def test_never_claim_prints_every_on_screen_line_that_uses_one(act_film):
    write_act(act_film)
    code, out = check(act_film)
    assert code == 0, out
    g = gate(out, "never_claim")
    assert g["status"] == "PASS" and "1 never-claims" in g["summary"]
    write_act(act_film, on_screen="Simply the best in the world")
    code, out = check(act_film)
    g = gate(out, "never_claim")
    assert code == 1 and g["status"] == "FAIL"
    assert any('scenes/act1/index.tsx:9: "Simply the best in the world" says "the best in the world"' in e
               for e in g["evidence"]), g["evidence"]


# ---------------------------------------------------------------------------------------------------- frame literals
def test_literal_frames_fail_with_file_and_line(act_film):
    write_act(act_film, body="registerMotifTrace([{kind: 'pulse', frame: S + 30}], {rig: 'mark', shot: 'C01', "
                             "layout: () => ({x: 160, y: 90, size: 40}), from: S, to: E - 1, short: 180, "
                             "character: true});")
    code, out = check(act_film)
    assert gate(out, "literals")["status"] == "PASS", gate(out, "literals")
    write_act(act_film, body="registerMotifTrace([{kind: 'pulse', frame: 30}], {rig: 'mark', shot: 'C01', "
                             "layout: () => ({x: 160, y: 90, size: 40}), from: S, to: E - 1, short: 180, "
                             "character: true});\nexport const LAND_FRAME = 96;")
    code, out = check(act_film)
    g = gate(out, "literals")
    assert code == 1 and g["status"] == "FAIL"
    assert any(e.startswith("scenes/act1/beats.ts:6: frame: 30 is a literal frame") for e in g["evidence"]), g["evidence"]
    assert any(e.startswith("scenes/act1/beats.ts:7: LAND_FRAME = 96 is a literal frame") for e in g["evidence"])


# ---------------------------------------------------------------------------------------------------- margins
def test_a_negative_margin_fails_the_check_and_stops_emit_events(act_film):
    write_act(act_film, body="import {margin} from '@slate/kit/core';\n"
                             "margin('the card lands before C02 starts', shot('C01').start + 1, shot('C02').start);")
    code, out = check(act_film)
    assert code == 0 and gate(out, "margins")["status"] == "PASS"
    assert "the least 1.400 s" in gate(out, "margins")["summary"]
    events = act_film / "timing" / "visual_events" / "act1.json"
    before = events.read_bytes()
    # after a "re-voice", the card would land after C02 starts
    write_act(act_film, body="import {margin} from '@slate/kit/core';\n"
                             "margin('the card lands before C02 starts', shot('C02').start + 0.3, shot('C02').start);")
    code, out, err = run("emit-events", "--film", str(act_film), "--json")
    assert code == 1 and "a timing margin is broken: the card lands before C02 starts (-0.300 s)" in json.dumps(out)
    assert events.read_bytes() == before  # nothing written
    code, out, _ = run("check", "1", "--film", str(act_film), "--json")
    g = gate(out, "margins")
    assert code == 1 and g["status"] == "FAIL" and "1 timing margin broken" in g["summary"]
    assert any("BROKEN  the card lands before C02 starts" in e for e in g["evidence"])


# ---------------------------------------------------------------------------------------------------- the character
LAYOUT = "() => ({x: 160, y: 90, size: 40})"


def mark(track: str, *, layout: str = LAYOUT, extra: str = "") -> str:
    return (f"registerMotifTrace({track}, {{rig: 'mark', shot: 'C01', layout: {layout}, from: S, to: E - 1, "
            f"short: 180, character: true{extra}}});")


def test_the_rig_lint_lists_each_combination_rule(act_film):
    """One mark per rule the rig switches a gesture off for (and one crowded pair it lets run): the margins gate lists
    each with its mark, and emit-events writes nothing while any is there (L-kit-D081)."""
    lay = "() => ({x: 160, y: 90, size: 40})"

    def rig(name: str, track: str) -> str:
        return (f"registerMotifTrace({track}, {{rig: '{name}', shot: 'C01', layout: {lay}, from: S, to: E - 1, "
                "short: 180});")

    body = "\n".join([
        "import {gestureSpans} from '@slate/kit/core';",
        rig("held", "[{kind: 'hold', frame: S + 20, untilFrame: S + 70}, {kind: 'pulse', frame: S + 45}]"),
        rig("away", "[{kind: 'exit', frame: S + 10}, {kind: 'pulse', frame: S + 50}, {kind: 'enter', frame: S + 100}]"),
        rig("early", "[{kind: 'turn', frame: S + 10}, {kind: 'enter', frame: S + 60}]"),
        rig("leans", "[{kind: 'lean', frame: S + 30, toward: {x: 300, y: 90}}, "
                     "{kind: 'lean', frame: S + 36, toward: {x: 160, y: 170}}]"),
        rig("busy", "[{kind: 'grow', frame: S + 40}, {kind: 'turn', frame: S + 42}]"),
        f"const [p] = gestureSpans([{{kind: 'pulse', frame: S + 30}}], {{layout: {lay}, short: 180}});",
        f"const [g] = gestureSpans([{{kind: 'grow', frame: S}}], {{layout: {lay}, short: 180}});",
        rig("crowded", "[{kind: 'pulse', frame: S + 30}, {kind: 'grow', frame: p.end + 2 - (g.start - S)}]"),
    ])
    write_act(act_film, body=body)
    code, out = check(act_film)
    g = gate(out, "margins")
    assert code == 1 and g["status"] == "FAIL" and g["details"]["rigs"] == 6, g
    lint = "\n".join(e for e in g["evidence"] if e.startswith("rig lint: "))
    for rig_name, says in (("held", "nothing runs during a hold"),
                           ("away", "the parts are away"),
                           ("early", "the parts haven't arrived yet"),
                           ("leans", "never two gestures on one channel at once"),
                           ("busy", "one momentary gesture at a time"),
                           ("crowded", "2 still frames between them (keep 4 still frames between momentary gestures)")):
        assert any(ln.startswith(f"rig lint: {rig_name} (C01): ") and says in ln for ln in lint.splitlines()), \
            (rig_name, lint)
    assert "the rig lint: held (C01)" in json.dumps(out["emit_refused"])


@pytest.mark.parametrize("case, body, brand_extra, expect", [
    ("lint", mark("[{kind: 'pulse', frame: S + 30}, {kind: 'turn', frame: S + 32}]"), None,
     "one momentary gesture at a time"),
    ("calm", mark("[{kind: 'breathe', frame: E - 10}]"), None, "not calm at the act's end"),
    ("still", mark("[{kind: 'pulse', frame: S + 30}]", layout="(f: number) => ({x: 160, y: 90, size: 40 + Math.sin(f / 7) * 2})"),
     None, "moves between gestures"),
    ("split", mark("[{kind: 'pulse', frame: S + 30, amount: 0.2}]"),
     {"motion_eases": {"wobble": {"bezier": [0.3, 2.5, 0.7, -1.5], "lineage": "fixture: a curve that doubles back"}},
      "motif": {"parts": 6, "part_size": 0.33, "ring": 0.3, "curves": {"move": "wobble", "land": "spring0"}}},
     "crosses the split point"),
    ("clip", mark("[{kind: 'pulse', frame: S + 30}]", extra=", clip: {x: 140, y: 70, w: 40, h: 40}"), None,
     "clipping container cuts into it"),
])
def test_the_character_gate_fails_each_rule(act_film, slate_home, case, body, brand_extra, expect):
    write_act(act_film, body=mark("[{kind: 'pulse', frame: S + 30}, {kind: 'turn', frame: S + 70}]"))
    code, out = check(act_film)
    g = gate(out, "character")
    assert g["status"] == "PASS", g
    if brand_extra:
        b = json.loads(json.dumps(BRAND))
        b["motion"]["eases"].update(brand_extra["motion_eases"])
        b["motif"] = brand_extra["motif"]
        jsonio.write_atomic(slate_home["brands"] / "handin-brand" / "brand.json", b)
    write_act(act_film, body=body)
    code, out = check(act_film)
    g = gate(out, "character")
    assert g["status"] == "FAIL" and expect in g["summary"] + json.dumps(g["evidence"]), (case, g)
    assert code == 1
    if case == "lint":  # a rig lint finding also stops emit-events writing the act's sound events
        assert "the rig lint: mark (C01)" in json.dumps(out["emit_refused"])


# ---------------------------------------------------------------------------------------------------- markup
def test_the_markup_bench_reads_every_sample_and_finds_nan(act_film):
    write_act(act_film)
    code, out = check(act_film, "--markup")
    g = gate(out, "markup")
    assert g["status"] == "PASS" and "480 samples (120 frames × 4)" in g["summary"], g
    nan = INDEX.replace("<Text role=\"h2\">ON_SCREEN</Text>",
                        "<Text role=\"h2\" style={{marginLeft: Number('twelve')}}>ON_SCREEN</Text>")
    write_act(act_film, index=nan)
    code, out = check(act_film, "--markup")
    g = gate(out, "markup")
    assert code == 1 and g["status"] == "FAIL" and "NaN" in g["summary"], g
    assert g["evidence"][0].startswith("frame 0 sample 0: style=")


def test_the_markup_bench_compares_with_the_kit_at_a_git_ref(act_film):
    write_act(act_film)
    code, out = check(act_film, "--markup", "--against", "HEAD")
    g = gate(out, "markup")
    assert g["status"] == "PASS" and "identical to the act drawn by the kit at HEAD" in g["summary"], g
    code, out, err = run("check", "1", "--film", str(act_film), "--against", "HEAD", "--json")
    assert code == 2 and "--against goes with --markup" in json.dumps(out) + err


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


def test_the_markup_bench_passes_an_opt_in_prop_and_catches_a_changed_default(act_film, tmp_path, monkeypatch):
    """A kit change proves itself against the kit it replaces, with no pixels: a new prop the act doesn't pass draws
    the act exactly as before, and a changed default doesn't (L-kit-S010). Here the earlier kit is a folder (this
    checkout's), as an installed engine's would be."""
    write_act(act_film)
    earlier = paths.ENGINE_SRC / "remotion" / "kit"

    def opt_in(kit: Path):
        f = kit / "type.tsx"
        src = f.read_text(encoding="utf-8")
        old = "export function Text({role = 'body', color, style, children}: {role?: string; color?: string; style?: React.CSSProperties; children?: React.ReactNode}) {\n  const s = useType(role, color);\n  return <span style={{...s, ...style}}>{children}</span>;"
        assert old in src
        f.write_text(src.replace(old, "export function Text({role = 'body', color, style, underline, children}: {role?: string; color?: string; style?: React.CSSProperties; underline?: boolean; children?: React.ReactNode}) {\n  const s = useType(role, color);\n  return <span style={{...s, ...(underline ? {textDecoration: 'underline'} : {}), ...style}}>{children}</span>;"), encoding="utf-8")

    def changed_default(kit: Path):
        f = kit / "core" / "tokens.ts"
        src = f.read_text(encoding="utf-8")
        head, sep, tail = src.partition("  h2: {")
        assert sep, "the type scale has no h2 role"
        size = tail.split("size: ", 1)[1].split(",", 1)[0]
        f.write_text(head + sep + tail.replace(f"size: {size},", f"size: {float(size) * 1.5:.4f},", 1),
                     encoding="utf-8")

    for name, edit, want in (("opt-in", opt_in, "PASS"), ("default", changed_default, "FAIL")):
        monkeypatch.setenv("SLATE_ENGINE_DIR", str(_engine_copy(tmp_path / name, edit)))
        code, out, err = run("check", "1", "--film", str(act_film), "--markup", "--against", str(earlier), "--json")
        g = gate(out, "markup")
        assert g["status"] == want, (name, g, err[-1500:])
        if want == "PASS":
            assert f"identical to the act drawn by the kit at {earlier}" in g["summary"]
        else:
            assert code == 1 and "draws differently from the kit at" in g["summary"]
            assert g["evidence"][0].startswith("frame 0 sample 0: differs at character")


# ---------------------------------------------------------------------------------------------------- the change report
def test_emit_events_reports_what_changed_against_the_last_file(act_film):
    write_act(act_film)
    beats = act_film / "scenes" / "act1" / "beats.ts"
    src = BEATS.replace("export const silent = 'a test act: its mark makes no sound yet';\n", "")
    events = ("import {defineEvent} from '@slate/kit/core';\n"
              "defineEvent({shot: 'C01', id: 'card', t: shot('C01').start + 0.4, kind: 'land', size: 's', note: 'the card lands'});\n"
              "defineEvent({shot: 'C02', id: 'dot', t: shot('C02').start + 0.2, kind: 'tap', size: 's', note: 'a dot taps'});\n")
    beats.write_text(src.replace("BODY", events), encoding="utf-8")
    (act_film / "scenes" / "act1" / "index.tsx").write_text(INDEX.replace("export {silent} from './beats';\n", "import './beats';\n").replace("ON_SCREEN", "A card"), encoding="utf-8")
    code, out, err = run("emit-events", "--film", str(act_film), "--json")
    assert code == 0, err
    first = out["acts"][0]["changes"]
    assert first["first"] is True and [a["id"] for a in first["added"]] == ["card", "dot"]
    # a re-voice moves the card two frames, rewords the dot and adds a beat
    events2 = ("import {defineEvent} from '@slate/kit/core';\n"
               "defineEvent({shot: 'C01', id: 'card', t: shot('C01').start + 0.48, kind: 'land', size: 's', note: 'the card lands'});\n"
               "defineEvent({shot: 'C02', id: 'dot', t: shot('C02').start + 0.2, kind: 'tap', size: 's', note: 'a dot taps twice'});\n"
               "defineEvent({shot: 'C02', id: 'end', t: shot('C02').start + 1, kind: 'snap', size: 's', note: 'it snaps shut'});\n")
    beats.write_text(src.replace("BODY", events2), encoding="utf-8")
    code, out, err = run("emit-events", "--film", str(act_film), "--json")
    assert code == 0, err
    ch = out["acts"][0]["changes"]
    assert ch["moved"] == [{"id": "card", "from_t": 0.4, "to_t": 0.48, "from_frame": 10, "to_frame": 12, "frames": 2}]
    assert [a["id"] for a in ch["added"]] == ["end"] and ch["removed"] == []
    assert ch["reworded"] == [{"id": "dot", "from": "a dot taps", "to": "a dot taps twice"}]
    assert ch["summary"] == "1 moved, 1 added, 1 reworded"
    report = jsonio.read(act_film / "timing" / "visual_events" / "changes" / "act1.json")
    assert report["moved"][0]["frames"] == 2 and report["act"] == 1
    # the beats folder still holds only the act's beats: everything that lists act*.json reads beats, never a report
    assert sorted(p.name for p in (act_film / "timing" / "visual_events").glob("act*.json")) == ["act1.json"]


# ---------------------------------------------------------------------------------------------------- real frames
@pytest.mark.skipif(not _has_browser(), reason="no headless Chrome here (slate doctor --install sets it up)")
def test_determinism_compares_frames_alone_and_in_sequence(act_film):
    """Two six-frame acts in one `slate check all --determinism` run (one bundle between them): act 1 draws every
    frame from film time alone and passes; act 2 counts how often it has drawn, so a frame drawn on its own differs
    from the same frame drawn in sequence, and the gate names the frame."""
    shots = jsonio.read(act_film / "timing" / "shots.json")
    shots["total_frames"] = 12
    shots["acts"] = [{"index": 1, "name": "Pure", "shots": ["C01"]}, {"index": 2, "name": "Counts", "shots": ["C02"]}]
    for s, (a, b, act) in zip(shots["shots"], ((0, 6, 1), (6, 12, 2))):
        s.update(act=act, frame_in=a, frame_out=b, frames=b - a, sec_in=a / FPS, sec_out=b / FPS)
    jsonio.write_atomic(act_film / "timing" / "shots.json", shots)
    write_act(act_film)
    # a component that redraws every frame and counts how often it has drawn: a picture that depends on history
    stateful = INDEX.replace("import {ActRoot, Text} from '@slate/kit';",
                             "import {ActRoot, Text, useFilmFrame} from '@slate/kit';\nlet drawn = 0;\n"
                             "function Counter() {\n  useFilmFrame();\n  drawn += 1;\n"
                             "  return <Text role=\"h2\">{`Drawn ${drawn} times`}</Text>;\n}"
                             ).replace("<Text role=\"h2\">ON_SCREEN</Text>", "<Counter />")
    act2 = act_film / "scenes" / "act2"
    act2.mkdir()
    (act2 / "index.tsx").write_text(stateful, encoding="utf-8")
    (act2 / "beats.ts").write_text("export const silent = 'a test act: it counts its own draws';\n", encoding="utf-8")
    code, _, err = run("emit-events", "--film", str(act_film), "--json")
    assert code == 0, err[-1500:]
    code, out, err = run("check", "all", "--film", str(act_film), "--determinism", "--json")
    by = {g["gate"]: g for g in out["gates"]}
    assert by["handin.act1.determinism"]["status"] == "PASS", (by["handin.act1.determinism"], err[-1500:])
    assert "0–5" in by["handin.act1.determinism"]["summary"]
    g = by["handin.act2.determinism"]
    assert code == 1 and g["status"] == "FAIL" and g["summary"].startswith("frame "), g


@pytest.mark.skipif(not _has_browser(), reason="no headless Chrome here (slate doctor --install sets it up)")
def test_a_face_the_renderer_cant_load_fails_the_fonts_gate(act_film, slate_home):
    """The kit's font gate, through `slate check`: a brand face no machine has is never substituted (L-kit-013). The
    contract stills run the gate, so the act declares a rest state it holds at the film's end... and a second act
    meets it."""
    b = json.loads(json.dumps(BRAND))
    b["type"]["families"][0]["family"] = "Slate Missing Face 7Q"
    jsonio.write_atomic(slate_home["brands"] / "handin-brand" / "brand.json", b)
    shots = jsonio.read(act_film / "timing" / "shots.json")
    shots["acts"] = [{"index": 1, "name": "One", "shots": ["C01"]}, {"index": 2, "name": "Two", "shots": ["C02"]}]
    shots["shots"][1]["act"] = 2
    jsonio.write_atomic(act_film / "timing" / "shots.json", shots)
    write_act(act_film)
    with (act_film / "scenes" / "act1" / "beats.ts").open("a", encoding="utf-8") as fh:
        fh.write("export const contracts = {out: 'Rest'};\n")
    (act_film / "scenes" / "contracts.tsx").write_text(
        "import React from 'react';\nexport function Rest() {\n  return <div />;\n}\n", encoding="utf-8")
    run("emit-events", "--film", str(act_film), "--json")
    code, out, err = run("check", "1", "--film", str(act_film), "--json")
    fonts = gate(out, "fonts")
    assert fonts["status"] == "FAIL", (fonts, err[-1500:])
    assert "Slate Missing Face 7Q" in json.dumps(fonts)
