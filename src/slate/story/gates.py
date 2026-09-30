"""The script gates (method stages 2–3): what a script has to pass before anything is built from it.

Each gate returns a GateResult (PASS, FAIL, REVIEW or UNCHECKED, never a quiet pass). `slate script build` refuses to
write anything when any of them FAILs.

- script.structure     the script can be planned: acts, unique ids, every silent shot has a length
- script.pace          every VO line at most 2.6 words a second of its shot (the format can change the ceiling)
- script.read_time     on-screen text holds long enough once it has settled: max(1.5 s, 0.4 s + 0.3 s x words),
                       measured as dur − entrance − 0.75 x entrance, and 2 s for a number (MOTION.md §7)
- script.on_screen     at most 12 on-screen words a shot
- script.banned_words  none of the brand's banned words in the VO, on screen or in the picture
- script.runtime       within ±10% of the requested runtime
- script.numbers       every number said or shown is approved, sample data, or tied to a pinned source
- script.claims        every claim is approved or cites a pinned source; nothing from the never-claim list
- script.quotes        no invented testimonials: anyone quoted has a real, sourced attribution
- script.* rules       a film's own rules from the script's "rules" block: vo_dashes, first_line, every_shot_sourced
"""

from __future__ import annotations

import re
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
        return _g("FAIL", "script.structure", f"the script can't be planned: {plan.problems[0]}", plan.problems)
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


def check_read_time(plan: Plan, brand: dict, rules: dict) -> GateResult:
    scale = inputs.durations(brand)
    short, unknown = [], set()
    for s in plan.shots:
        text = s.data.get("on_screen") or ""
        n = spoken.word_count(text, plan.lang)
        if not n:
            continue
        token = s.data.get("enter_token")
        if token and token in scale:
            ent = scale[token]
        else:
            ent = float(rules["default_entrance"])
            if token:
                unknown.add(str(token))
        settled = s.dur - ent - float(rules["exit_factor"]) * ent
        need = max(float(rules["floor"]), float(rules["base"]) + float(rules["per_word"]) * n)
        if _has_number(text, plan.lang):
            need = max(need, 2.0)
        if settled + 1e-9 < need:
            short.append(f"{s.id}: {_plural(n, 'on-screen word')} need {need:.1f} s settled, the shot gives "
                         f"{settled:.1f} s ({s.dur:.2f} s − {ent:.2f} s in − {float(rules['exit_factor']) * ent:.2f} s out)")
    evidence = short + ([f"entrance tokens not in the brand's duration scale (used {rules['default_entrance']} s): "
                         f"{', '.join(sorted(unknown))}"] if unknown else [])
    if short:
        return _g("FAIL", "script.read_time", f"{_plural(len(short), 'shot')} hold on-screen text too briefly", evidence)
    return _g("PASS", "script.read_time", "every on-screen line holds long enough to read", evidence)


def check_on_screen(plan: Plan, rules: dict) -> GateResult:
    cap = int(rules["max_words"])
    dense = [f"{s.id}: {spoken.word_count(s.data.get('on_screen') or '', plan.lang)} on-screen words (at most {cap})"
             for s in plan.shots if spoken.word_count(s.data.get("on_screen") or "", plan.lang) > cap]
    if dense and cap == 0:
        return _g("FAIL", "script.on_screen", f"{_plural(len(dense), 'shot')} put words on screen, and this format's "
                  "picture carries none", dense, max_words=cap)
    if dense:
        return _g("FAIL", "script.on_screen", f"{_plural(len(dense), 'shot')} carry more than {cap} on-screen words",
                  dense, max_words=cap)
    if cap == 0:
        return _g("PASS", "script.on_screen", "no words on screen, as the format asks", max_words=cap)
    return _g("PASS", "script.on_screen", f"every shot at or under {cap} on-screen words", max_words=cap)


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


def check_banned(plan: Plan, brand: dict) -> GateResult:
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
    if hits:
        return _g("FAIL", "script.banned_words", f"{_plural(len(hits), 'banned word')} in the script", hits)
    return _g("PASS", "script.banned_words", f"none of the brand's {_plural(len(words), 'banned word')} appear "
              "in the VO, on screen or in the picture")


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


def check_quotes(plan: Plan, truth: Truth) -> GateResult:
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
    if bad:
        return _g("FAIL", "script.quotes", f"{_plural(len(bad), 'quote')} without a real, sourced attribution", bad)
    return _g("PASS", "script.quotes", f"{_plural(ok_n, 'quote')} attributed and sourced" if ok_n else
              "no one is quoted")


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

def check_plan(plan: Plan, *, brand: dict, fmt: dict, truth: dict | None = None, film: dict | None = None,
               format_problem: str | None = None) -> list[GateResult]:
    """Every script gate on a planned script."""
    film = film or {}
    t = Truth(brand, truth or {}, plan.lang, approved_only=film.get("mode") == "autonomous")
    rules = inputs.read_time_settings(plan.script, fmt)
    results = [check_structure(plan)]
    if plan.problems:
        return results
    results += [check_pace(plan), check_read_time(plan, brand, rules), check_on_screen(plan, rules),
                check_banned(plan, brand), check_runtime(plan, inputs.runtime_target(film, fmt)),
                check_numbers(plan, t), check_claims(plan, t), check_quotes(plan, t)]
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
