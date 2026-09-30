"""The finish stage's settings: defaults here, overridden in slate.toml's [finish] table or the environment.

    [finish]
    supervised_master = "ae"      # how supervised and express films are finished; "ffmpeg" on a Mac without AE
    psnr_min_db = 40.0            # master against renders, every frame (film.json targets.master_psnr_db wins)
    gate_h264_psnr = true         # hold the H.264 master to the same bar as the ProRes one
    lock_timeout_s = 0            # wait this long for the render slot (0: say busy at once; --detach queues it)

    [finish.h264]                 # the H.264 master
    encoder = "libx264"           # or "h264_videotoolbox"
    crf = 18
    preset = "slow"
    bitrate = "40M"               # VideoToolbox only
    audio_bitrate = "320k"

    [finish.review]
    phone_short_side = 360        # the phone cut's short side in pixels (never upscaled)
    encoder = "auto"              # auto: VideoToolbox when this ffmpeg has it, else libx264
    downloads = "~/Downloads"     # where the owner's own review packets land (SLATE_DOWNLOADS wins)
    sheet_step_s = 0.5
    tile_long_side = 240
    sheet_cols = 12
    sheet_rows = 10
    font = ""                     # a TrueType file for burn-ins and labels; default: a system font
    burnin = "auto"               # auto: ffmpeg drawtext when this build has it, else labels drawn with Pillow
    taste_notes = []              # the owner's taste notes the reviewer reads (paths)
    to = ""                       # default destination: "" = downloads for the owner's films, imessage for Oneiro's

    [finish.deliver]
    thumbnails = 4                # stills from the master (film.json "deliver": {"thumbnails": [...]} wins)

    [finish.labels]
    law = ""                      # a law that requires an AI label, once one applies (none known by default)
    platforms = {}                # the owner's call per platform, e.g. { tiktok = "no label" }

Sizes and frame rates of the film itself never live here: they come from film.json.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

from slate import config

DEFAULTS: dict[str, Any] = {
    "supervised_master": "ae",
    "psnr_min_db": 40.0,
    "gate_h264_psnr": True,
    "lock_timeout_s": 0,
    "h264": {"encoder": "libx264", "crf": 18, "preset": "slow", "bitrate": "40M", "audio_bitrate": "320k"},
    "review": {
        "phone_short_side": 360,
        "phone_crf": 27,
        "phone_maxrate": "700k",
        "phone_audio_bitrate": "96k",
        "half_crf": 23,
        "half_maxrate": "3M",
        "half_audio_bitrate": "192k",
        "encoder": "auto",
        "downloads": "~/Downloads",
        "sheet_step_s": 0.5,
        "tile_long_side": 240,
        "sheet_cols": 12,
        "sheet_rows": 10,
        "font": "",
        "burnin": "auto",
        "taste_notes": [],
        "to": "",
        "beat_window_s": 1.0,
    },
    "deliver": {"thumbnails": 4},
    "labels": {"law": "", "platforms": {}, "default_platforms": ["tiktok", "youtube", "instagram", "facebook"]},
}

# System fonts for burn-ins and sheet labels (review material, never brand type). First one found wins.
FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/SFNS.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load() -> dict:
    """The merged [finish] settings (defaults < slate.toml)."""
    return _merge(DEFAULTS, config.load().get("finish") or {})


def downloads_dir() -> Path:
    """Where the owner's own review packets are copied. SLATE_DOWNLOADS wins (tests point it at a temp folder)."""
    env = os.environ.get("SLATE_DOWNLOADS")
    raw = env or str(load()["review"].get("downloads") or "~/Downloads")
    return Path(raw).expanduser()


def font_path() -> str | None:
    """A TrueType font for labels: the configured one, else the first system font found, else None (Pillow's own)."""
    configured = str(load()["review"].get("font") or "").strip()
    if configured and Path(configured).expanduser().is_file():
        return str(Path(configured).expanduser())
    for cand in FONT_CANDIDATES:
        if Path(cand).is_file():
            return cand
    return None
