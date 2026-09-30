"""Neutral fixtures for the brand tests: an invented app's code, synthetic takes and proofs, tiny test videos.

Nothing here names a real brand. The app fixture is a made-up product ("Kettle", a tea timer) written only to carry
the kinds of tokens a real product has: CSS custom properties for colours, easings and durations, a Tailwind-style
theme, SwiftUI springs and an asset-catalog colour.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

SR = 48000

CSS = """:root {
  --paper: #F6F3EE;
  --ink: #1B1A18;
  --kettle-500: oklch(0.62 0.12 150);
  --accent: var(--kettle-500);
  --page: var(--paper);
  --ease-out-soft: cubic-bezier(0.22, 1, 0.36, 1);
  --ease-in-out-calm: cubic-bezier(0.65, 0, 0.35, 1);
  --ease-snap: cubic-bezier(0.8, 0, 0.2, 1);
  --ease-in-exit: cubic-bezier(0.4, 0, 1, 1);
  --ease-spring: linear(0, 0.3 20%, 0.9 50%, 1.04 70%, 1);
  --dur-quick: 180ms;
  --dur-base: 320ms;
  --dur-slow: 0.6s;
  --stagger: 30ms;
  --font-sans: "Kettle Grotesk", system-ui, sans-serif;
}
.card { transition: transform 320ms cubic-bezier(0.22, 1, 0.36, 1), opacity 180ms ease-out; }
.toast { animation-timing-function: ease-in-out; }
@font-face { font-family: "Kettle Grotesk"; src: url("/fonts/kettle.woff2") format("woff2"); font-weight: 400; }
body { font-family: "Kettle Grotesk", sans-serif; }
"""

THEME_TS = """export const theme = {
  colors: { brand: '#2F6B4F', mist: 'rgb(230 236 232)' },
  easing: { 'enter-soft': 'cubic-bezier(0.2, 0.8, 0.3, 1)' },
  motion: { arriveEase: [0.16, 1, 0.3, 1] },
};
"""

SWIFT = """import SwiftUI

enum Motion {
    static let sheetArrive = Animation.spring(response: 0.42, dampingFraction: 1.0)
    static let chipSnap = Animation.spring(response: 0.3, dampingFraction: 0.7)
    static let fade = Animation.easeOut(duration: 0.25)
}

struct Card: View {
    var body: some View {
        Text("Steeping").font(.custom("KettleGrotesk-Bold", size: 17))
            .foregroundStyle(Color(red: 0.106, green: 0.102, blue: 0.094))
            .animation(.snappy, value: 1)
    }
}
"""

COLORSET = {"colors": [{"idiom": "universal", "color": {"color-space": "srgb", "components": {
    "red": "0x2F", "green": "0x6B", "blue": "0x4F", "alpha": "1.000"}}}], "info": {"version": 1}}


def write_app(root: Path) -> Path:
    """A small product codebase: web tokens, a theme file, SwiftUI views and an asset catalog, plus a dependency
    folder that must be skipped."""
    (root / "web" / "src").mkdir(parents=True)
    (root / "web" / "src" / "tokens.css").write_text(CSS)
    (root / "web" / "src" / "theme.ts").write_text(THEME_TS)
    (root / "web" / "node_modules" / "junk").mkdir(parents=True)
    (root / "web" / "node_modules" / "junk" / "x.css").write_text(":root { --junk-ease: cubic-bezier(0.1, 0.2, 0.3, 0.4); }")
    (root / "ios" / "Views").mkdir(parents=True)
    (root / "ios" / "Views" / "Motion.swift").write_text(SWIFT)
    cs = root / "ios" / "Assets.xcassets" / "Leaf.colorset"
    cs.mkdir(parents=True)
    (cs / "Contents.json").write_text(json.dumps(COLORSET))
    return root


def write_git(root: Path, commit: str = "0123456789abcdef0123456789abcdef01234567", remote: str | None = None) -> None:
    """Just enough of a .git folder to pin a commit (and, optionally, name a remote); no git process involved."""
    g = root / ".git"
    (g / "refs" / "heads").mkdir(parents=True)
    (g / "HEAD").write_text("ref: refs/heads/main\n")
    (g / "refs" / "heads" / "main").write_text(commit + "\n")
    cfg = "[core]\n\tbare = false\n"
    if remote:
        cfg += f'[remote "{remote}"]\n\turl = https://example.invalid/repo.git\n'
    (g / "config").write_text(cfg)


def tone(seconds: float, freq: float = 220.0, level: float = 0.2, channels: int = 2) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    x = level * np.sin(2 * np.pi * freq * t)
    return np.repeat(x[:, None], channels, axis=1).astype(np.float64)


def speechy(seconds: float, level: float = 0.3) -> np.ndarray:
    """A voice stand-in: a tone that comes and goes like phrases."""
    t = np.arange(int(seconds * SR)) / SR
    env = (np.sin(2 * np.pi * 0.7 * t) > -0.2).astype(float)
    x = level * env * np.sin(2 * np.pi * 180 * t) * (1 + 0.3 * np.sin(2 * np.pi * 5 * t))
    return np.repeat(x[:, None], 2, axis=1)


def write_wav(path: Path, x: np.ndarray) -> Path:
    from slate.sound import audio

    path.parent.mkdir(parents=True, exist_ok=True)
    return audio.write_wav(path, x, SR, bits=24)


def write_proof(folder: Path, seconds: float = 12.0) -> tuple[Path, Path]:
    """(proof mix, stems folder with vo.wav, ui.wav, fx.wav): the effects sit well under the voice."""
    vo = speechy(seconds, 0.3)
    ui = tone(seconds, 1200, 0.02)
    fx = tone(seconds, 90, 0.04)
    stems = folder / "proof_stems"
    write_wav(stems / "vo.wav", vo)
    write_wav(stems / "ui.wav", ui)
    write_wav(stems / "fx.wav", fx)
    mix = write_wav(folder / "proof_mix.wav", vo + ui + fx)
    return mix, stems


def make_video(path: Path, seconds: float, *, sound: str = "tone") -> Path:
    """A tiny test clip (64×36 at 10 fps): sound is "tone", "silent" or "none"."""
    from slate import procs

    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s=64x36:r=10:d={seconds}"]
    if sound == "tone":
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}"]
    elif sound == "silent":
        args += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={seconds}"]
    args += ["-c:v", "mpeg4", "-q:v", "8"]
    if sound != "none":
        args += ["-c:a", "pcm_s16le", "-shortest"]
    path.parent.mkdir(parents=True, exist_ok=True)
    procs.run(args + [str(path)], check=True, timeout=60)
    return path


def make_png(path: Path, rgb=(200, 190, 180)) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 36), rgb).save(path)
    return path


def minimal_pdf(path: Path, lines: list[str]) -> Path:
    """A one-page PDF with real text (Helvetica), written by hand, so PDF readers have something to read."""
    content = "BT /F1 12 Tf 40 760 Td 16 TL " + " ".join(f"({ln.replace('(', '').replace(')', '')}) '" for ln in lines) \
        + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out.encode("latin-1")))
        out += f"{i} 0 obj\n{body}\nendobj\n"
    xref = len(out.encode("latin-1"))
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    path.write_bytes(out.encode("latin-1"))
    return path


def _have_ffmpeg() -> bool:
    from slate import procs

    return bool(procs.which("ffmpeg") and procs.which("ffprobe"))


needs_ffmpeg = pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg and ffprobe are needed to make and probe test media")
