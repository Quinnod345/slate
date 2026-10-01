"""Golden data: the generalized tools rerun on films A, B and C's own inputs match what those films delivered (P181).

The film project is read-only here: every file is read from FILMROOT and every output goes to pytest's temp folders.
Brand values the comparison needs (the films' eases, banned words, duration scale) are read from FILMROOT's own tools at
test time, so none of them is ever copied into this repo. Tolerances and deliberate differences, stated once:

- shots.json: every frame, second and rounding field is exact for all three films. Text fields are exact for B and C.
  A's delivered shots.json predates its draft-2.5 edit, which re-voiced four lines (their text and words changed, their
  lengths didn't); those shots are found from the data (the embedded line differs from vo_words.json) and are the only
  differences. C's shots.json predates its vo_words.json, so it embeds no words; the rerun embeds them.
- vo_cues.json: every field but the free-text note is exact.
- vo_words.json: rebuilt from each film's Whisper JSON and takes, every word's start, end, Whisper start and snap are
  exact. Slate splits a number the script spells out into its spoken words (film C did this by hand for C05 and C19;
  the rerun reproduces those exactly); A and B left theirs whole, and there the split words tile the delivered token
  exactly. B12's delivered words drop a dash Whisper returned (a later hand edit: its `heard` still has the dash);
  Slate keeps every token.
- timeline.json: exact for A (all eleven shots with events); B and C have no authored events.
- storyboard: C's lengths re-derived from its takes (`--from-takes`) are exact, and so are its renumbered ids.
"""

from __future__ import annotations

import ast
import copy
import json
import re
from pathlib import Path

import pytest
from storyfix import write_json

from slate import jsonio, procs
from slate.cli import run_cli
from slate.clock import Clock
from slate.story.gates import check_script
from slate.story.inputs import DEFAULT_TIMING
from slate.story.model import plan
from slate.timing import audio, ease
from slate.timing.builder import check_reads
from slate.timing.cues import make_cues
from slate.timing.shots import check_frames, make_shots
from slate.timing.takes import Take
from slate.timing.timeline import resolve
from slate.timing.vowords import words_for_take
from slate.timing.words import resolve_tokens

FILMS = {"A": ("build",), "B": ("build/B",), "C": ("build/C",)}  # each film's data folder; takes come from its cues
TIMING_FIELDS = ["id", "act", "act_name", "frame_in", "frame_out", "frames", "sec_in", "sec_out", "storyboard_sec_in",
                 "storyboard_dur", "rounding_in_sec", "rounding_dur_sec", "enter_token", "vo_take", "vo_lead_sec",
                 "vo_frame"]
TEXT_FIELDS = ["vo", "on_screen", "picture", "transition_out", "build", "source", "sound"]
EXACT = 5e-4  # word times are stored to the millisecond


def read(fr: Path, rel: str):
    return json.loads((fr / rel).read_text(encoding="utf-8"))


def constants(path: Path, names: set[str]) -> dict:
    """Top-level constants of a FILMROOT tool, evaluated without running it (no imports, no side effects)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    ns: dict = {"re": re}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in names:
            ns[target.id] = eval(compile(ast.Expression(node.value), str(path), "eval"), dict(ns))  # noqa: S307
        elif isinstance(target, ast.Tuple) and {e.id for e in target.elts} & names:
            values = eval(compile(ast.Expression(node.value), str(path), "eval"), dict(ns))  # noqa: S307
            ns.update(zip([e.id for e in target.elts], values))
    return ns


def film_eases(fr: Path) -> dict:
    table = constants(fr / "pipeline/tools/timeline.py", {"EASE"})["EASE"]
    return {"linear": ease.LINEAR, **{k: tuple(v) for k, v in table.items()}}


def c_script(fr: Path) -> tuple[dict, dict]:
    """Film C's script data (its generator's ACTS) as a Slate script, and its timing constants."""
    ns = constants(fr / "build/C/tools/c_storyboard.py", {"M", "ACTS", "SPEECH", "MAX_PACE", "LEAD", "TAIL", "ACT_TAIL"})
    script = {"ids": "renumber", "id_prefix": "C", "defaults": {"enter_token": "T3"},
              "acts": [{"name": a["name"], "job": a["job"], "shots": copy.deepcopy(a["shots"])} for a in ns["ACTS"]]}
    timing = dict(DEFAULT_TIMING, lead=ns["LEAD"], tail=ns["TAIL"], act_tail=ns["ACT_TAIL"], speech_rate=ns["SPEECH"],
                  max_pace=ns["MAX_PACE"])
    return script, timing


def leads(fr: Path, film: str) -> tuple[float, dict]:
    """The film-level lead each film voiced with, and any per-line lead its script sets (C01's, in C's data)."""
    lead = float(read(fr, f"{FILMS[film][0]}/vo_cues.json")["lead"])
    per_shot = {}
    if film == "C":
        script, _t = c_script(fr)
        order = [s for a in script["acts"] for s in a["shots"]]
        per_shot = {f"C{i:02d}": s["lead"] for i, s in enumerate(order, 1) if "lead" in s}
    return lead, per_shot


def cue_takes(fr: Path, film: str) -> dict[str, Take]:
    cues = read(fr, f"{FILMS[film][0]}/vo_cues.json")["cues"]
    return {c["shot"]: Take(shot=c["shot"], path=fr / c["file"], rel=c["file"], trim=c.get("dur")) for c in cues}


def rebuilt_shots(fr: Path, film: str) -> tuple[dict, dict]:
    d = FILMS[film][0]
    delivered = read(fr, f"{d}/shots.json")
    mine = make_shots(read(fr, f"{d}/storyboard.json"), clock=Clock(delivered["fps_num"], delivered["fps_den"]),
                      width=delivered["width"], height=delivered["height"], cues=read(fr, f"{d}/vo_cues.json"),
                      words=read(fr, f"{d}/vo_words.json"), events=read(fr, f"{d}/events.json"))
    return mine, delivered


# --- shots.json -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("film", ["A", "B", "C"])
def test_shots_match_the_delivered_shots(filmroot, film):
    mine, delivered = rebuilt_shots(filmroot, film)
    clock = Clock(delivered["fps_num"], delivered["fps_den"])
    for key in ("fps_num", "fps_den", "width", "height", "storyboard_seconds", "total_frames", "total_seconds"):
        assert mine[key] == delivered[key], key
    assert [{k: a[k] for k in ("index", "name", "shots")} for a in mine["acts"]] == delivered["acts"]
    assert len(mine["shots"]) == len(delivered["shots"])
    words_now = read(filmroot, f"{FILMS[film][0]}/vo_words.json")["shots"]
    revoiced = {s["id"] for s in delivered["shots"] if "vo_words" in s and s["vo_words"]["line"] != words_now[s["id"]]["line"]}
    for a, b in zip(mine["shots"], delivered["shots"], strict=True):
        assert {k: a.get(k) for k in TIMING_FIELDS} == {k: b.get(k) for k in TIMING_FIELDS}, a["id"]
        assert a.get("events") == b.get("events"), a["id"]
        if a["id"] not in revoiced:
            assert {k: a.get(k) for k in TEXT_FIELDS} == {k: b.get(k) for k in TEXT_FIELDS}, a["id"]
        if "vo_words" in b and a["id"] not in revoiced:
            assert a["vo_words"] == b["vo_words"], a["id"]
    assert check_frames(mine, clock).passed and check_frames(delivered, clock).passed
    if film == "A":  # delivered before draft-2.5's four re-voiced lines; lengths unchanged
        assert len(revoiced) == 4 and mine["storyboard_version"] != delivered["storyboard_version"]
    else:
        assert not revoiced and mine["storyboard_version"] == delivered["storyboard_version"]
    if film == "C":  # C's shots.json was written before its vo_words.json existed
        assert not any("vo_words" in s for s in delivered["shots"]) and all("vo_words" in s for s in mine["shots"])


# --- vo_cues.json ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("film", ["A", "B", "C"])
def test_cues_match_the_delivered_cue_sheets(filmroot, film):
    d = FILMS[film][0]
    delivered = read(filmroot, f"{d}/vo_cues.json")
    storyboard = read(filmroot, f"{d}/storyboard.json")
    lead, per_shot = leads(filmroot, film)
    storyboard["timing"] = {"lead": lead}
    for act in storyboard["acts"]:
        for s in act["shots"]:
            if s["id"] in per_shot:
                s["lead"] = per_shot[s["id"]]
    mine, missing = make_cues(storyboard, cue_takes(filmroot, film), voice=delivered["voice"])
    assert not missing and (mine["lead"], mine["voice"]) == (delivered["lead"], delivered["voice"])
    keys = ["shot", "at", "type", "stem", "file", "gain", "fade_in", "fade_out", "dur"]
    assert [{k: c.get(k) for k in keys} for c in mine["cues"]] == [{k: c.get(k) for k in keys} for c in delivered["cues"]]


# --- vo_words.json ----------------------------------------------------------------------------------------------------------

def merged(words: list[dict]) -> list[dict]:
    """Fold split words back into the token they came from (parts after the first carry snap null)."""
    out: list[dict] = []
    for w in words:
        if w.get("snap") is None and out and out[-1]["whisper_start"] == w["whisper_start"]:
            out[-1] = {**out[-1], "end": w["end"], "split": True}
        else:
            out.append(dict(w))
    return out


def same_word(a: dict, b: dict) -> bool:
    return all(abs(a[k] - b[k]) <= EXACT for k in ("start", "end", "whisper_start")) and (
        (a["snap"] is None and b["snap"] is None) or abs(a["snap"] - b["snap"]) <= EXACT)


@pytest.mark.parametrize("film", ["A", "B", "C"])
def test_word_timings_match_the_delivered_word_timings(filmroot, film):
    if not procs.which("ffmpeg"):
        pytest.skip("rebuilding word timings decodes the takes with ffmpeg, which isn't installed")
    d = FILMS[film][0]
    delivered = read(filmroot, f"{d}/vo_words.json")["shots"]
    splits, dash_edits = set(), set()
    for sid, rec in delivered.items():
        result = read(filmroot, f"{d}/vo_whisper/{sid}.json")
        db, step = audio.envelope(audio.decode_mono(filmroot / rec["take"]))
        words, heard = words_for_take(result, rec["line"], db, step)
        assert heard == rec["heard"], sid
        toks = resolve_tokens(rec["line"], words, (0.0, words[-1]["end"]))
        assert all(t["exact"] for t in toks), (sid, [t["t"] for t in toks if not t["exact"]])  # every script word heard
        if len(words) == len(rec["words"]) and all(a["w"] == b["w"] and same_word(a, b)
                                                   for a, b in zip(words, rec["words"])):
            continue
        folded = merged(words)
        kept = [w for w in folded if any(ch.isalnum() for ch in w["w"]) or w.get("split")]
        if len(folded) != len(words) and len(folded) == len(rec["words"]):
            assert all(same_word(a, b) for a, b in zip(folded, rec["words"])), sid
            splits.add(sid)
        elif len(kept) == len(rec["words"]) and len(kept) < len(folded):
            assert all(same_word(a, b) for a, b in zip(kept, rec["words"])), sid
            assert any(not any(ch.isalnum() for ch in w["w"]) for w in words) and "–" in rec["heard"], sid
            dash_edits.add(sid)
        else:
            pytest.fail(f"{film} {sid}: rebuilt words differ from the delivered ones")
    recorded = {"A": ({"S09", "S11", "S33"}, set()), "B": ({"B08"}, {"B12"}), "C": (set(), set())}[film]
    assert (splits, dash_edits) == recorded


def test_film_cs_hand_splits_are_reproduced_exactly(filmroot):
    if not procs.which("ffmpeg"):
        pytest.skip("needs ffmpeg")
    delivered = read(filmroot, "build/C/vo_words.json")["shots"]
    for sid in ("C05", "C19"):
        result = read(filmroot, f"build/C/vo_whisper/{sid}.json")
        db, step = audio.envelope(audio.decode_mono(filmroot / delivered[sid]["take"]))
        words, _heard = words_for_take(result, delivered[sid]["line"], db, step)
        assert [w["w"] for w in words] == [w["w"] for w in delivered[sid]["words"]]
        assert all(same_word(a, b) for a, b in zip(words, delivered[sid]["words"]))


# --- timeline.json ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("film", ["A", "B", "C"])
def test_timeline_matches_the_delivered_timeline(filmroot, film):
    d = FILMS[film][0]
    mine_shots, delivered_shots = rebuilt_shots(filmroot, film)
    clock = Clock(delivered_shots["fps_num"], delivered_shots["fps_den"])
    words = read(filmroot, f"{d}/vo_words.json")
    with_tokens = {"shots": {sid: {**r, "tokens": resolve_tokens(r["line"], r["words"], (0.0, r["words"][-1]["end"]))}
                             for sid, r in words["shots"].items()}}
    delivered = read(filmroot, f"{d}/timeline.json")
    for source in (words, with_tokens):  # the heard words alone, and the resolved script tokens: the same frames
        doc, problems, misses = resolve(read(filmroot, f"{d}/events.json"), mine_shots, source, film_eases(filmroot),
                                        clock=clock)
        assert not problems and not misses
        assert doc["fps"] == delivered["fps"] and doc["shots"] == delivered["shots"]
    assert (len(delivered["shots"]) > 0) == (film == "A")


# --- the storyboard: C's lengths from its takes, A's reads fit --------------------------------------------------------------

def test_c_storyboard_retimed_from_its_takes_matches_the_delivered_storyboard(filmroot):
    script, timing = c_script(filmroot)
    takes = cue_takes(filmroot, "C")  # the takes C was timed from, by shot id
    planned = plan(script, timing=timing, takes=takes, use_takes=True, default_enter_token="T3")
    delivered = [s for a in read(filmroot, "build/C/storyboard.json")["acts"] for s in a["shots"]]
    assert not planned.problems and not planned.missing_takes
    assert [s.id for s in planned.shots] == [s["id"] for s in delivered]
    assert [s.dur for s in planned.shots] == [s["dur"] for s in delivered]
    for s, d in zip(planned.shots, delivered):
        for key in ("vo", "on_screen", "picture", "transition", "enter_token"):
            assert (s.data.get(key) or "") == (d.get(key) or ""), (s.id, key)
        assert "; ".join(s.data["sources"]) == d["source"], s.id
    assert round(planned.runtime, 2) == round(sum(s["dur"] for s in delivered), 2)


def test_every_one_of_as_reads_fits_its_shot(filmroot):
    if not procs.which("ffprobe"):
        pytest.skip("measuring mp3 takes needs ffprobe")
    delivered = read(filmroot, "build/shots.json")
    storyboard = read(filmroot, "build/storyboard.json")
    storyboard["timing"] = {"lead": 0.4, "min_tail": 0.25}  # film A's voice_script.py placement
    result = check_reads(storyboard, delivered, cue_takes(filmroot, "A"), Clock(delivered["fps_num"], delivered["fps_den"]))
    assert result.passed and result.summary == "all 36 reads fit their shots"


# --- the script gates agree with the films' own storyboard checks ------------------------------------------------------------

def test_script_gates_agree_with_film_as_automated_checks(filmroot):
    tool = constants(filmroot / "pipeline/tools/storyboard_build.py", {"BANNED_WORDS", "ENTRANCE"})
    brand = {"truth": {"banned_words": list(tool["BANNED_WORDS"])}, "motion": {"durations": tool["ENTRANCE"]}}
    sb = read(filmroot, "build/storyboard.json")
    script = {"defaults": {"enter_token": "T3"},
              "acts": [{"name": a["name"], "shots": [{k: v for k, v in s.items() if k != "source"} for s in a["shots"]]}
                       for a in sb["acts"]]}
    got = {r.gate: r for r in check_script(script, brand=brand, fmt={}, film={"targets": {"runtime_s": 360.65}})}
    for gate in ("script.pace", "script.read_time", "script.banned_words", "script.runtime"):
        assert got[gate].status == "PASS", (gate, got[gate].evidence[:3])
    assert "+0.5%" in got["script.runtime"].summary  # A's STORYBOARD.md: 6:02.5, +0.5% against the original
    _only_the_ratio_is_new(script, got, brand=brand, film={"targets": {"runtime_s": 360.65}})


def _only_the_ratio_is_new(script: dict, got: dict, **kw) -> None:
    """The films' storyboards predate the 40% gate (L-story-030), and a few of their shots carry more than 40% of the
    voice on screen without the voice saying it: a hero line over another line (A's S12), a product's own screen (its
    loading state, C's chat), an end card. Those are the gate's only findings (the 12-word cap holds everywhere), and
    each passes once it's declared for what it is, as the gate's evidence says to."""
    on = got["script.on_screen"]
    if on.status == "PASS":
        return
    over = [e for e in on.evidence if "(keywords, not transcripts)" in e]
    assert on.status == "FAIL" and over and not [e for e in on.evidence if "(at most " in e], on.evidence[:4]
    flagged = {e.split(":", 1)[0] for e in over}
    for a in script["acts"]:
        for sh in a["shots"]:
            if sh["id"] in flagged:
                sh["reading"] = {"labels": True, "reason": "the picture's own words, as delivered"}
    again = {r.gate: r for r in check_script(script, fmt={}, **kw)}
    assert again["script.on_screen"].status == "PASS", again["script.on_screen"].evidence[:3]


def test_script_gates_agree_with_film_cs_generator_checks(filmroot):
    ns = constants(filmroot / "build/C/tools/c_storyboard.py", {"BANNED", "FREE"})
    words = ns["BANNED"].pattern.split("(")[1].split(")")[0].split("|") + ["free"]
    sb = read(filmroot, "build/C/storyboard.json")
    first = sb["acts"][0]["shots"][0]["vo"]
    script = {"rules": {"vo_dashes": "fail", "first_line": first},
              "acts": [{"name": a["name"], "shots": [{k: v for k, v in s.items() if k != "source"} for s in a["shots"]]}
                       for a in sb["acts"]]}
    got = {r.gate: r for r in check_script(script, brand={"truth": {"banned_words": words}}, fmt={}, film={})}
    for gate in ("script.pace", "script.banned_words", "script.vo_dashes", "script.first_line"):
        assert got[gate].status == "PASS", (gate, got[gate].evidence[:3])
    _only_the_ratio_is_new(script, got, brand={"truth": {"banned_words": words}}, film={})


# --- the delivered files fit Slate's schemas ------------------------------------------------------------------------------------

@pytest.mark.parametrize("film", ["A", "B", "C"])
def test_the_delivered_files_fit_slates_schemas(filmroot, film):
    d = FILMS[film][0]
    for name, schema in (("shots", "shots"), ("vo_cues", "vo_cues"), ("vo_words", "vo_words"),
                         ("timeline", "timeline"), ("events", "events"), ("storyboard", "storyboard")):
        assert jsonio.validate(read(filmroot, f"{d}/{name}.json"), schema) == [], (film, name)


# --- the whole command, rerun on each film's inputs in a temp film folder -------------------------------------------------------

@pytest.mark.parametrize("film", ["A", "B", "C"])
def test_slate_timing_rerun_on_a_films_inputs(filmroot, film, slate_home):
    if not procs.which("ffmpeg"):
        pytest.skip("needs ffmpeg to decode the takes")
    d = FILMS[film][0]
    delivered = read(filmroot, f"{d}/shots.json")
    folder = slate_home["films"] / f"golden-{film.lower()}"
    write_json(folder / "film.json", {"id": f"golden-{film.lower()}", "brand": "golden", "language": "en",
                                      "fps_num": delivered["fps_num"], "fps_den": delivered["fps_den"],
                                      "width": delivered["width"], "height": delivered["height"],
                                      "voice": read(filmroot, f"{d}/vo_cues.json")["voice"]})
    write_json(slate_home["brands"] / "golden" / "brand.json",
               {"name": "golden", "motion": {"eases": {k: list(v) for k, v in film_eases(filmroot).items()}}})
    storyboard = read(filmroot, f"{d}/storyboard.json")
    lead, per_shot = leads(filmroot, film)
    storyboard["timing"] = {"lead": lead, "min_tail": 0.25}
    events = read(filmroot, f"{d}/events.json")["shots"]
    cues = {c["shot"]: c for c in read(filmroot, f"{d}/vo_cues.json")["cues"]}
    manifest = []
    for act in storyboard["acts"]:
        for s in act["shots"]:
            if s["id"] in per_shot:
                s["lead"] = per_shot[s["id"]]
            if s["id"] in events:
                s["events"] = events[s["id"]]
            if s["id"] in cues:
                take = filmroot / cues[s["id"]]["file"]
                manifest.append({"shot": s["id"], "file": str(take), **({"dur": cues[s["id"]]["dur"]}
                                                                         if "dur" in cues[s["id"]] else {})})
                whisper = read(filmroot, f"{d}/vo_whisper/{s['id']}.json")
                write_json(folder / "voice" / "align" / f"{s['id']}.json", {
                    "engine": {"name": "openai-whisper", "version": "films", "model": "turbo"}, "language": "en",
                    "sha256": jsonio.sha256(take), "text": whisper["text"], "segments": whisper["segments"]})
    write_json(folder / "script" / "storyboard.json", storyboard)
    write_json(folder / "voice" / "takes.json", {"takes": manifest})
    code, out, _ = run_cli(["timing", "--film", str(folder), "--json"])
    body = json.loads(out)
    assert code == 0 and body["ok"], body
    assert {g["gate"]: g["status"] for g in body["gates"]}["timing.frames"] == "PASS"
    mine = jsonio.read(folder / "timing" / "shots.json")
    for a, b in zip(mine["shots"], delivered["shots"], strict=True):
        assert {k: a.get(k) for k in TIMING_FIELDS if k != "vo_take"} == {k: b.get(k) for k in TIMING_FIELDS
                                                                           if k != "vo_take"}, a["id"]
        if "vo_take" in b:
            assert Path(a["vo_take"]).name == Path(b["vo_take"]).name
    timeline = jsonio.read(folder / "timing" / "timeline.json")
    assert timeline["shots"] == read(filmroot, f"{d}/timeline.json")["shots"]
    assert jsonio.read(folder / "timing" / "word_misses.json")["misses"] == []
    assert not any(folder.resolve().is_relative_to(p) for p in [filmroot.resolve()])
