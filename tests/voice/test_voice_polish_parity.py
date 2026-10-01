"""Port parity: Slate's numpy chain against the films' own polished takes (golden data, read-only).

The films polished their voices with a chain whose EQ and compressor ran in a GPL-licensed plug-in host; Slate's port
is numpy and scipy only. On the films' real takes, with the films' own recorded settings, Slate must come out the same
to within the tolerance below. The takes are found through the films' own polish records (no names are written here),
copied into a temp folder, and polished there; nothing in the film project is ever written.

Tolerance, and what it was on 2026-10-01 (three takes, two voices, both named strengths and a single middle one):
  per-band level difference      at most 0.05 dB   (measured: 0.001 dB or less)
  spectral distance (RMS thirds) at most 0.05 dB   (measured: 0.001 dB or less)
  sample-domain residual         at most -55 dB    (measured: -64 to -84 dB under the film's take; the gap is 24-bit
                                                    quantization, float32 against float64, and the true-peak meter)
  loudness and true peak         within 0.05 dB of the film's record; the length identical, sample for sample
"""

import json
import shutil
from pathlib import Path

import pytest

from slate.voice import audio, polish

BAND_TOL_DB = 0.05
DISTANCE_TOL_DB = 0.05
RESIDUAL_MAX_DB = -55.0
LEVEL_TOL_DB = 0.05


def _sets(root: Path) -> list[dict]:
    """The films' polished sets, from their own records: a voice folder whose takes.json records the polish (the
    chain, the strength, the unpolished source folder and each line's report), and a clip's vo/polish.json."""
    out = []
    vo = root / "sound" / "vo"
    for tk in sorted(vo.glob("*/takes.json")) if vo.is_dir() else []:
        try:
            pol = json.loads(tk.read_text()).get("polish") or {}
        except (OSError, ValueError):
            continue
        if pol.get("chain") and pol.get("source_set") and pol.get("per_line"):
            out.append({"dir": tk.parent, "source_dir": vo / pol["source_set"], "chain": pol["chain"],
                        "strength": pol.get("strength"), "lines": pol["per_line"]})
    for pj in sorted(root.glob("build/*/audio_p3/*/vo/polish.json")):
        try:
            rec = json.loads(pj.read_text())
        except (OSError, ValueError):
            continue
        if not (rec.get("chain") and rec.get("per_take")):
            continue
        sources = {}
        for take_id in rec["per_take"]:
            polished = pj.parent / f"{take_id}.wav"
            if not polished.is_file():
                continue
            size = polished.stat().st_size
            cands = [p for p in sorted(vo.glob(f"*/{take_id}.wav")) if p.stat().st_size == size]
            if len(cands) == 1:
                sources[take_id] = cands[0]
        out.append({"dir": pj.parent, "sources": sources, "chain": rec["chain"], "strength": rec.get("strength"),
                    "lines": rec["per_take"]})
    return out


def _cases(root: Path) -> list[tuple[str, Path, Path, dict, dict]]:
    """Up to three (label, source, film's polished take, film chain, film's own record) cases: two lines from the
    first named-strength set, one from a second voice's clip (or the other strength when there's no clip)."""
    sets = _sets(root)
    voices = [s for s in sets if "sources" not in s]
    clips = [s for s in sets if s.get("sources")]
    sets = voices[:1] + clips + voices[1:]  # a second voice before a second strength of the first
    picked = []
    for s in sets:
        ids = sorted(s["lines"])
        want = 2 if not picked else 1
        for take_id in ids:
            src = (s.get("sources") or {}).get(take_id) or (s.get("source_dir") and s["source_dir"] / f"{take_id}.wav")
            film = s["dir"] / f"{take_id}.wav"
            if not src or not Path(src).is_file() or not film.is_file():
                continue
            picked.append((f"{s['dir'].name}/{take_id} ({s['strength']})", Path(src), film, s["chain"],
                           s["lines"][take_id]))
            want -= 1
            if want == 0:
                break
        if len(picked) >= 3:
            break
    return picked[:3]


def test_the_chain_matches_the_films_polished_takes(filmroot, tmp_path):
    cases = _cases(filmroot)
    if len(cases) < 2:
        pytest.skip("the films' polished sets aren't there (or have moved on)")
    results = {}
    for label, src, film_take, film_chain, record in cases:
        a, b = tmp_path / f"{len(results)}_src.wav", tmp_path / f"{len(results)}_film.wav"
        shutil.copyfile(src, a)  # copied, never read in place for writing
        shutil.copyfile(film_take, b)
        x, sr = audio.read_wav(a)
        theirs, _ = audio.read_wav(b)
        if abs(polish._lufs(x, sr) - float(record["lufs_in"])) > 0.02:  # noqa: SLF001
            continue  # the film re-voiced this line after polishing it: its source is no longer the polish's input
        chain = polish.from_film_chain(film_chain)
        ours, rep = polish.apply(x, sr, chain)
        assert len(ours) == len(theirs) == len(x), label
        cmp = polish.compare_outputs(x, theirs, ours, sr, chain)
        worst = max(abs(v) for v in cmp["bands_diff_db"].values())
        results[label] = {"worst_band_db": worst, "distance_db": cmp["spectral_distance_db"],
                          "residual_db": cmp["residual_db"]}
        assert worst <= BAND_TOL_DB, (label, cmp["bands_diff_db"])
        assert cmp["spectral_distance_db"] <= DISTANCE_TOL_DB, (label, cmp["thirds_diff_db"])
        assert cmp["residual_db"] <= RESIDUAL_MAX_DB, (label, cmp["residual_db"])
        assert abs(rep["lufs_out"] - float(record["lufs_out"])) <= LEVEL_TOL_DB, label
        assert abs(rep["true_peak_out_dbtp"] - float(record["true_peak_out_dbtp"])) <= LEVEL_TOL_DB, label
        assert abs(rep["peak_trim_db"] - float(record["peak_trim_db"])) <= LEVEL_TOL_DB, label
        for stage in ("edge", "deess"):
            assert abs(rep[stage]["max_cut_db"] - record[stage]["max_cut_db"]) <= 0.05, (label, stage)
        assert abs(rep["compressor_gr_db"]["median_loud"] - record["comp_gr_db"]["median_loud"]) <= 0.05, label
    if len(results) < 2:
        pytest.skip("the films' polished takes no longer match their sources")
