"""script/SCRIPT.md: the readable script, generated from the same data as the storyboard.

Timecodes are computed, never typed (film A's rule), and the file says where to edit: script/script.json.
"""

from __future__ import annotations

from slate.gates import GateResult, summarize
from slate.story.model import Plan, transition_words

GENERATED = "<!-- generated: do not edit by hand. `slate script build` writes this from script/script.json. -->"


def tc(seconds: float) -> str:
    """m:ss.s, the films' storyboard timecode."""
    m, s = divmod(max(0.0, seconds), 60)
    return f"{int(m)}:{s:04.1f}"


def render(plan: Plan, *, title: str, results: list[GateResult], target: dict, film_id: str | None = None) -> str:
    total = plan.runtime
    vo_words = sum(s.words for s in plan.shots)
    lines = [GENERATED, "", f"# {title}: the script", ""]
    version = plan.script.get("version")
    if version:
        lines += [str(version), ""]
    target_s = target.get("target")
    against = (f" against a {target_s:g} s target ({(total - target_s) / target_s:+.1%}, allowed ±10%)"
               if target_s else "")
    pace = f", {vo_words / total:.2f} words a second across the film" if total else ""
    lines.append(f"**Runtime {tc(total)}** ({total:.1f} s){against}. **VO:** {vo_words} words{pace}. "
                 f"{len(plan.shots)} shots in {len(plan.acts)} acts.")
    lines.append("")
    if plan.mode == "takes":
        missing = f" Lines without a take yet are planned at {plan.timing['speech_rate']:g} words a second: " \
                  f"{', '.join(plan.missing_takes)}." if plan.missing_takes else ""
        lines.append(f"Shot lengths come from the real takes: each line starts {plan.timing['lead']:g} s into its shot "
                     f"and the next shot starts {plan.timing['tail']:g} s after it ends ({plan.timing['act_tail']:g} s at "
                     f"an act break), plus each shot's hold.{missing}")
    else:
        lines.append(f"Shot lengths are planning lengths at {plan.timing['speech_rate']:g} words a second of speech; "
                     "after voicing, `slate script build --from-takes` retimes them from the real takes.")
    if plan.ids_mode == "renumber":
        moved = [f"{a}→{b}" for a, b in plan.id_map.items() if a != b]
        if moved:
            lines.append(f"Renumbered from the script's draft ids: {', '.join(moved)}.")
    lines.append("")
    lines += ["| Act | Time | Shots | Job |", "|---|---|---|---|"]
    t = 0.0
    spans = []
    for i, act in enumerate(plan.acts, 1):
        shots = [s for s in plan.shots if s.act == i]
        dur = sum(s.dur for s in shots)
        spans.append((t, t + dur))
        if shots:
            lines.append(f"| {act.get('name')} | {tc(t)}–{tc(t + dur)} ({dur:.0f} s) | {shots[0].id}–{shots[-1].id} | "
                         f"{act.get('job') or ''} |")
        t += dur
    lines.append("")
    t = 0.0
    for i, act in enumerate(plan.acts, 1):
        start, end = spans[i - 1]
        lines += [f"## {act.get('name')} · {tc(start)}–{tc(end)}", ""]
        if act.get("job"):
            lines += [f"*{act['job']}*", ""]
        if act.get("intent"):
            lines += [str(act["intent"]), ""]
        for s in (x for x in plan.shots if x.act == i):
            d = s.data
            lines.append(f"**{s.id}** {tc(t)}–{tc(t + s.dur)} ({s.dur:.1f} s) · on screen: {d.get('on_screen') or '—'}")
            lines.append(f"> {s.vo}" if s.vo else "> (no voice)")
            if d.get("picture"):
                lines.append(f"Picture: {d['picture']}")
            if d.get("transition"):
                lines.append(f"Transition out: {transition_words(d['transition'])}")
            sources = [x if isinstance(x, str) else (x.get("ref") or x.get("id") or str(x)) for x in d.get("sources") or []]
            if sources:
                lines.append(f"Sources: {'; '.join(sources)}")
            if d.get("notes"):
                lines.append(f"Notes: {d['notes']}")
            lines.append("")
            t += s.dur
    lines += ["## Script for the booth", "",
              "The clean read, in order, with each line's shot and in-point.", ""]
    t = 0.0
    for s in plan.shots:
        if s.vo:
            lines.append(f"- `{s.id} {tc(t + s.lead)}` {s.vo}")
        t += s.dur
    lines += ["", "## Checks", ""]
    for r in results:
        lines.append(f"- **{r.status}** {r.gate}: {r.summary}")
        for ev in r.evidence[:8] if r.status != "PASS" else []:
            lines.append(f"  - {ev}")
    s = summarize(results)
    lines += ["", f"Overall: **{s['status']}**. Only PASS counts as passed."]
    if film_id:
        lines += ["", f"Film `{film_id}`. Edit script/script.json, then run `slate script build`."]
    return "\n".join(lines).rstrip() + "\n"
