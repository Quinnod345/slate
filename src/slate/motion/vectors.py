"""The shared cross-language vectors for the motion law: tests/vectors/eases.json and tests/vectors/t90.json.

One file, three runners: pytest (tests/engine_kit), vitest (engine/tests/kit) and the After Effects ExtendScript runner
all evaluate the same curves at the same points and must match these numbers within the stated tolerance. The curves
are neutral on purpose (CSS's standard curves, the kit's built-ins, and edge cases for the solver: vertical tangents,
anticipation, overshoot); no brand's eases live here.

    python -m slate.motion.vectors --check     # regenerate in memory and compare with the files (exit 1 on drift)
    python -m slate.motion.vectors --write     # rewrite the files
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from slate import paths
from slate.motion.eases import ae_keyframe_ease, evaluate, mono_spline, parse, start_for, t_at, t90

VECTORS_DIR = paths.REPO / "tests" / "vectors"
EASES_FILE = VECTORS_DIR / "eases.json"
T90_FILE = VECTORS_DIR / "t90.json"
GENERATED = "do not edit by hand: python -m slate.motion.vectors --write"

CURVES: list[tuple[str, dict, str]] = [
    ("linear", {"kind": "linear"}, "straight: time and progress only"),
    ("sine", {"kind": "sine"}, "(1 − cos πu) ÷ 2, at rest at both ends"),
    ("spring0", {"kind": "spring0", "stiffness": 7}, "critically damped spring, normalised to land on 1"),
    ("spring0-stiff", {"kind": "spring0", "stiffness": 12}, "the same, stiffer"),
    ("css-ease", {"bezier": [0.25, 0.1, 0.25, 1.0]}, "CSS ease (W3C)"),
    ("css-ease-in", {"bezier": [0.42, 0.0, 1.0, 1.0]}, "CSS ease-in (W3C): its in-handle collapses onto the end key"),
    ("css-ease-out", {"bezier": [0.0, 0.0, 0.58, 1.0]}, "CSS ease-out (W3C): its out-handle collapses onto the start key"),
    ("css-ease-in-out", {"bezier": [0.42, 0.0, 0.58, 1.0]}, "CSS ease-in-out (W3C)"),
    ("firm-out", {"bezier": [0.2, 0.7, 0.2, 1.0]}, "a firm deceleration"),
    ("quick-commit", {"bezier": [0.8, 0.0, 0.2, 1.0]}, "a steep in-out"),
    ("hard-in-out", {"bezier": [1.0, 0.0, 0.0, 1.0]}, "solver stress: x flat at both ends"),
    ("vertical-start", {"bezier": [0.0, 0.5, 0.5, 1.0]}, "solver stress: vertical tangent at the start"),
    ("anticipate", {"bezier": [0.36, -0.4, 0.6, 1.0]}, "dips below 0 before it moves (y1 < 0)"),
    ("overshoot", {"bezier": [0.34, 1.56, 0.64, 1.0]}, "passes 1 before it settles (y2 > 1): T90 is its first crossing"),
]

U_SAMPLES: list[float] = [
    -0.5, -1e-9, 0.0, 1e-6, 0.001, 0.01, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 1 / 3, 0.4, 0.45, 0.5, 0.55, 0.6, 2 / 3, 0.7,
    0.75, 0.8, 0.85, 0.9, 0.95, 0.99, 0.999, 1 - 1e-9, 1.0, 1.5,
]

SPLINES: list[tuple[str, list[list[float]], str]] = [
    ("even", [[0, 0], [1, 1], [2, 4], [3, 9]], "evenly spaced, rising"),
    ("uneven", [[0, 0], [0.2, 100], [3, 110], [3.2, 300]], "unevenly spaced: a Catmull-Rom would overshoot and backtrack"),
    ("plateau", [[0, 0], [1, 5], [2, 5], [3, 10]], "a flat stretch stays flat"),
    ("up-down", [[0, 0], [1, 10], [2, 3], [4, 8]], "turns: tangents are 0 where the data turns"),
    ("single", [[1, 7]], "one key: a constant"),
    ("same-time", [[0, 0], [1, 2], [1, 4], [2, 6]], "two keys at one time: the later wins"),
]

LANDINGS: list[tuple[str, float, float]] = [
    ("firm-out", 10.0, 0.52),
    ("sine", 3.25, 1.2),
    ("quick-commit", 7.0, 0.2),
    ("css-ease-in", 12.5, 0.39),
    ("spring0", 1.0, 0.53),
    ("overshoot", 2.0, 0.8),
]


def _spline_samples(keys: list[list[float]]) -> list[list[float]]:
    ts = [k[0] for k in keys]
    lo, hi = min(ts) - 0.5, max(ts) + 0.5
    n = 40
    return [[lo + (hi - lo) * i / n, mono_spline(lo + (hi - lo) * i / n, keys)] for i in range(n + 1)]


def build() -> tuple[dict, dict]:
    """Both vector files, freshly computed."""
    eases = []
    t90s = []
    specs: dict[str, dict] = {}
    for name, raw, note in CURVES:
        spec = parse(raw)
        assert spec is not None, name
        specs[name] = raw
        entry = {"name": name, "note": note, "spec": spec.to_dict(), "samples": [[u, evaluate(spec, u)] for u in U_SAMPLES], "t90": t90(spec)}
        if spec.kind == "bezier":
            entry["ae"] = ae_keyframe_ease(spec.bezier)  # type: ignore[arg-type]
        eases.append(entry)
        t90s.append({"name": name, "spec": spec.to_dict(), "t50": t_at(spec, 0.5), "t90": t90(spec), "t99": t_at(spec, 0.99)})
    splines = [{"name": name, "note": note, "keys": keys, "samples": _spline_samples(keys)} for name, keys, note in SPLINES]
    landings = []
    for name, land, dur in LANDINGS:
        spec = parse(specs[name])
        assert spec is not None
        landings.append({"ease": name, "spec": spec.to_dict(), "land": land, "dur": dur, "start": start_for(land, dur, spec)})
    fmt = {
        "spec": "kind is bezier (bezier: [x1, y1, x2, y2], CSS cubic-bezier with x1, x2 in [0, 1]), linear, sine "
        "((1 - cos(pi u)) / 2) or spring0 (stiffness a: (1 - (1 + a u) e^(-a u)) / (1 - (1 + a) e^(-a)))",
        "samples": "[u, value]: every ease is exactly 0 at u <= 0 and exactly 1 at u >= 1",
        "t90": "the first u where the ease reaches 0.9 of its travel",
        "ae": "After Effects keyframe ease: influence in percent, speed as a multiple of the segment's average speed",
        "splines": "monotone cubic (Fritsch-Carlson tangents, Butland's weighted mean), tangent 0 at the first and last "
        "key and wherever the data turns; constant before the first key and after the last",
    }
    eases_doc = {"generated": GENERATED, "version": 1, "tolerance": 1e-12, "format": fmt, "eases": eases, "splines": splines}
    t90_doc = {
        "generated": GENERATED,
        "version": 1,
        "tolerance": 1e-9,
        "format": {"t50/t90/t99": "first u where the ease reaches that fraction of its travel", "start_for": "land - t90 * dur"},
        "t90": t90s,
        "start_for": landings,
    }
    return eases_doc, t90_doc


def _dump(obj: dict) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m slate.motion.vectors", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="rewrite tests/vectors/eases.json and t90.json")
    ap.add_argument("--check", action="store_true", help="compare freshly computed vectors with the files")
    args = ap.parse_args(argv)
    eases_doc, t90_doc = build()
    if args.write:
        VECTORS_DIR.mkdir(parents=True, exist_ok=True)
        EASES_FILE.write_text(_dump(eases_doc), encoding="utf-8")
        T90_FILE.write_text(_dump(t90_doc), encoding="utf-8")
        print(f"wrote {EASES_FILE} and {T90_FILE}")
        return 0
    drift = []
    for path, doc in ((EASES_FILE, eases_doc), (T90_FILE, t90_doc)):
        if not path.exists() or path.read_text(encoding="utf-8") != _dump(doc):
            drift.append(str(path))
    if drift:
        print("the motion vectors have drifted from the code: " + ", ".join(drift), file=sys.stderr)
        print("if the maths changed on purpose, rewrite them: python -m slate.motion.vectors --write", file=sys.stderr)
        return 1
    print("the motion vectors match the code")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
