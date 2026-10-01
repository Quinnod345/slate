"""The script gates (method stages 2–3): what a script has to pass before anything is built from it.

Each gate returns a GateResult (PASS, FAIL, REVIEW or UNCHECKED, never a quiet pass). `slate script build` refuses to
write anything when any of them FAILs.

- script.structure     the script can be planned: acts, unique ids, every silent shot has a length
- script.pace          every VO line at most 2.6 words a second of its shot (the format can change the ceiling)
- script.read_time     on-screen text holds long enough once it has settled: max(1.5 s, 0.4 s + 0.3 s x words),
                       measured as dur − entrance − 0.75 x entrance, and 2 s for a number (MOTION.md §7)
- script.on_screen     at most 12 on-screen words a shot, and at most 40% of the words its voice says (keywords, not
                       transcripts); on-screen text the voice says word for word, in order, is read along with it
                       (the films' VO-matched lines) and keeps only the 12-word cap
- script.hero_lines    a hero line (on-screen text set in the hero role, the shot's `hero_lines`) has at most 6
                       words, and an act shows at most 2

A shot may declare how its text is read (`"reading": {"voice_matched": true, "reason": "…"}` or
`{"not_for_reading": true, "reason": "…"}`, the declarations `slate qa readtime` honours too, L-qa-D053): a
voice-matched definition, read along by the voice as it lands, may run past the word budget and needs only the 1.5 s
floor; a surface shown too small to read on purpose isn't timed or counted. Without a reason a declaration counts for
nothing (and says so). A list of statements instead of true is for the picture's QA, which reads each statement; here
it exempts nothing.
- script.banned_words  none of the brand's banned words in the VO, on screen or in the picture
- script.runtime       within ±10% of the requested runtime
- script.numbers       every number said or shown is approved, sample data, or tied to a pinned source
- script.claims        every claim is approved or cites a pinned source; nothing from the never-claim list
- script.quotes        no invented testimonials: anyone quoted has a real, sourced attribution; and every quoted
                       product fact (a truth claim's or a shot source's `quote` at its `path:line`) is re-read at its
                       source's pinned commit before the build: words that moved are named with the line they're on now
- script.* rules       a film's own rules from the script's "rules" block: vo_dashes, first_line, every_shot_sourced
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from slate import __version__
from slate.gates import GateResult, engine
from slate.story import inputs, numbers
from slate.story.model import Plan, PlannedShot, plan as make_plan
from slate.timing import spoken

ENGINE = engine("slate script build", __version__)
TEXT_FIELDS = ("vo", "on_screen", "picture")


def _g(status: str, gate: str, summary: str, evidence: list[str] | None = None, **details) -> GateResult:
    return GateResult(gate, status, summary, evidence=list(evidence or [])[:60], engine=dict(ENGINE),
                      details=details)


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


# --- structure, pace, read time, density, runtime ---------------------------------------------------------------------

def check_structure(plan: Plan) -> GateResult:
    if plan.problems:
        first = plan.problems[0]
        summary = first if first.startswith("the script has no acts") else f"the script can't be planned: {first}"
        return _g("FAIL", "script.structure", summary, plan.problems)
    return _g("PASS", "script.structure", f"{_plural(len(plan.acts), 'act')}, {_plural(len(plan.shots), 'shot')}, "
              f"ids {plan.ids_mode}")


def check_pace(plan: Plan) -> GateResult:
    ceiling = float(plan.timing["max_pace"])
    over, voiced = [], 0
    for s in plan.shots:
        if not s.words:
            continue
        voiced += 1
        pace = s.words / s.dur if s.dur else float("inf")
        if pace > ceiling + 1e-9:
            over.append(f"{s.id}: {pace:.2f} words a second ({s.words} words in {s.dur:.2f} s), over {ceiling:g}")
    if over:
        return _g("FAIL", "script.pace", f"{_plural(len(over), 'line')} over {ceiling:g} words a second", over,
                  ceiling=ceiling)
    return _g("PASS", "script.pace", f"all {_plural(voiced, 'VO line')} at or under {ceiling:g} words a second",
              ceiling=ceiling)


def _has_number(text: str, lang: str) -> bool:
    return any(m.counts for m in numbers.mentions(text, lang))


READING_FLAGS = ("voice_matched", "not_for_reading", "labels")


def reading(s: PlannedShot) -> tuple[set[str], str | None]:
    """The shot's whole-shot reading declarations ({"voice_matched", "not_for_reading", "labels"} with `true`) and a
    problem with them, if any. A declaration needs its reason; a list of statements is for the picture's QA.
    `labels` says the on-screen words name what the picture shows (a product's own UI, an end card's name and address)
    rather than a second script, so they may run past 40% of the voice; they still count toward the word cap."""
    raw = s.data.get("reading")
    if raw is None:
        return set(), None
    if not isinstance(raw, dict):
        return set(), f'{s.id}: "reading" should be an object like {{"voice_matched": true, "reason": "…"}}'
    flags = {f for f in READING_FLAGS if raw.get(f) is True}
    if flags and not str(raw.get("reason") or "").strip():
        return set(), f"{s.id}: declares {', '.join(sorted(flags))} without a reason, so it counts for nothing"
    return flags, None


def check_read_time(plan: Plan, brand: dict, rules: dict) -> GateResult:
    scale = inputs.durations(brand)
    short, unknown, notes = [], set(), []
    for s in plan.shots:
        text = s.data.get("on_screen") or ""
        n = spoken.word_count(text, plan.lang)
        if not n:
            continue
        flags, problem = reading(s)
        if problem:
            notes.append(problem)
        if "not_for_reading" in flags:
            notes.append(f"{s.id}: not for reading ({s.data['reading']['reason']}), so not timed")
            continue
        token = s.data.get("enter_token")
        if token and token in scale:
            ent = scale[token]
        else:
            ent = float(rules["default_entrance"])
            if token:
                unknown.add(str(token))
        settled = s.dur - ent - float(rules["exit_factor"]) * ent
        if "voice_matched" in flags:  # the voice reads it along as it lands: only the floor
            need = float(rules["floor"])
            notes.append(f"{s.id}: voice-matched ({s.data['reading']['reason']}), held to the {need:g} s floor")
        else:
            need = max(float(rules["floor"]), float(rules["base"]) + float(rules["per_word"]) * n)
        if _has_number(text, plan.lang):
            need = max(need, 2.0)
        if settled + 1e-9 < need:
            short.append(f"{s.id}: {_plural(n, 'on-screen word')} need {need:.1f} s settled, the shot gives "
                         f"{settled:.1f} s ({s.dur:.2f} s − {ent:.2f} s in − {float(rules['exit_factor']) * ent:.2f} s out)")
    evidence = short + ([f"entrance tokens not in the brand's duration scale (used {rules['default_entrance']} s): "
                         f"{', '.join(sorted(unknown))}"] if unknown else []) + notes
    if short:
        return _g("FAIL", "script.read_time", f"{_plural(len(short), 'shot')} hold on-screen text too briefly", evidence)
    return _g("PASS", "script.read_time", "every on-screen line holds long enough to read", evidence)


def _tokens(text: str, lang: str) -> list[str]:
    return [k for k in (spoken.norm(tok, lang) for tok in (text or "").split() if spoken.is_spoken(tok, lang)) if k]


def _said_as(token: str, lang: str) -> list[list[str]]:
    """The ways a voice says an on-screen token, as match keys: the token itself, and for a number or a symbol each of
    its spoken forms ("54%" -> fifty-four percent)."""
    own = spoken.norm(token, lang)
    out = [[own]] if own else []
    if any(ch.isdigit() for ch in token) or not token.isalpha():
        for form in spoken.forms(token, lang):
            keys = [k for k in (spoken.norm(w, lang) for w in form) if k]
            if keys and keys not in out:
                out.append(keys)
    return out


def vo_matched(on_screen: str, vo: str, lang: str) -> bool:
    """True when every on-screen word is one the voice says in the shot, in the order it says them (a number may be
    said in words): text read along with the voice (the films' VO-matched lines), which doesn't compete with it the way
    other words on screen do."""
    said = _tokens(vo, lang)
    shown = [tok for tok in (on_screen or "").split() if spoken.is_spoken(tok, lang) and spoken.norm(tok, lang)]
    if not shown or not said:
        return False
    at = 0
    for tok in shown:
        best = None
        for keys in _said_as(tok, lang):
            for i in range(at, len(said) - len(keys) + 1):
                if said[i:i + len(keys)] == keys:
                    best = i + len(keys) if best is None else min(best, i + len(keys))
                    break
        if best is None:
            return False
        at = best
    return True


def check_on_screen(plan: Plan, rules: dict) -> GateResult:
    """At most 12 on-screen words a shot, and at most 40% of the words the voice says in it (L-story-030: keywords, not
    transcripts). Words the voice says, in order, are read along with it and keep only the cap. A shot's reading
    declaration, with its reason, answers for it: voice-matched or not for reading exempts it from both, labels (the
    picture's own words: a product's UI, an end card) from the 40% only."""
    cap = int(rules["max_words"])
    ratio = float(rules.get("max_vo_ratio", 0.4))
    dense, wordy, notes, read_along, declared = [], [], [], [], []
    for s in plan.shots:
        text = s.data.get("on_screen") or ""
        n = spoken.word_count(text, plan.lang)
        if not n:
            continue
        flags, problem = reading(s)
        if problem:
            notes.append(problem)
        if n > cap:
            reads = flags & {"voice_matched", "not_for_reading"}
            if reads and cap > 0:  # a voice-matched definition (or a surface not for reading) may run past the budget
                why = "voice-matched" if "voice_matched" in reads else "not for reading"
                declared.append(why)
                notes.append(f"{s.id}: {n} on-screen words, past {cap}: {why} ({s.data['reading']['reason']})")
            else:
                dense.append(f"{s.id}: {n} on-screen words (at most {cap})")
        said = spoken.word_count(s.vo, plan.lang)
        if not said or ratio <= 0 or cap == 0 or n <= ratio * said + 1e-9:
            continue
        share = f"{n} on-screen words against {said} said ({n / said:.0%})"
        if flags:
            why = next(w for f, w in (("voice_matched", "voice-matched"), ("not_for_reading", "not for reading"),
                                      ("labels", "labels")) if f in flags)
            declared.append(why)
            notes.append(f"{s.id}: {share}, past {ratio:.0%}: {why} ({s.data['reading']['reason']})")
        elif vo_matched(text, s.vo, plan.lang):
            read_along.append(s.id)
            notes.append(f"{s.id}: {share}, past {ratio:.0%}, but the voice says every one of them as they show: "
                         "read along with it")
        else:
            wordy.append(f"{s.id}: {share}, over {ratio:.0%} (keywords, not transcripts)")
    if dense and cap == 0:
        return _g("FAIL", "script.on_screen", f"{_plural(len(dense), 'shot')} put words on screen, and this format's "
                  "picture carries none", dense + notes, max_words=cap)
    if dense or wordy:
        parts = ([f"{_plural(len(dense), 'shot')} carry more than {cap} on-screen words"] if dense else []) + \
                ([f"{_plural(len(wordy), 'shot')} put more than {ratio:.0%} of their voice-over on screen"] if wordy else [])
        evidence = dense + wordy + notes + (["a shot whose words are the picture's own (its UI, an end card) says so: "
                                             '"reading": {"labels": true, "reason": "…"}'] if wordy else [])
        return _g("FAIL", "script.on_screen", "; ".join(parts), evidence, max_words=cap, max_vo_ratio=ratio)
    if cap == 0:
        return _g("PASS", "script.on_screen", "no words on screen, as the format asks", notes, max_words=cap)
    extra = [x for x, on in ((f"declared {' or '.join(dict.fromkeys(declared))}", declared),
                             ("read along with the voice", read_along)) if on]
    return _g("PASS", "script.on_screen", f"every shot at or under {cap} on-screen words and {ratio:.0%} of its "
              "voice-over" + (f" (or {', or '.join(extra)})" if extra else ""), notes, max_words=cap,
              max_vo_ratio=ratio)


def hero_lines_of(s: PlannedShot) -> list[str]:
    """The shot's hero lines: on-screen text set in the hero role (its `hero_lines`, one string or a list)."""
    raw = s.data.get("hero_lines")
    if isinstance(raw, str):
        raw = [raw]
    return [str(x).strip() for x in raw or [] if str(x).strip()]


def check_hero_lines(plan: Plan, rules: dict) -> GateResult:
    """L-story-033: a hero line has at most 6 words, and an act shows at most 2 (more, and none of them is the hero)."""
    most, per_act = int(rules.get("hero_max_words", 6)), int(rules.get("hero_per_act", 2))
    long_, crowded, total = [], [], 0
    acts: dict[Any, list[str]] = {}
    for s in plan.shots:
        for line in hero_lines_of(s):
            total += 1
            n = spoken.word_count(line, plan.lang)
            if n > most:
                long_.append(f"{s.id}: the hero line '{line[:60]}' has {n} words (at most {most})")
            acts.setdefault(s.act_name or s.act, []).append(s.id)
    for act, shots in acts.items():
        if len(shots) > per_act:
            crowded.append(f"act {act}: {len(shots)} hero lines ({', '.join(shots)}), at most {per_act}")
    if long_ or crowded:
        parts = ([f"{_plural(len(long_), 'hero line')} over {most} words"] if long_ else []) + \
                ([f"{_plural(len(crowded), 'act')} with more than {per_act} hero lines"] if crowded else [])
        return _g("FAIL", "script.hero_lines", "; ".join(parts), long_ + crowded, max_words=most, per_act=per_act)
    if not total:
        return _g("PASS", "script.hero_lines", "no hero lines in this script", max_words=most, per_act=per_act)
    return _g("PASS", "script.hero_lines", f"{_plural(total, 'hero line')}, each at most {most} words, at most "
              f"{per_act} an act", max_words=most, per_act=per_act)


def check_runtime(plan: Plan, target: dict) -> GateResult:
    total = plan.runtime
    m, s = divmod(total, 60)
    shown = f"{int(m)}:{s:04.1f}"
    if target.get("target"):
        t = float(target["target"])
        diff = (total - t) / t
        lo, hi = t * (1 - inputs.RUNTIME_TOLERANCE), t * (1 + inputs.RUNTIME_TOLERANCE)
        ev = [f"runtime {total:.2f} s against {t:g} s ({target.get('source')}), {diff:+.1%}; allowed {lo:.1f}–{hi:.1f} s"]
        if abs(diff) <= inputs.RUNTIME_TOLERANCE + 1e-9:
            return _g("PASS", "script.runtime", f"{shown} is {diff:+.1%} from the {t:g} s target (±10%)", ev,
                      runtime_s=total, target_s=t)
        return _g("FAIL", "script.runtime", f"{shown} is {diff:+.1%} from the {t:g} s target (allowed ±10%)", ev,
                  runtime_s=total, target_s=t)
    if target.get("min") is not None and target.get("max") is not None:
        lo, hi = float(target["min"]) * (1 - inputs.RUNTIME_TOLERANCE), float(target["max"]) * (1 + inputs.RUNTIME_TOLERANCE)
        ev = [f"runtime {total:.2f} s against the format's {target['min']:g}–{target['max']:g} s range (±10% at the ends)"]
        if lo - 1e-9 <= total <= hi + 1e-9:
            return _g("PASS", "script.runtime", f"{shown} is inside the format's range", ev, runtime_s=total)
        return _g("FAIL", "script.runtime", f"{shown} is outside the format's {target['min']:g}–{target['max']:g} s range",
                  ev, runtime_s=total)
    return _g("UNCHECKED", "script.runtime", f"{shown}, but there's no runtime target to check it against",
              ["set targets.runtime_s in film.json (the requester's length), or use a format with a runtime"],
              runtime_s=total)


# --- words the brand never uses ---------------------------------------------------------------------------------------

def _phrase_re(phrase: str) -> re.Pattern:
    parts = [re.escape(p) for p in phrase.split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)", re.IGNORECASE)


#: Where a caption is on screen: the shot's on-screen text and its picture (never the voice).
SCREEN_FIELDS = ("on_screen", "picture")


def check_banned(plan: Plan, brand: dict) -> GateResult:
    """The brand's banned words, in the VO, on screen and in the picture. A brand that forbids self-commentary
    (truth.self_commentary: false) also has the captions a graphic would use on itself banned on screen ("Illustration",
    "Example" and their forms: L-brand-010); said aloud they're fine."""
    words = [w for w in inputs.banned_words(brand) if w and w.strip()]
    if not brand:
        return _g("UNCHECKED", "script.banned_words", "the film has no brand pack, so there's no banned list to check")
    hits = []
    for w in words:
        rx = _phrase_re(w)
        for s in plan.shots:
            for f in TEXT_FIELDS:
                for m in rx.finditer(str(s.data.get(f) or "")):
                    hits.append(f"{s.id} {f}: '{m.group(0)}'")
    captions = () if inputs.self_commentary(brand) else inputs.SELF_COMMENTARY
    for w in captions:
        rx = _phrase_re(w)
        for s in plan.shots:
            for f in SCREEN_FIELDS:
                for m in rx.finditer(str(s.data.get(f) or "")):
                    hits.append(f"{s.id} {f}: '{m.group(0)}' (the brand's graphics never caption themselves: "
                                "truth.self_commentary is false)")
    if hits:
        return _g("FAIL", "script.banned_words", f"{_plural(len(hits), 'banned word')} in the script", hits)
    extra = "; self-commentary captions are banned on screen" if captions else ""
    return _g("PASS", "script.banned_words", f"none of the brand's {_plural(len(words), 'banned word')} appear "
              f"in the VO, on screen or in the picture{extra}")


# --- sources, claims, numbers, quotes -----------------------------------------------------------------------------------

class Truth:
    """The film's pinned sources and every approved or sourced claim, for the claim, number and quote gates.

    An autonomous film (docs/PLAN.md §7) may say only what the brand has approved: a claim or number that is merely
    sourced is a new claim, which a person rules on, so the gates fail it and the film parks with the question.
    """

    def __init__(self, brand: dict, truth: dict, lang: str, *, approved_only: bool = False):
        self.lang = lang
        self.approved_only = approved_only
        self.sources: dict[str, dict] = {}
        for src in inputs.brand_sources(brand) + [s for s in (truth.get("sources") or []) if isinstance(s, dict)]:
            if src.get("id"):
                self.sources[str(src["id"])] = src
        self.approved = inputs.approved_claims(brand)
        self.truth_claims = [c for c in (truth.get("claims") or []) if isinstance(c, dict)]
        self.never = inputs.never_claim(brand) + [str(x if not isinstance(x, dict) else x.get("text"))
                                                  for x in (truth.get("never_claim") or []) if x]
        self.sample = inputs.sample_data(brand)
        self.allowed: set[float] = set()
        for c in self.approved:
            self.allowed |= self.claim_values(c)
        for c in self.truth_claims:
            if self.pinned(c.get("source"))[0] and not approved_only:
                self.allowed |= self.claim_values(c)
        self.sample_values = numbers.values_in_data(self.sample, lang) if self.sample is not None else set()
        self.sample_values |= numbers.values_in_data(truth.get("sample_data"), lang) if truth.get("sample_data") else set()

    def claim_values(self, claim: dict) -> set[float]:
        vals = numbers.values_in(str(claim.get("text") or ""), self.lang)
        for n in claim.get("numbers") or []:
            vals |= numbers.values_in(str(n), self.lang) if not isinstance(n, (int, float)) else {abs(float(n))}
        return vals

    def pinned(self, ref: Any) -> tuple[bool, str]:
        """(is it a pinned source?, why not). A ref is "<source id>" or "<source id>:<where>"."""
        if isinstance(ref, dict):
            ref = ref.get("ref") or ref.get("source") or ref.get("id")
        if not ref or not str(ref).strip():
            return False, "no source given"
        text = str(ref).strip()
        head = re.split(r"[:\s]", text, maxsplit=1)[0]
        src = self.sources.get(head) or self.sources.get(text)
        if src is None:
            return False, f"'{head}' isn't a source in brief/truth.json or the brand's truth"
        if not str(src.get("pin") or src.get("pinned") or src.get("sha256") or "").strip():
            return False, f"source '{head}' has no pin (a commit, date or hash) in brief/truth.json"
        return True, ""

    def approved_claim(self, key: str) -> dict | None:
        k = spoken.norm(key, self.lang) if key else ""
        for c in self.approved:
            if key == c.get("id") or (k and spoken.norm(str(c.get("text") or ""), self.lang) == k):
                return c
        return None

    def truth_claim(self, key: str) -> dict | None:
        for c in self.truth_claims:
            if key and key in (c.get("id"), c.get("text")):
                return c
        return None


NEW_CLAIM = "is sourced but not approved, and an autonomous film says only approved claims (a person rules on new ones)"


def _claim_status(claim: Any, truth: Truth) -> tuple[bool, str, set[float]]:
    """(ok, why, values it covers) for one entry of a shot's claims."""
    ok, why, values = _claim_status_any(claim, truth)
    if ok and truth.approved_only and why != "approved":
        return False, NEW_CLAIM, set()
    return ok, why, values


def _claim_status_any(claim: Any, truth: Truth) -> tuple[bool, str, set[float]]:
    if isinstance(claim, str):
        hit = truth.approved_claim(claim)
        if hit:
            return True, "approved", truth.claim_values(hit)
        tc = truth.truth_claim(claim)
        if tc:
            ok, why = truth.pinned(tc.get("source"))
            return ok, why or "sourced", truth.claim_values(tc) if ok else set()
        return False, "isn't on the approved list and cites no pinned source", set()
    if isinstance(claim, dict):
        text = str(claim.get("text") or claim.get("claim") or "")
        if claim.get("approved"):
            hit = truth.approved_claim(str(claim["approved"]))
            if hit:
                return True, "approved", truth.claim_values(hit) | truth.claim_values(claim)
            return False, f"says it's approved as '{claim['approved']}', which isn't on the brand's approved list", set()
        if claim.get("source"):
            ok, why = truth.pinned(claim["source"])
            return ok, why or "sourced", truth.claim_values(claim) if ok else set()
        hit = truth.approved_claim(text)
        if hit:
            return True, "approved", truth.claim_values(hit)
        return False, "isn't on the approved list and cites no pinned source", set()
    return False, "isn't a claim (write a string or {text, source})", set()


def _claim_label(claim: Any) -> str:
    if isinstance(claim, dict):
        return str(claim.get("text") or claim.get("claim") or claim.get("approved") or claim)[:90]
    return str(claim)[:90]


def check_claims(plan: Plan, truth: Truth) -> GateResult:
    bad, good = [], 0
    for s in plan.shots:
        for c in s.data.get("claims") or []:
            ok, why, _vals = _claim_status(c, truth)
            if ok:
                good += 1
            else:
                bad.append(f"{s.id}: claim '{_claim_label(c)}' {why}")
    never_hits = []
    for phrase in truth.never:
        if not phrase or not phrase.strip():
            continue
        rx = _phrase_re(phrase)
        for s in plan.shots:
            for f in TEXT_FIELDS:
                if rx.search(str(s.data.get(f) or "")):
                    never_hits.append(f"{s.id} {f}: says '{phrase}', which the brand never claims")
    if bad or never_hits:
        return _g("FAIL", "script.claims", f"{_plural(len(bad) + len(never_hits), 'claim problem')}", bad + never_hits)
    return _g("PASS", "script.claims", f"{_plural(good, 'claim')} approved or sourced; nothing from the never-claim list"
              if good else "no claims made; nothing from the never-claim list")


def _shot_values(s: PlannedShot, truth: Truth) -> set[float]:
    vals: set[float] = set()
    for c in s.data.get("claims") or []:
        ok, _why, covered = _claim_status(c, truth)
        if ok:
            vals |= covered
    for src in s.data.get("sources") or []:
        if isinstance(src, dict) and src.get("numbers") and truth.pinned(src)[0] and not truth.approved_only:
            for n in src["numbers"]:
                vals |= numbers.values_in(str(n), truth.lang) if not isinstance(n, (int, float)) else {abs(float(n))}
    return vals


def check_numbers(plan: Plan, truth: Truth) -> GateResult:
    bad, seen = [], 0
    for s in plan.shots:
        allowed = truth.allowed | truth.sample_values | _shot_values(s, truth)
        for f in ("vo", "on_screen"):
            for m in numbers.mentions(str(s.data.get(f) or ""), plan.lang):
                if not m.counts:
                    continue
                seen += 1
                if not any(any(abs(v - a) < 1e-9 for a in allowed) for v in m.values):
                    bad.append(f"{s.id} {f}: '{m.text}' isn't approved, sample data, or tied to a pinned source")
    if bad:
        return _g("FAIL", "script.numbers", f"{_plural(len(bad), 'unsourced number')}", bad +
                  ["add it to the brand's approved claims, or add a claim {text, source} on the shot citing a pinned "
                   "source in brief/truth.json"])
    return _g("PASS", "script.numbers", f"all {_plural(seen, 'number')} approved, sample data or sourced" if seen else
              "no numbers said or shown")


def _quotes_in(text: str, lang: str) -> list[str]:
    rules = spoken.table(lang)["script"]
    found = []
    for open_, close in rules["quote_pairs"]:
        rx = re.compile(re.escape(open_) + r"([^" + re.escape(close) + r"]+)" + re.escape(close))
        for m in rx.finditer(text or ""):
            inner = m.group(1).strip()
            if spoken.word_count(inner, lang) >= int(rules["min_quote_words"]):
                found.append(inner)
    return found


COMMIT = re.compile(r"\b[0-9a-f]{7,40}\b")
WHERE = re.compile(r"^(?P<path>.+?):(?P<a>\d+)(?:\s*[-–]\s*(?P<b>\d+))?$")
QUOTE_SEARCH_LINES = 2  # a quote may wrap: it's looked for over its own lines and this many either side


def _copy(text: str) -> str:
    """Product copy as a reader sees it: one space between words, curly quotes and apostrophes as plain ones."""
    t = str(text).replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    return " ".join(t.split())


def product_quotes(plan: Plan, truth_doc: dict) -> list[dict]:
    """Every quoted product fact: a truth claim's, or a shot source's, `quote` with where it is (`path:line`)."""
    out = []
    for c in truth_doc.get("claims") or []:
        if isinstance(c, dict) and str(c.get("quote") or "").strip():
            out.append({"what": f"claim {c.get('id') or c.get('text')}", "source": str(c.get("source") or ""),
                        "where": str(c.get("where") or ""), "quote": str(c["quote"])})
    for s in plan.shots:
        for src in s.data.get("sources") or []:
            if isinstance(src, dict) and str(src.get("quote") or "").strip():
                head, _, where = str(src.get("ref") or "").partition(":")
                out.append({"what": f"{s.id}'s source", "source": head.strip(),
                            "where": str(src.get("where") or where).strip(), "quote": str(src["quote"])})
    return out


def read_at_pin(source: dict, rel: str) -> tuple[list[str] | None, str, bool]:
    """(the file's lines at the source's pin, why not, whether that's the source's fault). Product code is read in
    place, read-only, at its pinned commit (`git show <commit>:<path>`); a source pinned by a file's SHA-256 is that
    file, checked against its hash. None when it can't be read on this machine (UNCHECKED, never a pass)."""
    sid = source.get("id")
    root_raw = str(source.get("path") or "").strip()
    if not root_raw:
        return None, f"source '{sid}' has no local path (\"path\" in brief/truth.json) to re-read it from", False
    root = Path(root_raw).expanduser()
    if not root.exists():
        return None, f"source '{sid}' isn't on this machine ({root})", False
    pin = str(source.get("pin") or source.get("pinned") or "")
    commit = COMMIT.search(pin)
    if root.is_dir() and commit:
        from slate import procs  # noqa: PLC0415

        if not procs.which("git"):
            return None, "git isn't installed, so the pinned commit can't be read", False
        has = procs.run(["git", "-C", str(root), "cat-file", "-e", f"{commit.group(0)}^{{commit}}"], timeout=60)
        if has.returncode != 0:
            return None, f"commit {commit.group(0)} isn't in {root} (fetch it there, then build again)", False
        shown = procs.run(["git", "-C", str(root), "show", f"{commit.group(0)}:{rel}"], timeout=60)
        if shown.returncode != 0:
            return None, f"{rel} isn't in source '{sid}' at {commit.group(0)}", True
        return shown.stdout.splitlines(), "", False
    if root.is_file() and source.get("sha256"):
        from slate import jsonio  # noqa: PLC0415

        if jsonio.sha256(root) != str(source["sha256"]):
            return None, f"source '{sid}' ({root}) has changed since it was pinned (its SHA-256 no longer matches)", True
        return root.read_text(encoding="utf-8", errors="replace").splitlines(), "", False
    return None, f"source '{sid}' is pinned by {pin or 'nothing'}, which isn't a commit Slate can read {rel} at", False


def _found_at(lines: list[str], quote: str, max_span: int) -> list[int]:
    """1-based line numbers where the quote starts, whitespace-insensitive (it may wrap over up to max_span lines)."""
    want = _copy(quote)
    hits = []
    for i in range(len(lines)):
        for k in range(1, max_span + 1):
            if want in _copy(" ".join(lines[i:i + k])):
                if k == 1 or want not in _copy(" ".join(lines[i + 1:i + k])):
                    hits.append(i + 1)  # the shortest window that holds it starts here
                break
    return hits


def check_product_quote(q: dict, sources: dict[str, dict], cache: dict) -> tuple[str, str]:
    """("ok" | "fail" | "unchecked", what was found) for one quoted product fact (L-story-005)."""
    m = WHERE.match(q["where"])
    if not q["source"] or not m:
        return "fail", (f"{q['what']}: the quote '{q['quote'][:50]}' needs its source and where it is "
                        f"(\"source\" and \"where\": \"path:line\"), got '{q['source']}' and '{q['where']}'")
    src = sources.get(q["source"])
    if src is None:
        return "fail", f"{q['what']}: '{q['source']}' isn't a source in brief/truth.json or the brand's truth"
    rel, a = m.group("path").strip(), int(m.group("a"))
    b = int(m.group("b") or a)
    key = (q["source"], rel)
    if key not in cache:
        cache[key] = read_at_pin(src, rel)
    lines, why, fault = cache[key]
    if lines is None:
        return ("fail" if fault else "unchecked"), f"{q['what']}: {why}"
    want = _copy(q["quote"])
    lo, hi = max(1, a - QUOTE_SEARCH_LINES), min(len(lines), b + QUOTE_SEARCH_LINES)
    if want and want in _copy(" ".join(lines[a - 1:b])):
        return "ok", f"{q['what']}: '{q['quote'][:50]}' is at {rel}:{q['where'].split(':', 1)[1]}"
    hits = sorted(set(_found_at(lines, q["quote"], b - a + 1 + QUOTE_SEARCH_LINES)), key=lambda n: (abs(n - a), n))
    if hits:
        return "fail", (f"{q['what']}: '{q['quote'][:50]}' isn't at {rel}:{q['where'].split(':', 1)[1]} any more; "
                        f"it's at {rel}:{hits[0]} (cite that line)")
    near = " / ".join(_copy(x)[:60] for x in lines[lo - 1:hi][:3]) if lines else ""
    return "fail", (f"{q['what']}: '{q['quote'][:50]}' isn't in {rel} at the pinned commit"
                    + (f" (around line {a}: {near})" if near else ""))


def check_quotes(plan: Plan, truth: Truth, truth_doc: dict | None = None) -> GateResult:
    """Two kinds of quote, one gate: nobody's words are invented (a testimonial names who said it and a pinned
    source), and every quoted product fact is still where it says it is, re-read at its source's pinned commit before
    the build (L-story-005: a first pass once corrected the line numbers on about a hundred quotes)."""
    bad, ok_n = [], 0
    for s in plan.shots:
        declared = [q for q in s.data.get("quotes") or [] if isinstance(q, dict)]
        for q in declared:
            who = str(q.get("by") or "").strip()
            fine, why = truth.pinned(q.get("source"))
            if q.get("real") is False or q.get("invented"):
                bad.append(f"{s.id}: '{str(q.get('text'))[:60]}' is marked invented; a quote can't be presented as a "
                           "real person's words unless they said it")
            elif not who:
                bad.append(f"{s.id}: a quote has no attribution (who said it)")
            elif not fine:
                bad.append(f"{s.id}: the quote from {who} {why}")
            else:
                ok_n += 1
        texts = [spoken.norm(str(q.get("text") or ""), plan.lang) for q in declared]
        for f in ("vo", "on_screen"):
            for quote in _quotes_in(str(s.data.get(f) or ""), plan.lang):
                key = spoken.norm(quote, plan.lang)
                if not any(key and t and (key in t or t in key) for t in texts):
                    bad.append(f"{s.id} {f}: \"{quote[:70]}\" reads as someone's words but the shot has no quotes entry "
                               "with who said it and a pinned source")
    facts = product_quotes(plan, truth_doc or {})
    checked, unchecked, cache = [], [], {}
    for q in facts:
        verdict, said = check_product_quote(q, truth.sources, cache)
        (checked if verdict == "ok" else unchecked if verdict == "unchecked" else bad).append(said)
    people = f"{_plural(ok_n, 'quote')} attributed and sourced" if ok_n else "no one is quoted"
    product = (f"; {_plural(len(checked), 'product quote')} re-read at {'its' if len(checked) == 1 else 'their'} "
               "pinned commit") if checked else ""
    if bad:
        return _g("FAIL", "script.quotes", f"{_plural(len(bad), 'quote')} without a real, sourced attribution or not "
                  "where it says it is", bad + unchecked, product_quotes=len(facts))
    if unchecked:
        return _g("UNCHECKED", "script.quotes", f"{people}{product}; {_plural(len(unchecked), 'product quote')} "
                  "couldn't be re-read on this machine", unchecked + checked, product_quotes=len(facts))
    return _g("PASS", "script.quotes", people + product, checked, product_quotes=len(facts))


# --- a film's own rules -------------------------------------------------------------------------------------------------

def check_rules(plan: Plan, truth: Truth) -> list[GateResult]:
    rules = plan.script.get("rules") or {}
    out = []
    dash_rule = rules.get("vo_dashes")
    if dash_rule in ("fail", "review"):
        dashes = spoken.table(plan.lang)["script"]["vo_dashes"]
        hits = [f"{s.id}: a dash in the VO ('{d}')" for s in plan.shots for d in dashes if d in s.vo]
        status = ("FAIL" if dash_rule == "fail" else "REVIEW") if hits else "PASS"
        out.append(_g(status, "script.vo_dashes", f"{_plural(len(hits), 'dash', 'dashes')} in the VO" if hits else
                      "no dashes in the VO", hits))
    first = rules.get("first_line")
    if first:
        voiced = next((s for s in plan.shots if s.vo), None)
        ok = bool(voiced and voiced.vo.startswith(str(first)))
        out.append(_g("PASS" if ok else "FAIL", "script.first_line",
                      f"the VO opens with '{first}'" if ok else f"the VO doesn't open with '{first}'"))
    if rules.get("every_shot_sourced"):
        missing = [s.id for s in plan.shots if not any(truth.pinned(x)[0] for x in s.data.get("sources") or [])]
        out.append(_g("FAIL" if missing else "PASS", "script.sources",
                      f"{_plural(len(missing), 'shot')} without a pinned source: {', '.join(missing[:12])}" if missing
                      else "every shot cites a pinned source", [f"{m}: no pinned source" for m in missing]))
    return out


# --- all of them ---------------------------------------------------------------------------------------------------------

def judged_mode(film: dict, film_dir: Path | str | None = None) -> str:
    """The mode the film is judged in: Slate's own record (slate.state.autonomy.film_mode) holds a film made autonomous
    autonomous, whatever its film.json says now, so its claims stay approved-only. film.json's own mode without the
    state module."""
    own = str((film or {}).get("mode") or "supervised")
    try:
        from slate.state import autonomy  # noqa: PLC0415 - story works without film state installed
    except ImportError:
        return own
    try:
        return autonomy.film_mode(film_dir, film or None)
    except Exception:  # noqa: BLE001 - an unreadable record never makes a film less strict than its film.json
        return own


def check_plan(plan: Plan, *, brand: dict, fmt: dict, truth: dict | None = None, film: dict | None = None,
               format_problem: str | None = None) -> list[GateResult]:
    """Every script gate on a planned script."""
    film = film or {}
    t = Truth(brand, truth or {}, plan.lang, approved_only=judged_mode(film) == "autonomous")
    rules = inputs.read_time_settings(plan.script, fmt)
    results = [check_structure(plan)]
    if plan.problems:
        return results
    results += [check_pace(plan), check_read_time(plan, brand, rules), check_on_screen(plan, rules),
                check_hero_lines(plan, rules), check_banned(plan, brand),
                check_runtime(plan, inputs.runtime_target(film, fmt)), check_numbers(plan, t), check_claims(plan, t),
                check_quotes(plan, t, truth or {})]
    results += check_rules(plan, t)
    if format_problem:
        for r in results:
            if r.gate in ("script.pace", "script.read_time", "script.runtime"):
                r.evidence.append(format_problem)
    return results


def check_schema(script: Any) -> GateResult | None:
    """A FAIL when script.json doesn't match its schema (nothing else can be judged then), else None."""
    from slate import jsonio  # noqa: PLC0415

    problems = jsonio.validate(script, "script")
    if problems:
        return _g("FAIL", "script.schema", f"script.json doesn't match the script schema: {problems[0]}", problems)
    return None


def check_script(script: dict, *, brand: dict, fmt: dict, truth: dict | None = None, film: dict | None = None,
                 lang: str | None = None, default_enter_token: str | None = None) -> list[GateResult]:
    """The §12 interface: plan the script at planning lengths and run every script gate on it."""
    schema = check_schema(script)
    if schema is not None:
        return [schema]
    film = film or {}
    language = lang or film.get("language") or script.get("language") or "en"
    timing = inputs.timing_settings(script, fmt)
    planned = make_plan(script, timing=timing, lang=language, default_enter_token=default_enter_token)
    return check_plan(planned, brand=brand, fmt=fmt, truth=truth, film=film)
