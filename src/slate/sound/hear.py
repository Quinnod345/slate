"""What a listener hears in the delivered mix: Whisper on the whole mix, word for word against the script.

The voice gates hear each take on its own. A mix can still lose a word under the music, or make Whisper hear one that
isn't there, so `sound.mix_words` (slate.sound.verify) runs Whisper on the delivered mix through Slate's one Whisper
runner (slate.voice.align.transcribe: the CLI SLATE_WHISPER names, one at a time) and compares, shot by shot, after
only the normalization a listener can't hear:

- spelled numbers against digits (Whisper writes "2,500" where a line may say "twenty-five hundred"), through the
  timing module's spoken forms;
- apostrophes dropped ("team's" and "teams" sound the same);
- a number with "percent" as the script's "%" ("30 percent" is "30%");
- the brand's known misspellings as its name (the voice profile's `heard_as`: {"Name": ["how Whisper spells it"]}).

An extra heard word counts against the mix only where the voice is sounding at that moment and Whisper is at least
20% sure of it. Otherwise it's listed as heard over the music: Whisper invents words after speech ends, and the
films' gate learned to set those aside (one film's last line came back as "...where you stand. you").

In replay mode (SLATE_FAKE_VENDORS=1) nothing is transcribed: a recorded transcript of this exact mix answers when
there is one, else a stand-in made from the voice's own placed words. A stand-in only ever stands in for a stand-in
voice; a mix of real takes waits for a real hearing (UNCHECKED, never passed).
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from slate import jsonio, replay
from slate.sound import audio, guard, vo
from slate.sound.film import Film, words_for

PROBABILITY_MIN = 0.2  # an extra word Whisper is less sure of than this is the music talking
SPAN_PAD_S = 0.35  # a heard word belongs to a shot when it falls within this of the shot's own words
PROVIDER, OP = "whisper", "transcribe_mix"


def _fake_transcript(key: str, request: dict) -> dict:
    """A stand-in transcript of a mix: the voice's own placed words, exactly where the voice says them."""
    words = [{"word": f" {w}", "start": round(float(s), 3), "end": round(float(e), 3), "probability": 1.0}
             for w, s, e in request.get("words") or []]
    text = "".join(w["word"] for w in words)
    return {"text": text, "language": request.get("language"),
            "segments": [{"id": 0, "start": words[0]["start"], "end": words[-1]["end"], "text": text, "words": words}]
            if words else []}


replay.register_faker(PROVIDER, OP, _fake_transcript)


# ---------------------------------------------------------------------------------------------------------------
# The script and what was heard, as comparable tokens


def heard_as(film: Film) -> dict[str, list[str]]:
    """The brand's known misspellings, {name: [spellings]}: the voice profile's heard_as (brand.json voice, with the
    film's own voice block over it)."""
    out: dict[str, list[str]] = {}
    for prof in ((film.brand or {}).get("voice"), film.data.get("voice")):
        raw = prof.get("heard_as") if isinstance(prof, dict) else None
        for k, vs in (raw.items() if isinstance(raw, dict) else ()):
            spellings = [str(v) for v in (vs if isinstance(vs, list) else [vs]) if str(v).strip()]
            if str(k).strip() and spellings:
                out[str(k)] = spellings
    return out


def tokens(text: str) -> list[str]:
    """A line as a listener's words: case-folded, punctuation and apostrophes gone, "%" said as "percent"."""
    from slate.voice import lang  # noqa: PLC0415

    t = str(text).replace("%", " percent ")
    return [w.replace("'", "") for w in lang.norm_words(t) if w.replace("'", "")]


def _respell(toks: list[tuple[str, int]], names: dict[str, list[str]]) -> list[tuple[str, int]]:
    """Whisper's known spellings of a brand word, back to the word (token runs, so "Demo Studios" can be "DemoStudio")."""
    rules = [(tokens(sp), tokens(name)) for name, sps in names.items() for sp in sps]
    rules = sorted([r for r in rules if r[0] and r[1]], key=lambda r: -len(r[0]))
    out, k = [], 0
    while k < len(toks):
        for sp, name in rules:
            if [t for t, _ in toks[k:k + len(sp)]] == sp:
                out += [(w, toks[k][1]) for w in name]
                k += len(sp)
                break
        else:
            out.append(toks[k])
            k += 1
    return out


def align(ref: list[str], hyp: list[str]) -> list[tuple[str, int | None, int | None]]:
    """Levenshtein alignment as ops [(op, ref index, hyp index)]: "ok", "sub", "del" (a script word not heard) and
    "ins" (a heard word the script doesn't have)."""
    n, m = len(ref), len(hyp)
    D = np.zeros((n + 1, m + 1), dtype=int)
    D[:, 0], D[0, :] = np.arange(n + 1), np.arange(m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            D[i, j] = min(D[i - 1, j] + 1, D[i, j - 1] + 1, D[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]))
    i, j, ops = n, m, []
    while i > 0 or j > 0:
        if i > 0 and j > 0 and D[i, j] == D[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]):
            ops.append(("ok" if ref[i - 1] == hyp[j - 1] else "sub", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and D[i, j] == D[i - 1, j] + 1:
            ops.append(("del", i - 1, None))
            i -= 1
        else:
            ops.append(("ins", None, j - 1))
            j -= 1
    return ops[::-1]


def _edits(ref: list[str], hyp: list[str]) -> int:
    return sum(1 for op, _a, _b in align(ref, hyp) if op != "ok")


def _say_numbers(toks: list[tuple[str, int]], other: list[str], language: str) -> list[tuple[str, int]]:
    """Digits said the way the other side has them, when a spoken form matches it better ("2500" -> twenty five
    hundred, against a line that spells it out)."""
    try:
        from slate.timing import spoken  # noqa: PLC0415
    except ModuleNotFoundError:
        return toks
    out, k = list(toks), 0
    while k < len(out):
        tok, idx = out[k]
        if any(ch.isdigit() for ch in tok):
            try:
                forms = spoken.forms(tok, lang=language) or []
            except Exception:  # noqa: BLE001 - a number spoken can't read stays as written
                forms = []
            plain = [t for t, _ in out]
            best, best_edits = None, _edits(plain, other)
            for form in forms:
                words = tokens(" ".join(form))
                edits = _edits(plain[:k] + words + plain[k + 1:], other)
                if words and edits < best_edits:
                    best, best_edits = words, edits
            if best is not None:
                out = out[:k] + [(w, idx) for w in best] + out[k + 1:]
                k += len(best)
                continue
        k += 1
    return out


# ---------------------------------------------------------------------------------------------------------------
# The comparison


def script_lines(film: Film) -> list[dict]:
    """Every voiced line in film order: {shot, line, start, end} (the line's words' own span at film time)."""
    doc = jsonio.read(film.source("timing/vo_words.json"), default={}) or {}
    shots = doc.get("shots") or {}
    placed = {}
    for cue in vo.cues(film):
        shot = cue.get("shot")
        entry = shots.get(shot) or {}
        t0 = vo.cue_time(film, cue) - (float(cue["trim"][0]) if cue.get("trim") else 0.0)
        ws = entry.get("words") or []
        start = t0 + float(ws[0]["start"]) if ws else vo.cue_time(film, cue)
        end = t0 + float(ws[-1]["end"]) if ws else start
        line = entry.get("line")
        if line is None:
            line = " ".join(str(w.get("w", "")) for w in ws)
        placed[shot] = {"shot": shot, "line": str(line), "start": round(start, 3), "end": round(end, 3),
                        "aligner": entry.get("aligner") or {}}
    return sorted(placed.values(), key=lambda r: r["start"])


def compare(lines: list[dict], heard: list[dict], sounding, *, language: str = "en",
            names: dict[str, list[str]] | None = None) -> dict:
    """Shot by shot, the script's words against the words heard in the mix. sounding(t) says whether the voice sounds
    at film second t. Returns {errors, over_music, per_shot}."""
    names = names or {}
    owner: list[int | None] = []
    for w in heard:
        w0, w1 = float(w["start"]), float(w["end"])
        mid = (w0 + w1) / 2
        inside = [i for i, ln in enumerate(lines) if ln["start"] - SPAN_PAD_S <= mid <= ln["end"] + SPAN_PAD_S]
        if inside:
            owner.append(min(inside, key=lambda i: abs(mid - (lines[i]["start"] + lines[i]["end"]) / 2)))
            continue
        # Whisper stamps a segment's first word from the end of the word before, so it can stretch across the pause
        # ("For" 50.24-51.40 s before a line that starts at 51.27). Such a word belongs to the line its span reaches
        # into most; the alignment below still says whether it's the right word.
        reach = {i: min(w1, ln["end"] + SPAN_PAD_S) - max(w0, ln["start"] - SPAN_PAD_S) for i, ln in enumerate(lines)}
        best = max(reach, key=reach.get) if reach else None
        owner.append(best if best is not None and reach[best] > 0 else None)
    errors, over_music, per_shot = [], [], {}

    def extra(w: dict, where: str) -> None:
        t = round(float(w["start"]), 3)
        p = w.get("probability")
        row = {"word": str(w["word"]).strip(), "t": t, "probability": p, "where": where}
        if sounding(float(w["start"])) and (p is None or float(p) >= PROBABILITY_MIN):
            errors.append({**row, "what": "an extra word in the voice"})
        else:
            over_music.append(row)

    for i, ln in enumerate(lines):
        mine = [k for k, o in enumerate(owner) if o == i]
        ref = _respell([(t, -1) for t in tokens(ln["line"])], names)
        hyp = _respell([(t, k) for k in mine for t in tokens(heard[k]["word"])], names)
        r_plain = [t for t, _ in ref]
        hyp = _say_numbers(hyp, r_plain, language)
        ref = _say_numbers(ref, [t for t, _ in hyp], language)
        r_plain, h_plain = [t for t, _ in ref], [t for t, _ in hyp]
        ops = align(r_plain, h_plain)
        bad = []
        for op, a, b in ops:
            if op == "sub":
                bad.append(f"heard {h_plain[b]!r} for {r_plain[a]!r}")
                errors.append({"shot": ln["shot"], "what": "misheard", "script": r_plain[a], "heard": h_plain[b],
                               "t": round(float(heard[hyp[b][1]]["start"]), 3)})
            elif op == "del":
                bad.append(f"{r_plain[a]!r} not heard")
                errors.append({"shot": ln["shot"], "what": "not heard", "script": r_plain[a], "t": ln["start"]})
            elif op == "ins":
                extra(heard[hyp[b][1]], ln["shot"])
        per_shot[ln["shot"]] = {"heard": " ".join(str(heard[k]["word"]).strip() for k in mine),
                                "wer": round(sum(1 for op, _a, _b in ops if op in ("sub", "del")) / max(1, len(r_plain)), 4),
                                "problems": bad}
    for k, o in enumerate(owner):
        if o is None:
            extra(heard[k], "between lines")
    return {"errors": errors, "over_music": over_music, "per_shot": per_shot}


def stand_in(lines: list[dict]) -> bool:
    """True when the voice in the mix is replayed stand-ins (no line was aligned from a real take)."""
    return not any((ln.get("aligner") or {}).get("replay") is False for ln in lines)


def hear(film: Film, mix_path: Path, *, language: str = "en") -> tuple[dict, dict]:
    """(Whisper-shaped transcript of the mix, how it was heard: {engine, model, version, replay, recorded})."""
    from slate.voice import align as voice_align  # noqa: PLC0415

    model = voice_align.whisper_model(None)
    sha = jsonio.sha256(mix_path)
    key = f"mix-{sha[:16]}-{model}"
    if replay.fake_vendors():
        request = {"mix_sha256": sha, "language": language,
                   "words": [[w, round(s, 3), round(e, 3)] for w, s, e in words_for(film)]}
        recorded = replay.load(PROVIDER, key) is not None
        return replay.respond(PROVIDER, OP, request, key=key), {"engine": "openai-whisper", "model": model,
                                                                 "version": "replay", "replay": True,
                                                                 "recorded": recorded, "key": key}
    info = voice_align.whisper_info()
    duration = audio.duration_s(mix_path)
    # Each window heard on its own (no previous-text conditioning): a listener doesn't carry Whisper's running guess.
    result = voice_align.transcribe(mix_path, model=model, language=language, timeout=max(900.0, 8.0 * duration),
                                    context=False)
    return result, {"engine": "openai-whisper", "model": model, "version": info.get("version"), "replay": False,
                    "recorded": False, "key": key, "previous_text": False}


RELISTEN_PAD_S = 0.6  # a second listen hears a line's own span of the mix with this much either side


def relisten(film: Film, mix_path: Path, lines: list[dict], shots: list[str], *, language: str = "en") -> list[dict]:
    """A second listen: each named line's own span of the mix (its words, RELISTEN_PAD_S either side) heard on its
    own, in one Whisper run. Returns the heard words in film seconds, only those inside the line's span (so a
    neighbour caught in the pad isn't counted twice). Over a whole mix Whisper can jump past half a line (one film's
    "Because two businesses can look very similar financially" went unheard while its take sat at its cue); a listener
    hearing that line hears it."""
    import tempfile  # noqa: PLC0415

    from slate.voice import align as voice_align  # noqa: PLC0415
    from slate.voice import measure as voice_measure  # noqa: PLC0415

    want = [ln for ln in lines if ln["shot"] in set(shots)]
    if not want or replay.fake_vendors():
        return []
    model = voice_align.whisper_model(None)
    out: list[dict] = []
    whole = audio.load(mix_path, film.sr, None)
    with tempfile.TemporaryDirectory(prefix="slate-relisten-") as tmp:
        clips = {}
        for ln in want:
            a = max(0.0, float(ln["start"]) - RELISTEN_PAD_S)
            b = float(ln["end"]) + RELISTEN_PAD_S
            x = whole[int(a * film.sr):int(b * film.sr)]
            clip = Path(tmp) / f"{ln['shot']}.wav"
            audio.write_wav(clip, x, film.sr)
            clips[clip] = (ln, a)
        heard = voice_align.run_whisper(list(clips), Path(tmp) / "out", model=model, language=language,
                                        context=False)
        for clip, (ln, a) in clips.items():
            for w in voice_measure.whisper_words(heard.get(clip)):
                w0, w1 = float(w["start"]) + a, float(w["end"]) + a
                mid = (w0 + w1) / 2
                if ln["start"] - SPAN_PAD_S <= mid <= ln["end"] + SPAN_PAD_S:
                    out.append({**w, "start": round(w0, 3), "end": round(w1, 3), "relistened": ln["shot"]})
    return out


def sounding_at(voice: np.ndarray, sr: int):
    """A function: is the voice sounding at film second t? (its 50 ms level within 30 dB of its loud level)."""
    hop = int(round(guard.HOP_S * sr))
    spk = guard.speaking(voice, sr, hop)

    def f(t: float) -> bool:
        i = int(t / guard.HOP_S)
        return bool(0 <= i < len(spk) and spk[max(0, i - 2):i + 3].any())

    return f
