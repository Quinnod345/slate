"""The words on screen in each scene, read by Slate's one OCR engine (Apple Vision through ocrmac, slate.qa.ocr).

Each scene's settled frame is read at the film's own size (read smaller, letter-spaced capitals come back garbled).
When Vision OCR isn't available here the step is UNCHECKED with the fix; it never falls back to another engine.
"""

from __future__ import annotations

from pathlib import Path

from slate.gates import GateResult

MIN_CONFIDENCE = 0.5


def run(scenes: list[dict], root: Path) -> tuple[GateResult, dict]:
    """OCR every scene's settled frame (or its change frame when there's no settled one)."""
    from slate.qa import ocr  # noqa: PLC0415

    ok, engine = ocr.available()
    if not ok:
        hint = engine.get("hint") or ocr.INSTALL_HINT
        return GateResult.unchecked("analyze.ocr", f"no on-screen text: {engine.get('reason', 'Vision OCR is unavailable')}",
                                    evidence=[f"next: {hint}"], engine=engine), {}
    from PIL import Image  # noqa: PLC0415

    read = 0
    lines_total = 0
    for sc in scenes:
        rel = sc.get("settled_frame") or sc.get("change_frame")
        if not rel or not (root / rel).is_file():
            continue
        with Image.open(root / rel) as im:
            rows = ocr.recognize(im.convert("RGB"))
        texts = [str(r.get("text", "")).strip() for r in rows
                 if str(r.get("text", "")).strip() and float(r.get("conf", 1.0)) >= MIN_CONFIDENCE]
        sc["ocr"] = texts
        read += 1
        lines_total += len(texts)
    if not read:
        return GateResult.unchecked("analyze.ocr", "no frames to read: run the cuts step first (it grabs them)",
                                    engine=engine), {}
    return GateResult.pass_("analyze.ocr", f"read {lines_total} lines of text in {read} scenes",
                            engine=engine), {"frames_read": read, "lines": lines_total, "min_confidence": MIN_CONFIDENCE}
