"""`slate demo`: a neutral 15-second 9:16 reel, made end to end through every stage, with the vendors replayed.

It's the proof that Slate works on this Mac, and the first thing to run after installing it. A film for the neutral
`brands/demo` pack goes from `slate new` to a delivered draft, one stage at a time: each stage is the real command in
its own process, and each prints one line saying what it did and how long it took. Nothing is spent. ElevenLabs
answers from Slate's replay stand-ins (SLATE_FAKE_VENDORS=1), so the voice and the score are stand-ins and the
delivery is a draft whose manifest says it isn't final and why. Everything else is real: the studio voice polish
(de-esser, edge cut, EQ, compressor, room) and its verification, measured inflection with an A/B spectrogram sheet
and the long-term spectrum against natural speech, the film clock (whose cue sheet plays the polished takes), a
placeholder act and a kit-built act rendered with motion blur at 4 samples, the score's own clarity guard, the mix and
its sound gates, an ffmpeg master, every film gate (OCR on Apple Vision when Slate's ocr extra is installed), the
review packet and the delivery with its manifest.

    slate demo            a scratch film, removed at the end (kept, and its folder named, when a stage fails)
    slate demo --keep     the film stays in your films folder as slate-demo-<n>
    slate demo --live     the voice and the score for real: it spends ElevenLabs credits. In a Terminal it asks
                          first; under an agent it refuses unless the person set SLATE_LIVE=1 for this run

A gate stage passes when no gate fails or is stale and every gate that isn't PASS is one the demo expects, each with
its reason: the replayed takes leave the voice gates unchecked (nothing real was heard), the demo brand's references
are placeholders, and without Apple Vision the text on screen can't be read. Unchecked is never called passed.

Exit codes: 0 every stage passed, 1 a stage failed, 3 something the demo needs is missing (ffmpeg, the engine, a key
for --live), 4 spend refused (--live without a person's yes).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from slate import cli, jsonio, log, paths, procs, secrets
from slate.errors import MissingPrerequisite, SpendRefused, UsageError

ASSETS = Path(__file__).with_name("demo_film")  # the demo's script and its two scenes
BRAND = "demo"
FORMAT = "social-reel"
RUNTIME_S = 15
SAMPLES = 4
TITLE = "Slate demo"
REQUEST = ("A neutral 15-second vertical reel for the demo brand, made end to end through every Slate stage, so a new "
           "install can watch the whole pipeline work.")
AB_SHOT = "D03"  # the line whose take the A/B spectrogram sheet shows before and after the studio polish
REPLAYED = "replayed stand-in"  # how the voice gates say a take was never heard
OCR_GATES = ("ocr", "readtime", "banned_words", "access")
STATES = ("PASS", "FAIL", "REVIEW", "UNCHECKED", "STALE")
STAGE_TIMEOUT_S = 600.0
LONG_TIMEOUT_S = 2400.0  # the render, every film gate, a live Whisper alignment


class StageFailed(Exception):
    """A stage didn't do what the demo needs: the run stops there."""


@dataclass
class Run:
    """One demo run: where the film is, how its stages run, and what each one did."""

    film_id: str
    film: Path
    root: Path  # the scratch folder: its data, the review packet's copy, and the film unless --keep
    keep: bool
    live: bool
    env: dict[str, str]
    vision: bool
    stages: list[dict] = field(default_factory=list)
    docs: dict[str, dict] = field(default_factory=dict)  # each stage's JSON result, by stage key

    @property
    def replay(self) -> bool:
        return not self.live

    def rel(self, path: str | Path) -> str:
        try:
            return Path(path).resolve().relative_to(self.film.resolve()).as_posix()
        except ValueError:
            return str(path)


@dataclass
class Stage:
    key: str
    run: Callable[[Run], tuple[str, dict]]  # does the stage, returns (its one-line result, its JSON)
    what: str  # for --dry-run: the command or the step


# ---------------------------------------------------------------------------------------------------- running slate
def _json(stdout: str) -> dict:
    """The one JSON object a --json command prints (the last line that is one, to be forgiving of stray output)."""
    for line in reversed([ln for ln in (stdout or "").splitlines() if ln.strip()]):
        if line.lstrip().startswith("{"):
            try:
                doc = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(doc, dict):
                return doc
    try:
        doc = json.loads(stdout or "")
        return doc if isinstance(doc, dict) else {}
    except json.JSONDecodeError:
        return {}


def slate(run: Run, *argv: str, film: bool = True, timeout: float = STAGE_TIMEOUT_S) -> tuple[int, dict, str]:
    """Run one `slate` command in its own process, with the demo's environment. Returns (exit code, JSON, stderr)."""
    words = [sys.executable, "-m", "slate", "--json", *(["--film", str(run.film)] if film else []), *argv]
    cwd = run.film if film and run.film.is_dir() else run.root
    proc = procs.run(words, env=run.env, timeout=timeout, cwd=cwd)
    return proc.returncode, _json(proc.stdout), proc.stderr or ""


def _tail(text: str, n: int = 12) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip() and "Unable to find a valid E5" not in ln]
    return "\n".join(lines[-n:])


def gates_of(doc: dict) -> list[dict]:
    for key in ("gates", "results"):
        value = doc.get(key)
        if isinstance(value, list):
            return [g for g in value if isinstance(g, dict) and g.get("gate")]
    return []


def expected(run: Run, gate: dict) -> str | None:
    """Why a gate that isn't PASS is what this demo expects, or None when it isn't."""
    if gate.get("status") != "UNCHECKED":
        return None
    name, details = str(gate.get("gate")), gate.get("details") or {}
    if run.replay and name.startswith("voice_"):
        unchecked = [str(u) for u in details.get("unchecked") or []]
        if unchecked and all(REPLAYED in u for u in unchecked):
            return "the takes are replayed stand-ins, so nothing real was heard; --live voices them"
    if name == "sound.reference" and (details.get("reference") or {}).get("placeholder"):
        return "the demo brand's sound reference is a placeholder; a real brand approves one during onboarding"
    if not run.vision and name in OCR_GATES:
        return "Apple Vision isn't installed, so the text on screen can't be read; Slate's ocr extra adds it"
    return None


def judge(run: Run, key: str, code: int, doc: dict, err: str, *, gate_stage: bool = False) -> list[dict]:
    """Raise StageFailed unless the command did its job; return its gates that aren't PASS, each with the reason the
    demo expects it. A gate stage may exit 1 (some gate isn't PASS) as long as every such gate is expected."""
    gates = gates_of(doc)
    if code not in (0, 1) or (code == 1 and not (gate_stage and gates)):
        why = doc.get("message") or doc.get("error") or _tail(err, 4) or f"exit code {code}"
        raise StageFailed(f"`slate {key}` exited {code}: {why}" + (f"\n{_tail(err)}" if err.strip() else ""))
    open_ = []
    for g in gates:
        status = g.get("status")
        if status == "PASS":
            continue
        reason = expected(run, g)
        if reason is None:
            raise StageFailed(f"{g.get('gate')} is {status}: {g.get('summary')}")
        open_.append({"gate": g.get("gate"), "status": status, "summary": g.get("summary"), "why": reason})
    return open_


def counts(doc: dict) -> dict[str, int]:
    out = {s: 0 for s in STATES}
    for g in gates_of(doc):
        if g.get("status") in out:
            out[g["status"]] += 1
    return out


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _gate_line(doc: dict, opened: list[dict], what: str, note: str = "") -> str:
    """A gate stage's line, like `17 sound gates PASS, …; 1 unchecked as expected: sound.reference (why)`."""
    c = counts(doc)
    line = f"{c['PASS']} {what} PASS" + (f", {note}" if note else "")
    if opened:
        reasons: dict[str, list[str]] = {}
        for o in opened:
            reasons.setdefault(str(o["why"]), []).append(str(o["gate"]))
        line += f"; {len(opened)} unchecked as expected: " + "; ".join(
            f"{', '.join(names)} ({why})" for why, names in reasons.items())
    return line


# ---------------------------------------------------------------------------------------------------- the stages
def st_new(run: Run) -> tuple[str, dict]:
    argv = ["new", run.film_id, "--brand", BRAND, "--format", FORMAT, "--runtime", str(RUNTIME_S), "--mode", "draft",
            "--title", TITLE, "--request", REQUEST, "--source", "slate demo"]
    if not run.keep:
        argv.append("--no-git")  # a scratch film is removed at the end: it has no history to keep
    code, doc, err = slate(run, *argv, film=False)
    judge(run, "new", code, doc, err)
    made = Path(doc.get("dir") or run.film)
    if made.resolve() != run.film.resolve():
        raise StageFailed(f"`slate new` made the film in {made}, not {run.film}")
    fj = doc.get("film_json") or {}
    fps = fj.get("fps_num", 0) / max(1, fj.get("fps_den", 1))
    return (f"{run.film_id}: {fj.get('format')} {fj.get('width')}×{fj.get('height')} at {fps:g} fps, "
            f"{fj.get('mode')} mode, brand {fj.get('brand')}"), doc


def st_script(run: Run) -> tuple[str, dict]:
    shutil.copyfile(ASSETS / "script.json", run.film / "script" / "script.json")
    code, doc, err = slate(run, "script", "build")
    judge(run, "script build", code, doc, err)
    acts = len(jsonio.read(ASSETS / "script.json")["acts"])
    c = counts(doc)
    return (f"wrote the demo's script, built the storyboard: {doc.get('shots')} shots in {acts} acts, "
            f"{float(doc.get('runtime_s') or 0):.1f} s; {c['PASS']} script gates PASS"), doc


def st_tts(run: Run) -> tuple[str, dict]:
    code, doc, err = slate(run, "voice", "tts")
    judge(run, "voice tts", code, doc, err)
    takes = doc.get("takes") or []
    if not takes:
        raise StageFailed("`slate voice tts` voiced nothing")
    replayed = all(t.get("replay") for t in takes)
    fit = doc.get("fit_problems") or []
    line = (f"{_plural(len(takes), 'take')} on {doc.get('model')}, voice {doc.get('voice_id')}"
            + (" (replayed: nothing spent)" if replayed else ""))
    return line + (f"; fit: {'; '.join(fit)}" if fit else "; every read fits its shot"), doc


def st_polish(run: Run) -> tuple[str, dict]:
    code, doc, err = slate(run, "voice", "polish")
    judge(run, "voice polish", code, doc, err)
    done = doc.get("polished") or []
    if not done:
        raise StageFailed("`slate voice polish` polished nothing")
    bad = [t["shot"] for t in done if not (t.get("verified") or {}).get("ok")]
    if bad:
        raise StageFailed(f"the polish didn't verify on {', '.join(bad)}")
    return (f"{_plural(len(done), 'take')} through the studio chain at strength {doc.get('strength')} (high-pass, "
            "low-mid cut, compressor, edge cut, de-esser, warmth, room): same length and loudness, each verified "
            "by measurement"), doc


def st_align(run: Run) -> tuple[str, dict]:
    code, doc, err = slate(run, "voice", "align", timeout=LONG_TIMEOUT_S)
    judge(run, "voice align", code, doc, err)
    rows = doc.get("aligned") or []
    engines = sorted({str(r.get("engine")) for r in rows})
    how = ("the replayed voice's own word times" if engines == ["replay"] else ", ".join(engines))
    return f"{_plural(len(rows), 'take')} timed word by word ({how})", doc


def st_analyze(run: Run) -> tuple[str, dict]:
    raw = run.film / "voice" / "takes" / AB_SHOT / "t01.wav"
    code, doc, err = slate(run, "voice", "analyze", AB_SHOT, "--stage", "polished", "--ab", str(raw), "--sheet",
                           "--spectrum", "--name", "demo-polish")
    judge(run, "voice analyze", code, doc, err)
    sheet = doc.get("sheet")
    chart = (doc.get("spectrum") or {}).get("chart")
    if not sheet or not Path(sheet).is_file() or not chart or not Path(chart).is_file():
        raise StageFailed("`slate voice analyze` drew no spectrogram sheet or spectrum chart")
    return (f"{AB_SHOT} before and after the polish: pitch contour, breath, sibilance and edge measured; A/B "
            f"spectrogram sheet and long-term spectrum against natural speech in {run.rel(Path(sheet).parent)}"), doc


def st_timing(run: Run) -> tuple[str, dict]:
    code, doc, err = slate(run, "timing")
    judge(run, "timing", code, doc, err, gate_stage=True)
    shots = jsonio.read(run.film / "timing" / "shots.json")
    cues = (jsonio.read(run.film / "timing" / "vo_cues.json") or {}).get("cues") or []
    polished = [c for c in cues if str(c.get("file", "")).endswith(".polished.wav")]
    if len(polished) != len(cues):
        raw = ", ".join(c.get("shot") for c in cues if c not in polished)
        raise StageFailed(f"the cue sheet doesn't play the polished takes ({raw}): the polish wouldn't reach the mix")
    return (f"{shots.get('total_frames')} frames on the film's clock; the cue sheet plays the polished takes "
            f"({len(polished)} of {len(cues)})"), doc


def st_captions(run: Run) -> tuple[str, dict]:
    code, doc, err = slate(run, "voice", "captions")
    judge(run, "voice captions", code, doc, err)
    problems = doc.get("problems") or []
    if problems:
        raise StageFailed("captions: " + "; ".join(map(str, problems)))
    return f"{_plural(int(doc.get('cues') or 0), 'cue')} timed to the words: SRT, WebVTT and timing/captions.json", doc


def st_scenes(run: Run) -> tuple[str, dict]:
    src = ASSETS / "scenes"
    written = []
    for path in sorted(p for p in src.rglob("*") if p.is_file()):
        dest = run.film / "scenes" / path.relative_to(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        written.append(run.rel(dest))
    return ("act 1 the kit's placeholder (silent on purpose); act 2 built from the kit (Card, Text, Captions) with "
            f"two beats on the words ({_plural(len(written), 'file')})"), {"written": written}


def st_emit(run: Run) -> tuple[str, dict]:
    code, doc, err = slate(run, "emit-events")
    judge(run, "emit-events", code, doc, err)
    acts = {int(a.get("act")): a for a in doc.get("acts") or []}
    if set(acts) != {1, 2} or doc.get("skipped"):
        raise StageFailed(f"emit-events wrote acts {sorted(acts)}, skipped {doc.get('skipped')}")
    traced = sum(int((a.get("motion") or {}).get("entries") or 0) for a in acts.values())
    return (f"act 1 silent ({acts[1].get('reason')}), act 2 {_plural(int(acts[2].get('events') or 0), 'beat')}; "
            f"motion traces for both ({_plural(traced, 'move')})"), doc


def st_check(run: Run) -> tuple[str, dict]:
    code, doc, err = slate(run, "check", "all")
    opened = judge(run, "check all", code, doc, err, gate_stage=True)
    return _gate_line(doc, opened, "hand-in gates", "both acts: types, words, banned words, beats, contracts"), doc


def st_render(run: Run) -> tuple[str, dict]:
    code, doc, err = slate(run, "render", "--samples", str(SAMPLES), timeout=LONG_TIMEOUT_S)
    judge(run, "render", code, doc, err, gate_stage=True)
    renders = doc.get("renders") or []
    if not renders or not all(r.get("registered") for r in renders):
        raise StageFailed("the act renders weren't all registered")
    frames = sum(int(r.get("frame_count") or 0) for r in renders)
    c = counts(doc)
    return (f"{_plural(len(renders), 'act')}, {frames} frames at {doc.get('samples')} samples, ProRes 422 HQ, "
            f"every join proven; {c['PASS']} render gates PASS (frames, fonts)"), doc


def st_score_plan(run: Run) -> tuple[str, dict]:
    code, doc, err = slate(run, "score", "plan")
    judge(run, "score plan", code, doc, err)
    plan = doc.get("plan") or {}
    return (f"{_plural(len(plan.get('parts') or []), 'part')} on {plan.get('model_id')}, "
            f"{_plural(len(plan.get('seeds') or []), 'seed')} each, from the brand's sound palette"), doc


def st_score_compose(run: Run) -> tuple[str, dict]:
    code, doc, err = slate(run, "score", "compose")
    judge(run, "score compose", code, doc, err)
    takes = doc.get("composed") or []
    if not takes:
        raise StageFailed("`slate score compose` composed nothing")
    estimate = (doc.get("estimate") or {}).get("amount")
    spent = "replayed: nothing spent" if doc.get("replay") else f"estimate {estimate} credits"
    return f"{_plural(len(takes), 'take')} composed ({spent})", doc


def st_score_assemble(run: Run) -> tuple[str, dict]:
    code, doc, err = slate(run, "score", "assemble")
    judge(run, "score assemble", code, doc, err)
    return (f"{run.rel(run.film / str(doc.get('file')))}: {float(doc.get('seconds') or 0):.2f} s, every join, "
            "landing and gap checked"), doc


def st_mix(run: Run) -> tuple[str, dict]:
    code, doc, err = slate(run, "mix")
    judge(run, "mix", code, doc, err)
    rep = doc.get("report") or {}
    placed = ((rep.get("inputs") or {}).get("voice") or {}).get("placed") or []
    if not placed or not all(str(p.get("file", "")).endswith(".polished.wav") for p in placed):
        raise StageFailed("the mix didn't use the polished takes")
    if not rep.get("pass"):
        raise StageFailed(f"the mix missed its targets: {rep.get('final_lufs')} LUFS, "
                          f"{rep.get('final_true_peak_dbtp')} dBTP")
    under = abs(float(rep.get("music_vs_voice_lu") or 0))
    return (f"{rep.get('final_lufs')} LUFS, {rep.get('final_true_peak_dbtp')} dBTP, four stems that sum to it; "
            f"the voice is the polished takes, the score {under:g} LU under it, ducked {rep.get('duck_db')} dB"), doc


def st_verify(run: Run) -> tuple[str, dict]:
    code, doc, err = slate(run, "verify")
    opened = judge(run, "verify", code, doc, err, gate_stage=True)
    return _gate_line(doc, opened, "sound gates", "the balance against the voice included"), doc


def st_voice_check(run: Run) -> tuple[str, dict]:
    code, doc, err = slate(run, "voice", "check")
    opened = judge(run, "voice check", code, doc, err, gate_stage=True)
    return _gate_line(doc, opened, "voice gates"), doc


def st_master(run: Run) -> tuple[str, dict]:
    code, doc, err = slate(run, "master", "--ffmpeg", timeout=LONG_TIMEOUT_S)
    judge(run, "master --ffmpeg", code, doc, err, gate_stage=True)
    masters = doc.get("masters") or []
    if not doc.get("passed") or not masters:
        raise StageFailed("the masters didn't pass their gates")
    kinds = " and ".join(m.get("kind", "?").replace("prores_422_hq", "ProRes 422 HQ").replace("h264", "H.264")
                         for m in masters)
    psnr = (next((g for g in gates_of(doc) if g.get("gate") == "master.psnr_h264"), {}).get("details") or {})
    worst_db = (psnr.get("summary") or {}).get("min_db")
    return (f"{kinds}, {masters[0].get('frames')} frames each, with the mix; {counts(doc)['PASS']} master gates PASS"
            + (f", the H.264 at worst {worst_db} dB against the renders" if worst_db is not None else "")), doc


def st_qa(run: Run) -> tuple[str, dict]:
    code, doc, err = slate(run, "qa", timeout=LONG_TIMEOUT_S)
    opened = judge(run, "qa", code, doc, err, gate_stage=True)
    ocr = next((g for g in gates_of(doc) if g.get("gate") == "ocr"), {})
    how = "OCR on Apple Vision" if run.vision else "no OCR engine here"
    return _gate_line(doc, opened, "film gates", f"{how}: {ocr.get('summary', 'not run')}"), doc


def st_review(run: Run) -> tuple[str, dict]:
    code, doc, err = slate(run, "review", "--to", "downloads")
    judge(run, "review", code, doc, err)
    sent = doc.get("sent") or {}
    files = doc.get("files") or {}
    folder = Path(str(sent.get("folder") or ""))
    if not folder.is_dir() or run.root.resolve() not in folder.resolve().parents:
        raise StageFailed(f"the review packet's copy went to {folder}, not the demo's scratch folder")
    sheets = len(files.get("contact_sheets") or [])
    return (f"packet v{doc.get('version')}: phone cut, {_plural(sheets, 'contact sheet')}, review.md; copied to a "
            "scratch folder, no message sent"), doc


def st_deliver(run: Run) -> tuple[str, dict]:
    code, doc, err = slate(run, "deliver", "--draft", timeout=LONG_TIMEOUT_S)
    judge(run, "deliver --draft", code, doc, err)
    if doc.get("final") is not False:
        raise StageFailed("a demo delivery came out final")
    why = [re.split(r"[:(]", str(w), maxsplit=1)[0].strip() for w in doc.get("why_not_final") or []]
    return f"{doc.get('files')} files in {doc.get('folder')}; not final: {'; '.join(why)}", doc


def st_deliverables(run: Run) -> tuple[str, dict]:
    """Read the manifest back: it must say the delivery isn't final (and, in replay, that replayed assets are why),
    and every file it lists must be there with its sha256."""
    deliver = run.docs.get("deliver") or {}
    manifest_rel = deliver.get("manifest")
    if not manifest_rel:
        raise StageFailed("the delivery named no manifest")
    manifest = jsonio.read(run.film / manifest_rel)
    problems = []
    if manifest.get("final") is not False:
        problems.append("the manifest calls the delivery final")
    why = " ".join(map(str, manifest.get("why_not_final") or []))
    if not why:
        problems.append("the manifest doesn't say why the delivery isn't final")
    if run.replay and (not manifest.get("replayed") or "replayed" not in why):
        problems.append("the manifest doesn't say the replayed stand-ins keep it from being final")
    folder = run.film / str(manifest.get("folder"))
    listed = manifest.get("files") or []
    for f in listed:
        p = folder / str(f.get("file"))
        if not p.is_file():
            problems.append(f"{f.get('file')} is missing")
        elif jsonio.sha256(p) != f.get("sha256"):
            problems.append(f"{f.get('file')} doesn't match its sha256")
    kinds = {str(f.get("kind", "")).split(":")[0] for f in listed}
    for need in ("master", "mix"):
        if need not in kinds:
            problems.append(f"no {need} in the delivery")
    if problems:
        raise StageFailed("; ".join(problems))
    replayed = len(manifest.get("replayed") or [])
    return (f"manifest read back: {_plural(len(listed), 'file')} there with their sha256; final: no"
            + (f" ({_plural(replayed, 'replayed stand-in')} listed)" if replayed else "")), {
        "manifest": manifest_rel, "folder": run.rel(folder), "files": len(listed), "final": False,
        "why_not_final": manifest.get("why_not_final"), "replayed": manifest.get("replayed") or []}


STAGES: list[Stage] = [
    Stage("new", st_new, "slate new slate-demo-<n> --brand demo --format social-reel --runtime 15 --mode draft"),
    Stage("script build", st_script, "the demo's script.json, then slate script build"),
    Stage("voice tts", st_tts, "slate voice tts (replayed)"),
    Stage("voice polish", st_polish, "slate voice polish"),
    Stage("voice align", st_align, "slate voice align"),
    Stage("voice analyze", st_analyze,
          f"slate voice analyze {AB_SHOT} --stage polished --ab <its raw take> --sheet --spectrum"),
    Stage("timing", st_timing, "slate timing"),
    Stage("voice captions", st_captions, "slate voice captions"),
    Stage("scenes", st_scenes, "act 1 the kit's placeholder, act 2 built from the kit"),
    Stage("emit-events", st_emit, "slate emit-events"),
    Stage("check", st_check, "slate check all"),
    Stage("render", st_render, f"slate render --samples {SAMPLES}"),
    Stage("score plan", st_score_plan, "slate score plan"),
    Stage("score compose", st_score_compose, "slate score compose (replayed)"),
    Stage("score assemble", st_score_assemble, "slate score assemble"),
    Stage("mix", st_mix, "slate mix"),
    Stage("verify", st_verify, "slate verify"),
    Stage("voice check", st_voice_check, "slate voice check"),
    Stage("master", st_master, "slate master --ffmpeg"),
    Stage("qa", st_qa, "slate qa (every gate)"),
    Stage("review", st_review, "slate review --to downloads (a scratch folder; nothing is sent)"),
    Stage("deliver", st_deliver, "slate deliver --draft"),
    Stage("deliverables", st_deliverables, "read the manifest back and check every file"),
]


# ---------------------------------------------------------------------------------------------------- set-up
def prerequisites() -> None:
    """What every stage leans on, checked before the first one starts, each with its fix."""
    for tool in ("ffmpeg", "ffprobe", "node"):
        if not procs.which(tool):
            raise MissingPrerequisite(f"The demo needs {tool}, and it isn't installed.",
                                      hint="slate doctor --install (in your own Terminal) sets up the free tools")
    if not (paths.ENGINE_SRC / "node_modules" / "remotion").is_dir() and not paths.engine_root().is_dir():
        raise MissingPrerequisite("The Node engine isn't installed, so nothing can render.",
                                  hint="slate engine install")
    if not (paths.BRANDS_REPO / BRAND / "brand.json").is_file():
        raise MissingPrerequisite(f"The neutral brand pack brands/{BRAND} is missing from this checkout.",
                                  hint="slate update (or reinstall Slate)")
    if not (ASSETS / "script.json").is_file():
        raise MissingPrerequisite("The demo's script and scenes are missing from this install.", hint="slate update")


def vision_available() -> bool:
    """Whether Apple Vision OCR can run here (Slate's ocr extra). Checked without loading Vision itself."""
    return sys.platform == "darwin" and importlib.util.find_spec("ocrmac") is not None


def consent_to_spend(*, ask: Callable[[str], str] = input) -> str:
    """`--live` spends: a person in a Terminal is asked first; an agent needs the person's explicit SLATE_LIVE=1."""
    if not secrets.present("elevenlabs"):
        raise MissingPrerequisite("`slate demo --live` voices and scores the film on ElevenLabs, and no ElevenLabs key "
                                  "is connected.", hint="slate connect elevenlabs (in your own Terminal)")
    what = ("voice four lines (about 130 characters) and compose a 15-second score twice per act on ElevenLabs, "
            "roughly 1,000 credits in all, each call through the spend ledger and the film's budget")
    agent = procs.agent_context()
    if agent or not sys.stdin.isatty():
        if cli.caller_env("SLATE_LIVE") == "1":
            return f"a person's SLATE_LIVE=1 for this run ({agent or 'no Terminal'})"
        where = f"under {agent}" if agent else "without a Terminal to ask in"
        raise SpendRefused(f"`slate demo --live` would {what}, and it's running {where}. Nothing was spent.",
                           hint="Run it in your own Terminal, or set SLATE_LIVE=1 for this one run if you want it "
                                "to spend: SLATE_LIVE=1 slate demo --live")
    answer = ask(f"slate demo --live will {what}. Type yes to spend: ")
    if answer.strip().lower() != "yes":
        raise SpendRefused("Nothing was spent: the demo needs a yes to run live.", hint="slate demo (replayed, free)")
    return "yes, typed in this Terminal"


def next_film_id(films: Path) -> str:
    taken = [int(m.group(1)) for p in (films.iterdir() if films.is_dir() else [])
             if (m := re.fullmatch(r"slate-demo-(\d+)", p.name))]
    return f"slate-demo-{max(taken, default=0) + 1}"


def prepare(*, live: bool, keep: bool) -> Run:
    root = Path(tempfile.mkdtemp(prefix="slate-demo-"))
    env = {"SLATE_FAKE_VENDORS": "0" if live else "1", "SLATE_LIVE": "1" if live else "0",
           "SLATE_DOWNLOADS": str(root / "review"), "BROWSER": "none"}
    if keep:
        films = paths.films()
        films.mkdir(parents=True, exist_ok=True)
        film_id = next_film_id(films)
    else:
        films = root / "films"
        films.mkdir()
        film_id = "slate-demo-1"
        env["SLATE_FILMS"] = str(films)
        if not live:  # replayed: nothing worth keeping, so its records stay out of your ledger and caches
            env["SLATE_DATA"] = str(root / "data")
    if keep or live:
        env["SLATE_DATA"] = str(paths.data())  # a kept film, or real spend, belongs in your own records
        env["SLATE_FILMS"] = str(films)
    return Run(film_id=film_id, film=films / film_id, root=root, keep=keep, live=live, env=env,
               vision=vision_available())


# ---------------------------------------------------------------------------------------------------- the run
def _fmt_s(seconds: float) -> str:
    return f"{seconds:.1f} s" if seconds < 60 else f"{int(seconds // 60)} min {int(round(seconds % 60))} s"


def run_stages(run: Run, *, say: Callable[[str], None]) -> bool:
    width = max(len(s.key) for s in STAGES)
    for i, stage in enumerate(STAGES, 1):
        t0 = time.monotonic()
        try:
            line, doc = stage.run(run)
            ok, error = True, None
        except StageFailed as exc:
            line, doc, ok, error = str(exc).splitlines()[0], {}, False, str(exc)
        except Exception as exc:  # noqa: BLE001 - one stage's crash is that stage's failure, reported like the rest
            line, doc, ok, error = f"{type(exc).__name__}: {exc}", {}, False, f"{type(exc).__name__}: {exc}"
        seconds = time.monotonic() - t0
        run.docs[stage.key] = doc
        rec = {"n": i, "stage": stage.key, "ok": ok, "seconds": round(seconds, 2), "result": line}
        if not ok:
            rec["error"] = error
        run.stages.append(rec)
        say(f"  {i:>2}  {stage.key:<{width}}  {'ok' if ok else 'FAILED':<6} {_fmt_s(seconds):>9}  {line}")
        if not ok:
            for extra in (error or "").splitlines()[1:]:
                say(f"      {extra}")
            return False
    return True


def not_passed(run: Run) -> list[dict]:
    """Every gate the demo saw that isn't PASS, with why it was expected (the run stops at any other)."""
    out, seen = [], set()
    for key, doc in run.docs.items():
        for g in gates_of(doc) if isinstance(doc, dict) else []:
            if g.get("status") != "PASS" and g.get("gate") not in seen:
                seen.add(g.get("gate"))
                out.append({"stage": key, "gate": g.get("gate"), "status": g.get("status"),
                            "summary": g.get("summary"), "why": expected(run, g)})
    return out


def demo(*, live: bool = False, keep: bool = False, say: Callable[[str], None] = print,
         ask: Callable[[str], str] = input) -> dict:
    """Make the demo film. Returns the run's record ({ok, film, stages, not_passed, deliverables, kept, …})."""
    prerequisites()
    consent = consent_to_spend(ask=ask) if live else None
    run = prepare(live=live, keep=keep)
    started = time.monotonic()
    say(f"slate demo: a {RUNTIME_S}-second 9:16 reel for brands/{BRAND}, every stage for real, "
        + ("vendors live (it spends)" if live else "vendors replayed (nothing is spent)"))
    say(f"  film: {run.film}")
    ok = run_stages(run, say=say)
    total = time.monotonic() - started
    record = {"ok": ok, "film": run.film_id, "dir": str(run.film), "kept": keep, "replay": run.replay,
              "live_consent": consent, "vision": run.vision, "seconds": round(total, 1), "stages": run.stages,
              "not_passed": not_passed(run), "deliverables": run.docs.get("deliverables") or None,
              "made_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    if ok and keep:
        jsonio.write_atomic(run.film / "qa" / "demo.json", jsonio.mark_generated(record, "slate demo"))
    removed = False
    if ok and not keep:
        shutil.rmtree(run.root, ignore_errors=True)
        removed = True
    elif not keep:
        record["scratch"] = str(run.root)
    record["removed"] = removed
    passed = sum(1 for s in run.stages if s["ok"])
    if ok:
        unchecked = record["not_passed"]
        say(f"Every stage passed: {passed} of {len(STAGES)} in {_fmt_s(total)}.")
        if unchecked:
            reasons: dict[str, list[str]] = {}
            for g in unchecked:
                reasons.setdefault(str(g["why"]), []).append(str(g["gate"]))
            for why, names in reasons.items():
                say(f"  unchecked, as expected: {', '.join(names)}: {why}")
        say(f"  {'kept: ' + str(run.film) if keep else 'the scratch film was removed (slate demo --keep keeps it)'}")
    else:
        failed = next(s for s in run.stages if not s["ok"])
        say(f"Stopped at stage {failed['n']} of {len(STAGES)} ({failed['stage']}) after {_fmt_s(total)}.")
        say(f"  everything so far is in {run.film if keep else run.root}")
    return record


# ---------------------------------------------------------------------------------------------------- the command
DESCRIPTION = __doc__.split("\n\n", 1)[1].split("\nExit codes:")[0].strip()


def cmd_demo(args: argparse.Namespace) -> int:
    live = bool(cli.flag(args, "live", False))
    keep = bool(getattr(args, "keep", False))
    if cli.flag(args, "film"):
        raise UsageError("`slate demo` makes its own film; --film doesn't apply.")
    if cli.flag(args, "dry_run", False):
        lines = [f"slate demo would make a {RUNTIME_S}-second 9:16 reel for brands/{BRAND} "
                 + ("live (it spends, after a yes)" if live else "with every vendor replayed (nothing is spent)")
                 + (f", kept in {paths.films()}" if keep else ", in a scratch folder removed at the end") + ":"]
        lines += [f"  {i:>2}  {s.key}: {s.what}" for i, s in enumerate(STAGES, 1)]
        log.result({"dry_run": True, "live": live, "keep": keep,
                    "stages": [{"n": i, "stage": s.key, "does": s.what} for i, s in enumerate(STAGES, 1)]},
                   "\n".join(lines))
        return 0
    quiet = bool(cli.flag(args, "quiet", False))

    def say(text: str) -> None:
        if quiet:
            return
        print(text, file=sys.stderr if log.json_mode() else sys.stdout, flush=True)

    record = demo(live=live, keep=keep, say=say)
    if log.json_mode():
        log.emit(record)
    elif quiet:
        print(f"slate demo: {'every stage passed' if record['ok'] else 'a stage failed'} "
              f"({record['seconds']} s)", flush=True)
    return 0 if record["ok"] else 1


def register(sub) -> None:
    p = cli.add_command(sub, "demo", cmd_demo,
                        "a neutral 15-second reel end to end through every stage (replayed: nothing is spent)",
                        description=DESCRIPTION,
                        epilog="Exit codes: 0 every stage passed, 1 a stage failed, 3 something the demo needs is "
                               "missing, 4 spend refused (--live without a person's yes).")
    p.add_argument("--keep", action="store_true",
                   help="keep the film in your films folder as slate-demo-<n> (default: a scratch folder, removed)")
