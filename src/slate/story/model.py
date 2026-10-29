"""script/script.json as data: the one hand-edited source, planned into shots with ids and lengths.

The script is acts of shots (or "lines"; the same thing): id, vo, on_screen, picture, sources, claims, notes, plus
optional transition, build, sound, enter_token, lead, hold, dur, quotes and events. Two things are derived here:

Ids. By default a shot's id is the one written in the script. With `"ids": "renumber"` (film C's pattern) the written
ids are draft ids: shots are numbered <prefix>01… in order on output, and every draft id mentioned inside the acts is
remapped with them, so a cut or an insertion never leaves a stale reference.

Lengths. Film C's rule, generalized: a line starts `lead` into its shot, and the next shot starts `tail` after the line
ends (`act_tail` at an act break), plus `hold` for the visual beats. Until the takes exist the speech is planned at
`speech_rate` words a second; once they do (`slate script build --from-takes`) it's each take's real length, so every
read fits its shot ("retimed so every read fits its shot", film A draft-2.4). A shot never goes over the pace ceiling:
at least words / max_pace x 1.04. A shot with no voice takes its written `dur`, or its `hold`.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from slate import jsonio
from slate.errors import MissingPrerequisite
from slate.timing import spoken

SCRIPT = Path("script") / "script.json"
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


def load_script(film_dir: Path) -> dict:
    path = Path(film_dir) / SCRIPT
    if not path.is_file():
        raise MissingPrerequisite("There's no script yet: script/script.json is the one hand-edited source.",
                                  hint="Write script/script.json (acts of shots: id, vo, on_screen, picture, sources).")
    return jsonio.read(path)


def text_of(value: Any, joiner: str = " · ") -> str:
    """On-screen text may be a string or a list of lines; the storyboard keeps one string."""
    if value is None:
        return ""
    if isinstance(value, list):
        return joiner.join(str(v) for v in value if str(v).strip())
    return str(value)


def list_of(value: Any) -> list:
    if value is None or value == "":
        return []
    return list(value) if isinstance(value, list) else [value]


@dataclass
class PlannedShot:
    id: str
    source_id: str
    act: int
    act_name: str
    data: dict
    act_end: bool
    lead: float = 0.0
    hold: float = 0.0
    dur: float = 0.0
    dur_source: str = "planned"  # script | planned | take | hold
    words: int = 0
    take: dict | None = None

    @property
    def vo(self) -> str:
        return (self.data.get("vo") or "").strip()


@dataclass
class Plan:
    script: dict
    shots: list[PlannedShot]
    acts: list[dict]
    timing: dict
    lang: str = "en"
    mode: str = "planning"  # planning | takes
    ids_mode: str = "as-written"
    id_map: dict[str, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    missing_takes: list[str] = field(default_factory=list)

    @property
    def runtime(self) -> float:
        return round(sum(s.dur for s in self.shots), 6)

    def by_id(self) -> dict[str, PlannedShot]:
        return {s.id: s for s in self.shots}


def act_shots(act: dict) -> list[dict]:
    return list(act.get("shots") if act.get("shots") is not None else act.get("lines") or [])


def structure_problems(script: dict) -> list[str]:
    """What stops the script from being planned at all (before any gate can judge it)."""
    problems = []
    acts = script.get("acts")
    if not isinstance(acts, list) or not acts:
        return ["the script has no acts yet: write the first act in script/script.json (acts of shots with id, vo, "
                "on_screen, picture and sources)"]
    seen: dict[str, str] = {}
    for a_i, act in enumerate(acts, 1):
        if not isinstance(act, dict) or not act.get("name"):
            problems.append(f"act {a_i} needs a name")
            continue
        shots = act_shots(act)
        if not shots:
            problems.append(f"{act['name']}: an act needs at least one shot")
        for sh in shots:
            sid = str(sh.get("id") or "")
            if not sid:
                problems.append(f"{act['name']}: a shot has no id")
                continue
            if not ID_RE.match(sid):
                problems.append(f"{sid}: shot ids start with a letter and use letters, digits, '_', '-' or '.'")
            if sid in seen:
                problems.append(f"{sid} is used twice ({seen[sid]} and {act['name']})")
            seen[sid] = act["name"]
    if script.get("ids") not in (None, "as-written", "renumber"):
        problems.append(f"ids must be 'as-written' or 'renumber', not {script.get('ids')!r}")
    return problems


def renumber_map(script: dict) -> dict[str, str]:
    """Draft id -> output id (<prefix>01… in order). Identity when ids are as written."""
    order = [str(sh["id"]) for act in script.get("acts") or [] for sh in act_shots(act)]
    if script.get("ids") != "renumber":
        return {sid: sid for sid in order}
    prefix = str(script.get("id_prefix") or re.match(r"^[A-Za-z]*", order[0]).group(0) or "S") if order else "S"
    width = max(2, len(str(len(order))))
    return {old: f"{prefix}{i:0{width}d}" for i, old in enumerate(order, 1)}


def remap_text(value: Any, mapping: dict[str, str]) -> Any:
    """Replace draft ids mentioned inside text with their new ids (film C's remap), leaving other text alone."""
    moved = {k: v for k, v in mapping.items() if k != v}
    if not moved:
        return value
    pattern = re.compile(r"(?<![\w.-])(" + "|".join(re.escape(k) for k in sorted(moved, key=len, reverse=True)) +
                         r")(?![\w-])")

    def fix(v: Any) -> Any:
        if isinstance(v, str):
            return pattern.sub(lambda m: moved[m.group(1)], v)
        if isinstance(v, list):
            return [fix(x) for x in v]
        if isinstance(v, dict):
            return {k: fix(x) for k, x in v.items()}
        return v

    return fix(value)


# The motion law's transitions (slate.qa.checks.picture.TRANSITIONS sorts a shot's own words into them).
TRANSITION_KINDS = ("carry", "portal", "mask", "whip", "ui_cut", "music_cut", "seam")


def transition_words(value) -> str:
    """A shot's transition as a person reads it: its own words, or "<kind>: <note>" for the {kind, note} form."""
    if isinstance(value, dict):
        kind = str(value.get("kind") or "").strip()
        note = str(value.get("note") or "").strip()
        return f"{kind}: {note}" if kind and note else (kind or note)
    return str(value or "")


def plan(script: dict, *, timing: dict, takes: dict | None = None, use_takes: bool = False,
         lang: str = "en", default_enter_token: str | None = None) -> Plan:
    """Ids and lengths for every shot. `takes` maps output id -> Take (or {"read_seconds", …}) when voicing exists."""
    problems = structure_problems(script)
    result = Plan(script=script, shots=[], acts=[], timing=timing, lang=lang,
                  mode="takes" if use_takes else "planning", ids_mode=script.get("ids") or "as-written",
                  problems=problems)
    if problems and any("no acts" in p or "no id" in p for p in problems):
        return result
    mapping = renumber_map(script)
    result.id_map = mapping
    acts = remap_text(copy.deepcopy(script.get("acts") or []), mapping) if result.ids_mode == "renumber" else \
        copy.deepcopy(script.get("acts") or [])
    originals = [sh for act in script.get("acts") or [] for sh in act_shots(act)]
    k = 0
    for a_i, act in enumerate(acts, 1):
        shots = act_shots(act)
        result.acts.append({key: v for key, v in act.items() if key not in ("shots", "lines")})
        for pos, sh in enumerate(shots):
            source_id = str(originals[k].get("id"))
            k += 1
            data = dict(sh)
            # Count bindings refer to exact ids in the emitted visual export, not draft shot names. Text keeps the
            # existing shot-name remap, but a colliding visual event id must not be rewritten with the shot id.
            original_reading = originals[k - 1].get("reading")
            reading = data.get("reading")
            if isinstance(original_reading, dict) and isinstance(reading, dict):
                original_counts, counts = original_reading.get("count_events"), reading.get("count_events")
                if isinstance(original_counts, list) and isinstance(counts, list):
                    for original, count in zip(original_counts, counts):
                        if isinstance(original, dict) and isinstance(count, dict) and "events" in original:
                            count["events"] = copy.deepcopy(original["events"])
            data["on_screen"] = text_of(sh.get("on_screen"))
            data["sources"] = [str(s) if not isinstance(s, dict) else s for s in list_of(sh.get("sources",
                                                                                                   sh.get("source")))]
            data["claims"] = list_of(sh.get("claims"))
            data["quotes"] = list_of(sh.get("quotes"))
            if default_enter_token and not data.get("enter_token"):
                data["enter_token"] = default_enter_token
            act_end = bool(sh.get("act_end", pos == len(shots) - 1))
            ps = PlannedShot(id=mapping.get(source_id, source_id), source_id=source_id, act=a_i,
                             act_name=str(act.get("name")), data=data, act_end=act_end)
            _length(ps, timing, takes or {}, use_takes, lang, result)
            result.shots.append(ps)
    return result


def _length(ps: PlannedShot, timing: dict, takes: dict, use_takes: bool, lang: str, result: Plan) -> None:
    sh = ps.data
    n = spoken.word_count(ps.vo, lang)
    ps.words = n
    ps.lead = float(sh["lead"]) if sh.get("lead") is not None else float(timing["lead"])
    ps.hold = float(sh.get("hold") or 0.0)
    tail = float(timing["act_tail"] if ps.act_end else timing["tail"])
    written = sh.get("dur")
    if n == 0:
        if written is not None:
            ps.dur, ps.dur_source = float(written), "script"
        elif sh.get("hold") is not None:
            ps.dur, ps.dur_source = round(ps.hold, 2), "hold"
        else:
            result.problems.append(f"{ps.id} has no voice, so it needs a dur (seconds) or a hold")
            ps.dur, ps.dur_source = 0.0, "missing"
        return
    take = takes.get(ps.id) if use_takes else None
    if take is not None:
        speech = float(take.read_seconds if hasattr(take, "read_seconds") else take["read_seconds"])
        ps.dur_source = "take"
        ps.take = {"file": getattr(take, "rel", None) if not isinstance(take, dict) else take.get("file"),
                   "read_seconds": round(speech, 3)}
    elif written is not None:
        ps.dur, ps.dur_source = float(written), "script"
        if use_takes:
            result.missing_takes.append(ps.id)
        return
    else:
        speech = n / float(timing["speech_rate"])
        if use_takes:
            result.missing_takes.append(ps.id)
    dur = ps.lead + speech + tail + ps.hold
    dur = max(dur, n / float(timing["max_pace"]) * float(timing["pace_headroom"]))
    ps.dur = round(dur, 2)
