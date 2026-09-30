"""The readable report beside analysis.json: the short version, the method table, the scenes and the measured eases,
in the shape of the films' ANALYSIS.md."""

from __future__ import annotations

from slate.analyze import eases as eases_mod

STEP_LABELS = {
    "probe": "Probe", "cuts": "Scene cuts", "ocr": "On-screen text", "eases": "Measured eases",
    "audio": "Loudness", "stems": "Voice and music split", "transcript": "Transcript", "beats": "Music tempo",
}


def _mmss(t: float | None) -> str:
    if t is None:
        return ""
    m, s = divmod(float(t), 60)
    return f"{int(m)}:{s:05.2f}"


def _fps(video: dict) -> str:
    num, den = video.get("fps_num"), video.get("fps_den")
    if not num or not den:
        return "unknown fps"
    return f"{num / den:.3f}".rstrip("0").rstrip(".") + " fps"


def _cell(text: str | None, limit: int = 160) -> str:
    t = " ".join(str(text or "").split()).replace("|", "\\|")
    return t if len(t) <= limit else t[: limit - 1] + "…"


def render(a: dict) -> str:
    v = a["video"]
    vi = v["video"]
    lines = [f"# Analysis: {v['name']}", ""]
    audio = v.get("audio")
    audio_text = (f"{audio.get('codec')} {audio.get('channels')}-channel at {audio.get('sample_rate')} Hz" if audio
                  else "no audio")
    lines.append(f"Made by `slate analyze` on {a['made_at'][:10]}. {v.get('duration_s') or 0:.2f} s of "
                 f"{vi['width']} x {vi['height']} at {_fps(vi)} ({vi.get('frames')} frames, {vi.get('codec')}); "
                 f"{audio_text}. SHA-256 `{v['sha256'][:16]}…`.")
    lines += ["", "## The short version", ""]
    cuts = a.get("cuts") or {}
    scenes = a.get("scenes") or []
    if cuts:
        longest = cuts.get("longest_scene") or {}
        lines.append(f"- **Shape.** {len(scenes)} scenes: {len(cuts.get('hard_cuts', []))} hard cuts and "
                     f"{len(cuts.get('changes', []))} softer changes. Scenes average {cuts.get('average_scene_s') or 0:.1f} s"
                     + (f"; the longest (S{longest['id']}) holds {longest['dur']:.1f} s." if longest else "."))
    tr = a.get("transcript") or {}
    if tr.get("stats", {}).get("words"):
        st = tr["stats"]
        replayed = " (replayed, not a real transcript)" if tr.get("engine", {}).get("replay") else ""
        lines.append(f"- **Pace.** {st['words']} words from {_mmss(st['first_s'])} to {_mmss(st['last_s'])}: "
                     f"{st['wpm']:g} words a minute ({st['words_per_second']:.2f} a second), "
                     f"{len(st['pauses'])} pauses over 0.8 s{replayed}.")
    mix = (a.get("audio") or {}).get("mix") or {}
    if mix.get("integrated_lufs") is not None:
        lines.append(f"- **Loudness.** Integrated {mix['integrated_lufs']:.1f} LUFS, LRA {mix.get('lra_lu') or 0:.1f} LU, "
                     f"true peak {mix.get('true_peak_dbtp') or 0:.1f} dBTP (web delivery is -14 LUFS).")
    beats = a.get("beats") or {}
    if beats.get("tempo_bpm"):
        if beats.get("steady") and beats.get("tempo_from_beats_bpm"):
            lines.append(f"- **Music.** About {beats['tempo_from_beats_bpm']:g} BPM from a steady beat every "
                         f"{beats['interval_mean_s']:.3f} s (librosa's own estimate: {beats['tempo_bpm']:g}), "
                         f"on the {beats.get('source', 'mix')}.")
        else:
            lines.append(f"- **Music.** About {beats['tempo_bpm']:g} BPM (librosa's estimate) on the "
                         f"{beats.get('source', 'mix')}.")
    moves = (a.get("eases") or {}).get("moves") or []
    if moves:
        best = sorted(moves, key=lambda m: m["rms"])[:3]
        lines.append(f"- **Motion.** {len(moves)} measured move{'s' if len(moves) != 1 else ''}; the cleanest: "
                     + "; ".join(f"{m['name']} {m['duration_s']:.2f} s, nearest {m['best_named']['name']}"
                                 for m in best if m.get("best_named")) + ".")
    open_steps = [(k, r) for k, r in a["steps"].items() if r["status"] != "PASS"]
    for k, r in open_steps:
        lines.append(f"- **Not checked: {STEP_LABELS.get(k, k).lower()}.** {r['summary']}"
                     + "".join(f" {e}" for e in r.get("evidence", []) if str(e).startswith("next:")))
    lines += ["", "## Method", "", "| Step | Status | What ran | Result |", "|---|---|---|---|"]
    for k, r in a["steps"].items():
        eng = r.get("engine") or {}
        what = " ".join(str(x) for x in (eng.get("name"), eng.get("version")) if x)
        lines.append(f"| {STEP_LABELS.get(k, k)} | {r['status']} | {_cell(what, 60)} | {_cell(r['summary'])} |")
    if scenes:
        lines += ["", "## Scenes", "", "| # | Start | Length | Starts with | Voice | On screen |", "|---|---|---|---|---|---|"]
        for sc in scenes:
            hf = sc.get("higgsfield") or {}
            label = f" ({hf['label']})" if hf.get("label") else ""
            lines.append(f"| S{sc['id']}{_cell(label, 40)} | {_mmss(sc['start'])} | {sc['dur']:.2f} s | {sc['starts_with']} | "
                         f"{_cell(sc.get('vo'))} | {_cell(' / '.join(sc.get('ocr') or []))} |")
    if moves:
        lines += ["", "## Measured eases", "",
                  "| Move | Window | Channel | cubic-bezier | RMS | Nearest named ease | Overshoot |", "|---|---|---|---|---|---|---|"]
        for m in moves:
            near = m.get("best_named") or {}
            lines.append(f"| {m['name']} | {_mmss(m['window_s'][0])}-{_mmss(m['window_s'][1])} | {m['channel']} | "
                         f"({', '.join(f'{x:g}' for x in m['bezier'])}) | {m['rms'] * 100:.2f}% ({m['confidence']}) | "
                         f"{near.get('name', '')} ({near.get('family', '')}, {near.get('rms', 0) * 100:.1f}%) | "
                         f"{m['overshoot'] * 100:.0f}% |")
        lines += ["", "In words: " + "; ".join(eases_mod.summary_line(m) for m in moves) + "."]
    skipped = (a.get("eases") or {}).get("skipped") or []
    if skipped:
        lines += ["", "Moves the tracker saw but couldn't fit: "
                  + "; ".join(f"{s['name']} at {_mmss(s['window_s'][0])} ({s['reason']})" for s in skipped) + "."]
    notes = list(a.get("notes") or [])
    if notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in notes]
    return "\n".join(lines) + "\n"
