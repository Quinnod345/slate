"""Audio gates on the final mix and its stems.

- loudness      integrated loudness on target (−14 LUFS ±0.5 unless film.json says otherwise) and true peak at or
                under the ceiling (−1 dBTP), by ffmpeg's EBU R128 meter;
- bit_depth     the mix and every stem are PCM at the delivery bit depth and rate (24-bit, 48 kHz by default).
                The films' validator computed this and then never used it (an operator-precedence slip left `ok`
                unused), so a 16-bit mix passed. Here the header is read directly and the verdict gates;
- silent_stems  no stem is silent. The films' test was "integrated < −70 LUFS", which can't fire: ebur128 reads
                digital silence as exactly −70.0, its floor, so B's silent ambience stem passed. Here a stem is
                silent when its samples peak under −90 dBFS or its loudness sits at the floor (≤ −70.0);
- stems_sum     the stems add up to the mix, sample for sample, to within 1e-6.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from slate import __version__, gates
from slate.errors import SlateError
from slate.gates import GateResult
from slate.qa import audio, media, wav
from slate.qa.checks import Sub, combine, plural
from slate.qa.film import FilmContext, context

DEFAULTS = {"lufs": -14.0, "lufs_tolerance": 0.5, "true_peak": -1.0, "bit_depth": 24, "sample_rate": 48000,
            "stems_sum_tolerance": 1e-6}


def _target(ctx: FilmContext, key: str, *aliases: str):
    t = ctx.targets
    for k in (key, *aliases):
        if t.get(k) is not None:
            return t[k], "film.json"
    return DEFAULTS[key], "default"


def _no_mix(gate: str, ctx: FilmContext, engine: dict) -> GateResult:
    from slate.qa import film as filmmod  # noqa: PLC0415

    return GateResult.unchecked(gate, "there's no final mix yet", evidence=["next: slate mix"], engine=engine,
                                inputs=ctx.inputs([*filmmod.MIX_RECORDS, *filmmod.MIX_CANDIDATES]))


# ---------------------------------------------------------------------------------------------- loudness
def loudness(film_dir: Path) -> GateResult:
    """Integrated loudness on target and true peak under the ceiling, measured by ffmpeg's EBU R128 meter."""
    ctx = context(film_dir)
    engine = audio.engine()
    mix = ctx.mix_path()
    if mix is None:
        return _no_mix("loudness", ctx, engine)
    inputs = ctx.inputs(["film.json", mix])
    if not media.have("ffmpeg"):
        return GateResult.unchecked("loudness", "ffmpeg isn't installed, so the mix can't be metered",
                                    evidence=["next: slate doctor --install"], engine=engine, inputs=inputs)
    target, t_src = _target(ctx, "lufs")
    tol, _ = _target(ctx, "lufs_tolerance", "lufs_tol")
    ceiling, c_src = _target(ctx, "true_peak", "true_peak_dbtp")
    try:
        m = audio.ebur128(mix)
    except SlateError as exc:
        return GateResult.unchecked("loudness", f"ffmpeg couldn't meter the mix: {exc}", engine=engine,
                                    inputs=inputs)
    i, tp, lra = m["integrated_lufs"], m["true_peak_dbtp"], m["lra_lu"]
    line = (f"{ctx.rel(mix)}: {audio.rounded(i)} LUFS integrated (target {target} ±{tol}, {t_src}), true peak "
            f"{audio.rounded(tp)} dBTP (ceiling {ceiling}, {c_src}), LRA {audio.rounded(lra)} LU")
    subs = []
    if i is None or math.isinf(i) or abs(i - float(target)) > float(tol) + 1e-9:
        subs.append(Sub("integrated", "FAIL", f"the mix measures {audio.rounded(i)} LUFS; the target is {target} ±{tol}",
                        [line]))
    else:
        subs.append(Sub("integrated", "PASS", f"{audio.rounded(i)} LUFS integrated (target {target} ±{tol})", [line]))
    if tp is not None and not (tp <= float(ceiling) + 1e-9):
        subs.append(Sub("true_peak", "FAIL", f"true peak {audio.rounded(tp)} dBTP is over the {ceiling} dBTP ceiling",
                        [line]))
    else:
        subs.append(Sub("true_peak", "PASS", f"true peak {audio.rounded(tp)} dBTP (ceiling {ceiling})", [line]))
    return combine("loudness", subs, engine=engine, inputs=inputs,
                   details={"file": ctx.rel(mix), "integrated_lufs": audio.rounded(i), "true_peak_dbtp": audio.rounded(tp),
                            "lra_lu": audio.rounded(lra), "target_lufs": target, "tolerance": tol,
                            "ceiling_dbtp": ceiling})


# ---------------------------------------------------------------------------------------------- bit depth
def _format_of(path: Path) -> dict:
    """{format, bits, sample_rate, channels, how} from the WAV header, or from ffprobe for anything else."""
    try:
        wi = wav.info(path)
        bits = wi.valid_bits or wi.bits_per_sample
        return {"format": wi.format, "bits": bits, "sample_rate": wi.sample_rate, "channels": wi.channels,
                "how": "WAV header", "describe": wi.describe()}
    except wav.WavError:
        pass
    streams = media.audio_streams(path)
    if not streams:
        raise SlateError(f"{path.name} has no audio stream")
    s = streams[0]
    raw = s.get("bits_per_raw_sample") or s.get("bits_per_sample") or 0
    codec = str(s.get("codec_name") or "")
    kind = "float" if codec.startswith("pcm_f") or s.get("sample_fmt") in ("flt", "fltp", "dbl", "dblp") else (
        "pcm" if codec.startswith("pcm_") or codec in ("flac", "alac") else codec)
    return {"format": kind, "bits": int(raw or 0), "sample_rate": int(s.get("sample_rate") or 0),
            "channels": int(s.get("channels") or 0), "how": "ffprobe",
            "describe": f"{codec} {raw}-bit {s.get('sample_rate')} Hz {s.get('channels')} ch"}


def bit_depth(film_dir: Path) -> GateResult:
    """The mix and every stem are integer PCM at the delivery bit depth and sample rate (24-bit, 48 kHz unless
    film.json's targets say otherwise). A 16-bit or float mix fails, whatever it measures."""
    ctx = context(film_dir)
    engine = gates.engine("slate WAV header reader", __version__, fallback=f"ffprobe {media.version('ffprobe')}")
    mix = ctx.mix_path()
    if mix is None:
        return _no_mix("bit_depth", ctx, engine)
    want_bits, _ = _target(ctx, "bit_depth")
    want_rate, _ = _target(ctx, "sample_rate")
    files = [("mix", mix), *sorted(ctx.stem_paths().items())]
    problems, rows = [], []
    for label, path in files:
        try:
            f = _format_of(path)
        except SlateError as exc:
            problems.append(f"{label} {ctx.rel(path)}: couldn't read its format ({exc})")
            continue
        rows.append(f"{label} {ctx.rel(path)}: {f['describe']} ({f['how']})")
        if f["format"] != "pcm" or int(f["bits"]) != int(want_bits):
            problems.append(f"{label} {ctx.rel(path)} is {f['bits']}-bit {f['format']}, delivery is "
                            f"{want_bits}-bit PCM")
        if int(f["sample_rate"]) != int(want_rate):
            problems.append(f"{label} {ctx.rel(path)} is {f['sample_rate']} Hz, delivery is {want_rate} Hz")
    inputs = ctx.inputs(["film.json", *[p for _l, p in files]])
    if problems:
        sub = Sub("format", "FAIL", problems[0], problems + rows)
    else:
        sub = Sub("format", "PASS", f"{plural(len(rows), 'file')} at {want_bits}-bit PCM, {want_rate} Hz", rows)
    return combine("bit_depth", [sub], engine=engine, inputs=inputs,
                   details={"want_bits": want_bits, "want_sample_rate": want_rate})


# ---------------------------------------------------------------------------------------------- silent stems
def is_silent(peak_dbfs: float, integrated_lufs: float | None) -> bool:
    """Silent: the samples peak under −90 dBFS, or the meter sits at its floor (every block under −70 LUFS)."""
    if peak_dbfs <= audio.SILENT_PEAK_DBFS:
        return True
    return integrated_lufs is not None and integrated_lufs <= audio.LOUDNESS_FLOOR


def silent_stems(film_dir: Path) -> GateResult:
    """No stem is silent (sample peak under −90 dBFS, or loudness at the meter's −70 LUFS floor)."""
    ctx = context(film_dir)
    have_ffmpeg = media.have("ffmpeg")
    engine = gates.engine("slate sample peak", __version__,
                          meter=f"ffmpeg ebur128 {media.version('ffmpeg')}" if have_ffmpeg else "not installed")
    stems = ctx.stem_paths()
    mix = ctx.mix_path()
    if not stems:
        if mix is None:
            return _no_mix("silent_stems", ctx, engine)
        return GateResult.unchecked("silent_stems", f"no stems were found beside {ctx.rel(mix)}",
                                    evidence=["the mix record (sound/mix.json \"stems\") or a stems/ folder names them"],
                                    engine=engine, inputs=ctx.inputs([mix, "sound/mix.json"]))
    silent, rows, problems = [], [], []
    for name, path in sorted(stems.items()):
        try:
            lv = audio.levels(path)
        except (OSError, wav.WavError) as exc:
            problems.append(f"{name} {ctx.rel(path)}: couldn't read it ({exc})")
            continue
        integrated = None
        if have_ffmpeg:
            try:
                integrated = audio.ebur128(path, true_peak=False)["integrated_lufs"]
            except SlateError as exc:
                problems.append(f"{name}: ffmpeg couldn't meter it ({exc})")
        row = (f"{name} {ctx.rel(path)}: peak {audio.rounded(lv['peak_dbfs'])} dBFS, "
               f"{audio.rounded(integrated)} LUFS" + (" (digital silence)" if lv["digital_silence"] else ""))
        rows.append(row)
        if is_silent(lv["peak_dbfs"], integrated):
            silent.append(name)
    inputs = ctx.inputs(list(stems.values()))
    subs = []
    if silent:
        subs.append(Sub("silent", "FAIL", f"silent stem{'s' if len(silent) > 1 else ''}: {', '.join(silent)}",
                        rows + ["a stem that should be empty isn't a stem: leave it out of the mix record"]))
    if problems:
        subs.append(Sub("unreadable", "UNCHECKED", problems[0], problems))
    if not subs:
        subs.append(Sub("stems", "PASS", "the one stem carries sound" if len(rows) == 1 else
                        f"all {len(rows)} stems carry sound", rows))
    return combine("silent_stems", subs, engine=engine, inputs=inputs, details={"silent": silent})


# ---------------------------------------------------------------------------------------------- stems sum
def stems_sum(film_dir: Path) -> GateResult:
    """The stems add up to the mix sample for sample (max |Σ stems − mix| ≤ 1e-6 of full scale by default)."""
    ctx = context(film_dir)
    engine = gates.engine("slate WAV reader (numpy float64 sum)", __version__, numpy=np.__version__)
    mix = ctx.mix_path()
    if mix is None:
        return _no_mix("stems_sum", ctx, engine)
    stems = ctx.stem_paths()
    inputs = ctx.inputs(["film.json", mix, *stems.values()])
    if not stems:
        return GateResult.unchecked("stems_sum", f"no stems were found beside {ctx.rel(mix)}", engine=engine,
                                    inputs=inputs)
    tol, _ = _target(ctx, "stems_sum_tolerance")
    try:
        mi = wav.info(mix)
        infos = {n: wav.info(p) for n, p in stems.items()}
    except wav.WavError as exc:
        return GateResult.unchecked("stems_sum", f"the mix and stems must be WAV to be summed exactly: {exc}",
                                    engine=engine, inputs=inputs)
    shape_problems = [f"{n} is {i.sample_rate} Hz / {i.channels} ch; the mix is {mi.sample_rate} Hz / {mi.channels} ch"
                      for n, i in infos.items() if (i.sample_rate, i.channels) != (mi.sample_rate, mi.channels)]
    if shape_problems:
        return combine("stems_sum", [Sub("shape", "FAIL", shape_problems[0], shape_problems)], engine=engine,
                       inputs=inputs)
    worst, worst_at, block = 0.0, 0, 1 << 18
    for start in range(0, mi.frames, block):
        m, _ = wav.read(mix, start, block)
        total = np.zeros_like(m)
        for name, path in stems.items():
            s, _ = wav.read(path, start, len(m))
            total[: len(s)] += s
        d = np.abs(total - m)
        if d.size:
            k = int(np.argmax(d))
            if float(d.flat[k]) > worst:
                worst, worst_at = float(d.flat[k]), start + k // mi.channels
    tails = []
    for name, i in infos.items():
        if i.frames > mi.frames:
            extra, _ = wav.read(stems[name], mi.frames, i.frames - mi.frames)
            if extra.size and float(np.max(np.abs(extra))) > tol:
                tails.append(f"{name} carries sound after the mix ends ({(i.frames - mi.frames) / mi.sample_rate:.3f} s)")
    line = (f"max |Σ stems − mix| = {worst:.3g} at {worst_at / mi.sample_rate:.3f} s over "
            f"{plural(len(stems), 'stem')} ({', '.join(sorted(stems))}); tolerance {tol:g}")
    subs = []
    if worst > float(tol):
        subs.append(Sub("sum", "FAIL", f"the stems don't add up to the mix: {line}", [line]))
    else:
        subs.append(Sub("sum", "PASS", f"the stems add up to the mix ({worst:.3g} ≤ {tol:g})", [line]))
    if tails:
        subs.append(Sub("tails", "FAIL", tails[0], tails))
    return combine("stems_sum", subs, engine=engine, inputs=inputs,
                   details={"max_abs": worst, "at_seconds": round(worst_at / mi.sample_rate, 6), "tolerance": tol})
