"""The one ElevenLabs client Slate uses.

Every call is replay-aware and every paid call is ledger-wrapped:

- **Replay** (`SLATE_FAKE_VENDORS=1`, the default for tests, evals and `slate demo`): answers come from recorded
  fixtures, or from deterministic fakes registered here: a harmonic "voice" whose length follows the text (one burst
  per word, so alignment and fit checks still mean something), three previews per design call, a soft pad for music.
  Nothing leaves the machine and nothing is spent; every result says `"replay": true`.
- **Live** (`--live`): the estimate comes first, then the budget check, then an in-flight ledger row that carries the
  request's stable key before Slate waits on ElevenLabs. The vendor's own request id and billed characters are added
  as soon as the response arrives. Every paid call goes through `slate.jobs.resume.paid_call`, so a row whose process
  died is found by its key and closed as orphaned (counted at its estimate until `slate ledger reconcile` settles it)
  rather than left in flight or lost.
- **Never pay twice for an answer already received.** ElevenLabs answers in the same request it bills, so the answer
  is parked (`DATA/cache/paid/elevenlabs/<key>`) before the ledger row closes, and unparked once the output file is
  written. A crash in between leaves it parked, and the rerun of the same request uses it instead of paying again.

Keys come only from `slate.secrets` (never argv, never a URL, never a log line). Autonomous films use Oneiro's own
capped key (`elevenlabs_oneiro`), which is also its own spend pool in the ledger. A film's mode is the one Slate judges
it in (`slate.ledger.film_mode`): a film made autonomous spends through that key whatever mode the caller passes and
whatever its film.json says now.

Inside the OpenClaw gateway, whose egress proxy doesn't let api.elevenlabs.io through, no live call is tried: the
command queues itself for Slate's launchd job runner (outside the gateway) or refuses with the next step
(`slate.vendors.egress`). A proxy's 403 at the tunnel anywhere becomes a MissingPrerequisite naming the owner's egress
allowlist and the queue route, never a mystery network error.

Models are always named explicitly: Voice Design defaults to an older model on the API side (`eleven_ttv_v3` is sent
every time) and so does music (`music_v2_5`). Lossless voice (PCM 48 kHz) needs a Pro plan or higher, so the client
reads the plan's tier and falls back to the best format the plan allows, recording which it used and why.

There is deliberately no method that creates a voice clone. The only clone Slate ever uses is the owner's own
professional clone, which the owner makes and verifies in ElevenLabs themselves.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import math
import re
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Callable

import numpy as np

from slate import config, http, jsonio, ledger, log, paths, procs, replay, secrets
from slate import errors as _errors
from slate.errors import Busy, MissingPrerequisite, SlateError, UsageError
from slate.vendors import egress

TryLater = getattr(_errors, "TryLater", Busy)  # exit 75 on a vendor's "not now" (the job queue retries it)
from slate.voice import audio as audio_mod

PROVIDER = "elevenlabs"
BASE_URL = "https://api.elevenlabs.io"

# Plans in order. Lossless PCM/WAV at 44.1 kHz and above needs Pro; 192 kbps mp3 needs Creator.
TIERS = ("free", "starter", "creator", "pro", "scale", "business", "enterprise")
FORMAT_MIN_TIER = {"pcm_48000": "pro", "wav_48000": "pro", "pcm_44100": "pro", "wav_44100": "pro",
                   "mp3_48000_192": "creator", "mp3_44100_192": "creator", "mp3_44100_128": "free"}
VOICE_FORMATS = ("pcm_48000", "pcm_44100", "mp3_44100_192", "mp3_44100_128")
MUSIC_FORMATS = ("pcm_48000", "mp3_48000_192", "mp3_44100_192", "mp3_44100_128")
FORMAT_REJECTED = (400, 403, 422)  # what ElevenLabs answers when a plan can't have a format (nothing is billed)
# The channels each operation answers in. The pcm_* formats are bare samples with no header, so unless the answer says
# otherwise (a WAV header, or a channels= parameter on its content type) a take is written with its format's count:
# Eleven Music composes in stereo; speech, Voice Design and Remix previews and the isolated voice are mono. Labelling
# every answer one channel wrote Eleven Music's 48 kHz stereo as mono at twice the length, an octave low.
FORMAT_CHANNELS = {"music": 2, "tts": 1, "design": 1, "remix": 1, "isolate": 1}
STITCH_MAX = 3  # previous_request_ids / next_request_ids: the API takes at most three on each side

SUBSCRIPTION_FIELDS = ("tier", "status", "character_count", "character_limit", "next_character_count_reset_unix",
                       "voice_slots_used", "voice_limit", "professional_voice_slots_used", "professional_voice_limit",
                       "can_use_instant_voice_cloning", "can_use_professional_voice_cloning")

DESIGN_LIMITS = {"voice_description": (20, 1000), "text": (100, 1000), "guidance_scale": (0.0, 100.0),
                 "loudness": (-1.0, 1.0)}
MUSIC_MIN_SECTION_MS = 3000
MUSIC_MAX_SECTION_MS = 120000  # a section (v1) or chunk (v2 and later) runs 3-120 s
MUSIC_MAX_NAME = 100
MUSIC_MAX_LINES, MUSIC_MAX_LINE_CHARS = 30, 200
DEFAULT_MUSIC_SECONDS = 30.0  # what a prompt without a length is estimated at
PLAN_SCHEMAS = ("sections", "chunks")

# Prices checked against ElevenLabs after registry.json was written (the registry check in
# knowledge/registry_proposal.json). The client uses a check while the registry still carries an older figure for the
# same model and isn't newer than the check; once registry.json adopts the price (or is re-verified later), the
# registry is used again, so there is only ever one source of a price at a time.
PRICE_CHECKS: dict[str, dict] = {
    "elevenlabs_forced_alignment": {
        "unit": "credits", "per_minute": 330, "verified_on": "2026-09-30",
        "source": "ElevenLabs docs: Forced Alignment bills at the Speech to Text rate, and the pricing FAQ puts Speech "
                  "to Text at 330 credits a minute (registry check, knowledge/registry_proposal.json)"},
}


def checked_price(model: str) -> dict | None:
    """The price check for a model when it should be used instead of registry.json's figure (else None)."""
    check = PRICE_CHECKS.get(model)
    if not check:
        return None
    info = config.model_info(model) or {}
    price = info.get("price") or {}
    if price.get("per_minute") == check["per_minute"] and price.get("unit", check["unit"]) == check["unit"]:
        return None  # the registry already agrees
    if str(info.get("verified_on") or "") > check["verified_on"]:
        return None  # the registry was re-verified after the check
    return {**check, "registry_per_minute": price.get("per_minute")}


def music_plan_schema(model_id: str) -> str:
    """Which composition plan a music model takes: "sections" (music_v1: global styles plus sections) or "chunks"
    (music_v2, music_v2_5 and later: a list of chunks, each with its own text and styles). A v1 section plan sent
    to a v2 model is rejected, so plans are built for the model they're sent to. registry.json can say it outright
    (limits.plan_schema); otherwise the model id does."""
    stated = (config.model_info(model_id).get("limits") or {}).get("plan_schema")
    if stated in PLAN_SCHEMAS:
        return stated
    m = re.match(r"^music_v(\d+)", model_id or "")
    return "sections" if m and int(m.group(1)) < 2 else "chunks"


def plan_shape(plan: dict) -> str | None:
    """"sections", "chunks", or None when a composition plan is neither (or claims both)."""
    has_sections, has_chunks = "sections" in (plan or {}), "chunks" in (plan or {})
    if has_sections == has_chunks:
        return None
    return "sections" if has_sections else "chunks"


def plan_problems(plan: dict, model_id: str) -> list[str]:
    """Why Eleven Music would reject this composition plan for this model (empty when it wouldn't)."""
    want, shape = music_plan_schema(model_id), plan_shape(plan)
    if shape is None:
        return ["a composition plan has either sections (music_v1) or chunks (music_v2 and later), and this one has "
                + ("both" if plan and "sections" in plan else "neither")]
    if shape != want:
        return [f"{model_id} takes a plan made of {want}, and this one has {shape}"
                + (" (a music_v1 section plan is rejected by the v2 models)" if want == "chunks" else "")]
    blocks = plan[shape]
    if not blocks:
        return [f"the plan has no {shape}"]
    problems = []
    for i, b in enumerate(blocks):
        label = (b.get("section_name") if shape == "sections" else b.get("text")) or f"#{i + 1}"
        ms = b.get("duration_ms")
        if not isinstance(ms, int) or isinstance(ms, bool) or not MUSIC_MIN_SECTION_MS <= ms <= MUSIC_MAX_SECTION_MS:
            problems.append(f"{label}: duration_ms must be a whole number from {MUSIC_MIN_SECTION_MS} to "
                            f"{MUSIC_MAX_SECTION_MS} (got {ms!r})")
        if shape == "sections":
            name = b.get("section_name") or ""
            if not 1 <= len(name) <= MUSIC_MAX_NAME:
                problems.append(f"section {i + 1}: section_name must be 1-{MUSIC_MAX_NAME} characters")
        else:
            text = b.get("text")
            if not isinstance(text, str) or not text.strip():
                problems.append(f"chunk {i + 1}: text is required (at least its [section name])")
            else:
                lines = text.splitlines()
                if len(lines) > MUSIC_MAX_LINES or any(len(line) > MUSIC_MAX_LINE_CHARS for line in lines):
                    problems.append(f"chunk {i + 1}: text is at most {MUSIC_MAX_LINES} lines of "
                                    f"{MUSIC_MAX_LINE_CHARS} characters")
            if not isinstance(b.get("positive_styles"), list):
                problems.append(f"chunk {i + 1}: positive_styles is required (a list)")
    return problems


# ---------------------------------------------------------------------------------------------------------------
# Plans and formats


def tier_rank(tier: str | None) -> int | None:
    """Where a plan's tier sits in TIERS (None when ElevenLabs reports a name Slate doesn't know)."""
    tokens = re.split(r"[^a-z]+", (tier or "").lower())
    for rank in range(len(TIERS) - 1, -1, -1):
        if TIERS[rank] in tokens:
            return rank
    return None


def format_allowed(fmt: str, tier: str | None) -> bool | None:
    rank = tier_rank(tier)
    need = FORMAT_MIN_TIER.get(fmt)
    if rank is None or need is None:
        return None
    return rank >= TIERS.index(need)


def format_chain(want: str, family: tuple[str, ...] = VOICE_FORMATS) -> list[str]:
    """The wanted format, then the next best ones in order."""
    if want in family:
        return list(family[family.index(want):])
    return [want, *family]


def best_format(tier: str | None, want: str = "pcm_48000", family: tuple[str, ...] = VOICE_FORMATS) -> tuple[str, str]:
    """(format, reason): the best format at or below `want` that the plan allows."""
    rank = tier_rank(tier)
    if rank is None:
        return want, f"plan tier {tier!r} unknown to Slate; asking for {want} and falling back if refused"
    for fmt in format_chain(want, family):
        if format_allowed(fmt, tier) is not False:
            if fmt == want:
                return fmt, f"{tier} plan allows {fmt}"
            return fmt, f"{tier} plan can't have {want} (lossless needs Pro or higher); using {fmt}"
    return family[-1], f"{tier} plan: falling back to {family[-1]}"


def parse_format(fmt: str) -> tuple[str, int]:
    """("pcm", 48000) from "pcm_48000"; ("mp3", 44100) from "mp3_44100_192"."""
    parts = fmt.split("_")
    try:
        return parts[0], int(parts[1])
    except (IndexError, ValueError) as exc:
        raise UsageError(f"Unknown ElevenLabs output format {fmt!r}.") from exc


def is_lossless(fmt: str) -> bool:
    return parse_format(fmt)[0] in ("pcm", "wav")


def wav_channels(data: bytes) -> int | None:
    """The channel count in a WAV header (None when the bytes aren't a WAV, or its fmt chunk isn't there)."""
    if len(data) < 12 or data[:4] not in (b"RIFF", b"RF64") or data[8:12] != b"WAVE":
        return None
    pos = 12
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], int.from_bytes(data[pos + 4:pos + 8], "little")
        if cid == b"fmt " and pos + 12 <= len(data):
            return int.from_bytes(data[pos + 10:pos + 12], "little") or None
        pos += 8 + size + (size & 1)
    return None


def answer_channels(op: str, headers: dict | None = None, data: bytes | None = None) -> tuple[int, str]:
    """(channels, where from) for an audio answer: the answer's own word first (its WAV header, or a `channels=`
    parameter on its content type), else the count of the format the operation answers in (FORMAT_CHANNELS)."""
    n = wav_channels(data or b"")
    if n:
        return n, "answer"
    m = re.search(r"channels\s*=\s*(\d+)", str(_lower(headers).get("content-type", "")), re.I)
    if m and int(m.group(1)) > 0:
        return int(m.group(1)), "answer"
    return FORMAT_CHANNELS.get(op, 1), "format"


def _replayed_channels(op: str, response: dict, headers: dict, data: bytes) -> int:
    """A replayed answer's channels. A recording made since channels were kept says where its count came from; one
    made before said one channel for every answer, so its count is read again the way a live answer's is (an old
    Eleven Music recording replays as the stereo it was)."""
    if response.get("channels_from") and int(response.get("channels") or 0) > 0:
        return int(response["channels"])
    return answer_channels(op, headers, data)[0]


# ---------------------------------------------------------------------------------------------------------------
# Deterministic stand-ins for replay mode


def _seed_of(*parts: Any) -> int:
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:8], 16)


def _spoken_words(text: str) -> list[str]:
    plain = re.sub(r"\[[^\[\]]*\]", " ", text or "")
    return plain.split()


def fake_speech(text: str, voice_key: str, sr: int = 48000) -> tuple[np.ndarray, list[dict]]:
    """A speech-like stand-in: one harmonic burst per word (length follows its letters), pauses after punctuation.

    Returns (samples, words) where words carry exact start/end times, so replayed takes can be aligned, fitted and
    captioned offline. The pitch follows the voice, so different voices in a shootout measure differently.
    """
    words = _spoken_words(text)
    f0 = 105.0 + (_seed_of(voice_key) % 160)
    lead, tail = 0.12, 0.22
    pieces, timed, t = [np.zeros(int(lead * sr))], [], lead
    for i, w in enumerate(words):
        letters = max(1, sum(ch.isalnum() for ch in w))
        dur = 0.09 + 0.055 * letters
        n = int(round(dur * sr))
        tt = np.arange(n) / sr
        wobble = 1.0 + 0.035 * np.sin(2 * math.pi * (1.3 + (_seed_of(w, i) % 7) / 10.0) * tt)
        phase = 2 * math.pi * np.cumsum(f0 * wobble) / sr
        env = np.sin(math.pi * np.clip(tt / dur, 0, 1)) ** 0.6
        burst = (0.30 * np.sin(phase) + 0.12 * np.sin(2 * phase) + 0.06 * np.sin(3 * phase)) * env
        pieces.append(burst)
        timed.append({"word": w, "start": round(t, 4), "end": round(t + n / sr, 4)})
        t += n / sr
        if i + 1 < len(words):
            last = w.rstrip("\"'”’)")[-1:]
            gap = 0.32 if last in ".!?…。！？" else (0.16 if last in ",;:、，" else 0.07)
            pieces.append(np.zeros(int(round(gap * sr))))
            t += int(round(gap * sr)) / sr
    pieces.append(np.zeros(int(tail * sr)))
    return np.concatenate(pieces), timed


def fake_music(seconds: float, key: str, sr: int = 48000) -> np.ndarray:
    """A soft two-channel pad of the requested length (for replay scores)."""
    n = max(1, int(round(seconds * sr)))
    t = np.arange(n) / sr
    root = 110.0 * 2 ** ((_seed_of(key) % 12) / 12.0)
    left = sum(a * np.sin(2 * math.pi * root * r * t) for a, r in ((0.05, 1.0), (0.035, 1.25), (0.03, 1.5)))
    right = sum(a * np.sin(2 * math.pi * root * r * 1.002 * t) for a, r in ((0.05, 1.0), (0.035, 1.25), (0.03, 1.5)))
    fade = np.minimum(1.0, np.minimum(t / min(2.0, seconds / 3), (seconds - t) / min(3.0, seconds / 3)))
    fade = np.clip(fade, 0.0, 1.0)
    return np.stack([left * fade, right * fade], axis=1)


def _pcm16(x: np.ndarray) -> bytes:
    return np.round(np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _fake_headers(key: str, chars: int | None = None) -> dict:
    out = {"request-id": f"replay_{key}"}
    if chars is not None:
        out["character-cost"] = str(chars)
    return out


def _fake_subscription(key: str, request: dict) -> dict:
    return {"headers": _fake_headers(key), "json": {
        "tier": "pro", "status": "active", "character_count": 0, "character_limit": 1_000_000,
        "next_character_count_reset_unix": 0, "voice_slots_used": 0, "voice_limit": 160,
        "professional_voice_slots_used": 0, "professional_voice_limit": 1,
        "can_use_instant_voice_cloning": True, "can_use_professional_voice_cloning": True}}


def _fake_voice(key: str, request: dict) -> dict:
    vid = request.get("voice_id")
    return {"headers": _fake_headers(key), "json": {"voice_id": vid, "name": f"Replay voice {vid}",
                                                     "category": "premade", "labels": {}}}


def _fake_tts(key: str, request: dict) -> dict:
    body = request.get("body") or {}
    _, sr = parse_format(request.get("format") or "pcm_48000")
    x, words = fake_speech(body.get("text", ""), f"{request.get('voice_id')}|{body.get('seed')}", sr=sr)
    return {"headers": _fake_headers(key, len(body.get("text", ""))), "encoding": "pcm_s16le", "sample_rate": sr,
            "channels": 1, "audio_b64": _b64(_pcm16(x)), "words": words}


def _fake_previews(key: str, request: dict, prefix: str) -> dict:
    body = request.get("body") or {}
    _, sr = parse_format(request.get("format") or "pcm_48000")
    previews = []
    for i in range(3):
        voice_key = f"{prefix}|{request.get('voice_id')}|{body.get('voice_description')}|{body.get('seed')}|" \
                    f"{body.get('guidance_scale')}|{body.get('prompt_strength')}|{i}"
        x, words = fake_speech(body.get("text", ""), voice_key, sr=sr)
        previews.append({"audio_base_64": _b64(_pcm16(x)), "media_type": "audio/pcm", "words": words,
                         "generated_voice_id": f"replay_gv_{hashlib.sha256(voice_key.encode()).hexdigest()[:16]}",
                         "duration_secs": round(len(x) / sr, 3), "language": "en"})
    return {"headers": _fake_headers(key, len(body.get("text", ""))), "json": {"previews": previews,
                                                                              "text": body.get("text", "")}}


def _fake_save_voice(key: str, request: dict) -> dict:
    body = request.get("body") or {}
    return {"headers": _fake_headers(key), "json": {
        "voice_id": f"replay_voice_{hashlib.sha256(str(body.get('generated_voice_id')).encode()).hexdigest()[:16]}",
        "name": body.get("voice_name")}}


def _fake_isolate(key: str, request: dict) -> dict:
    path = Path(request.get("audio_path", ""))
    data = path.read_bytes() if path.is_file() else audio_mod.wav_bytes(np.zeros(4800), 48000)
    if path.suffix.lower() != ".wav":
        data = audio_mod.wav_bytes(np.zeros(4800), 48000)
    return {"headers": _fake_headers(key), "encoding": "wav", "audio_b64": _b64(data)}


def _bursts(x: np.ndarray, sr: int) -> list[tuple[float, float]]:
    hop = max(1, sr // 200)
    n = len(x) // hop
    if n == 0:
        return []
    env = np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1))
    on = env > max(1e-4, float(env.max()) * 0.02)
    out, i = [], 0
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            out.append((i * hop / sr, j * hop / sr))
            i = j
        else:
            i += 1
    return out


def _fake_align(key: str, request: dict) -> dict:
    text = request.get("text", "")
    words = text.split()
    path = Path(request.get("audio_path", ""))
    spans: list[tuple[float, float]] = []
    if path.is_file() and path.suffix.lower() == ".wav" and words:
        x, sr = audio_mod.load(path)
        spans = _bursts(x, sr)
        if len(spans) != len(words) and spans:
            start, end = spans[0][0], spans[-1][1]
            weights = np.array([max(1, len(w)) for w in words], dtype=float)
            edges = start + (end - start) * np.concatenate([[0.0], np.cumsum(weights) / weights.sum()])
            spans = [(float(edges[i]), float(edges[i + 1])) for i in range(len(words))]
    if not spans:
        spans = [(i * 0.4, i * 0.4 + 0.35) for i in range(len(words))]
    out_words, chars = [], []
    for w, (a, b) in zip(words, spans):
        out_words.append({"text": w, "start": round(a, 3), "end": round(b, 3), "loss": 0.0})
        step = (b - a) / max(1, len(w))
        chars += [{"text": ch, "start": round(a + k * step, 3), "end": round(a + (k + 1) * step, 3)}
                  for k, ch in enumerate(w)]
    return {"headers": _fake_headers(key), "json": {"words": out_words, "characters": chars, "loss": 0.0}}


def _fake_music(key: str, request: dict) -> dict:
    body = request.get("body") or {}
    seconds = float(request.get("seconds") or 10.0)
    _, sr = parse_format(request.get("format") or "pcm_48000")
    x = fake_music(seconds, json.dumps(body, sort_keys=True), sr=sr)
    return {"headers": _fake_headers(key), "encoding": "pcm_s16le", "sample_rate": sr, "channels": 2,
            "audio_b64": _b64(_pcm16(x))}


# The replay stand-ins, by operation. Tests re-install these before each test, so a stub another test left in the
# shared registry can't change what a replayed call answers.
FAKERS: dict[str, Callable[[str, dict], dict]] = {
    "subscription": _fake_subscription, "voice": _fake_voice, "tts": _fake_tts,
    "design": lambda k, r: _fake_previews(k, r, "design"), "remix": lambda k, r: _fake_previews(k, r, "remix"),
    "save_voice": _fake_save_voice, "isolate": _fake_isolate, "align": _fake_align, "music": _fake_music,
}
for _op, _faker in FAKERS.items():
    replay.register_faker(PROVIDER, _op, _faker)


# ---------------------------------------------------------------------------------------------------------------
# The client


def _lower(headers: dict | None) -> dict:
    return {str(k).lower(): v for k, v in (headers or {}).items()}


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _vendor_refusal(exc: http.HttpError, provider: str) -> Exception | None:
    """ElevenLabs answers that mean "not now" or "no credits left", as Slate's own errors (None for anything else).

    - quota_exceeded (401) or 402: the balance has run out. Slate never buys credits, so the work stops and asks
      (exit 4).
    - 429 (too many requests at once, or the system is busy) or 503 (briefly unavailable): nothing was billed;
      TryLater, exit 75, which the job queue puts back in line and retries after a backoff instead of failing."""
    body = (exc.body or "").lower()
    if exc.status == 402 or (exc.status == 401 and "quota" in body):
        return ledger.out_of_balance(provider, f"HTTP {exc.status}: {exc.body[:200]}")
    if exc.status in (429, 503):
        what = "too many at once, or the service is busy" if exc.status == 429 else "the service is briefly unavailable"
        message = (f"ElevenLabs is turning requests away right now (HTTP {exc.status}: {what}). Nothing was billed.")
        details = {"status": exc.status, "detail": (exc.body or "")[:300]}
        if TryLater is Busy:  # a foundation without TryLater: the queue's older retry code
            return Busy(message, hint="Run it again in a minute; a queued job retries by itself.", details=details)
        return TryLater(message, hint="Run it again in a minute; a queued job goes back in the queue and retries by "
                                      "itself.", details=details, retry_after_s=getattr(exc, "retry_after_s", None))
    return None


def _check_range(name: str, value: float | int | None, lo: float, hi: float) -> None:
    if value is not None and not lo <= value <= hi:
        raise UsageError(f"{name} must be between {lo:g} and {hi:g} (got {value}).")


class Client:
    """ElevenLabs for one film (or none), in one mode.

    `mode` decides the key and the spend pool: autonomous films use Oneiro's capped key. `film` (a film folder or
    film.json dict) scopes budget checks and ledger rows. `approval_id` is a person's yes for spend that needs one;
    `rework=True` allows the 1.5x rework allowance on the film's budget.
    """

    def __init__(self, *, mode: str = "supervised", film: Any = None, approval_id: str | None = None,
                 rework: bool = False, key: str | None = None, base_url: str | None = None):
        if mode not in ledger.MODES:
            raise UsageError(f"Unknown mode {mode!r}; one of {', '.join(ledger.MODES)}.")
        if film is not None and hasattr(ledger, "film_mode"):
            # A film made autonomous spends as autonomous (its own capped key, its caps), whatever mode the caller
            # read from film.json: Slate's own record of the film's mode wins. (A film that can't be read is the
            # ledger's to refuse when it's spent against, as before.)
            try:
                mode = ledger.film_mode(film, mode)
            except UsageError:
                pass
        self.mode = mode
        # The ledger's budget file says which key each mode spends through (autonomous: Oneiro's capped key).
        required = (ledger.secret_name(PROVIDER, mode) if hasattr(ledger, "secret_name")
                    else ("elevenlabs_oneiro" if mode == "autonomous" else PROVIDER))
        self.key_name = key or required
        if self.key_name not in ("elevenlabs", "elevenlabs_oneiro"):
            raise UsageError(f"{self.key_name!r} isn't an ElevenLabs key name.")
        if required != PROVIDER and self.key_name != required:
            raise UsageError(f"{mode.capitalize()} films spend through their own capped ElevenLabs key ({required}).")
        self.provider = self.key_name  # Oneiro's key is its own spend pool in the ledger
        self.film = film
        self.approval_id = approval_id
        self.rework = rework
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self._subscription: dict | None = None

    # -- plumbing -------------------------------------------------------------------------------------------------

    @property
    def replay(self) -> bool:
        return replay.fake_vendors()

    def _url(self, path: str, query: dict | None = None) -> str:
        q = urllib.parse.urlencode({k: v for k, v in (query or {}).items() if v is not None})
        return f"{self.base_url}{path}" + (f"?{q}" if q else "")

    def _auth(self, accept: str = "application/json") -> dict:
        return {"xi-api-key": secrets.get(self.key_name), "Accept": accept}

    def _live(self, method: str, path: str, *, query: dict | None = None, json_body: Any = None,
              fields: dict | None = None, files: dict | None = None, accept: str = "application/json",
              timeout: float = 300) -> tuple[dict, bytes]:
        egress.route(PROVIDER, host=self._host(), op=path)  # inside the OpenClaw gateway: queue or refuse, never try
        headers = self._auth(accept)
        data = None
        if files is not None:
            data, ctype = http.multipart(fields or {}, files)
            headers["Content-Type"] = ctype
        try:
            _status, resp_headers, body = http.request(method, self._url(path, query), headers=headers,
                                                       json_body=json_body, data=data, timeout=timeout)
        except http.HttpError as exc:
            raise _vendor_refusal(exc, self.provider) or exc
        except SlateError as exc:  # never reached the vendor: a proxy's 403 at the tunnel says why, and what to do
            blocked = egress.from_error(PROVIDER, self._host(), exc, op=path)
            if blocked is not None:
                raise blocked from None
            raise
        return _lower(resp_headers), body

    def _host(self) -> str:
        return urllib.parse.urlparse(self.base_url).netloc or egress.HOSTS[PROVIDER]

    def _replay(self, op: str, request: dict) -> tuple[dict, dict]:
        response = replay.respond(PROVIDER, op, request)
        return _lower(response.get("headers")), response

    def _record(self, op: str, request: dict, headers: dict, response: dict, payload: bytes | None) -> None:
        if not replay.recording():
            return
        keep = {k: v for k, v in headers.items() if k in ("request-id", "character-cost", "content-type")}
        replay.record(PROVIDER, replay.request_key(PROVIDER, op, request), {"headers": keep, **response},
                      {"audio": payload} if payload is not None else None)

    def _estimate(self, op: str, model: str, *, chars: int | None = None, seconds: float | None = None
                  ) -> ledger.Estimate:
        try:
            return ledger.estimate(self.provider, op, model=model, chars=chars, seconds=seconds)
        except UsageError:
            amount = chars if chars is not None else math.ceil((seconds or 0) / 60.0 * 1000)
            return ledger.estimate(self.provider, op, amount=amount, model=model,
                                   basis=f"no registry price for {model}; assumed the upper bound "
                                         + ("(1 credit per character)" if chars is not None else "(1,000 per minute)"))

    def _spend(self, op: str, est: ledger.Estimate, call: Callable[[], tuple[dict, Any]], note: str | None = None,
               request: dict | None = None) -> dict:
        """One paid call through `slate.jobs.resume.paid_call`: budget check and an in-flight row carrying the
        request's stable key first, then the call, then the vendor's request id and billed cost. The answer is parked
        before the row closes (live only), so a crash before the output is written never pays twice."""
        client_rid = f"slate_{uuid.uuid4().hex[:16]}"
        key = replay.request_key(PROVIDER, op, request) if request is not None else f"{op}-{uuid.uuid4().hex[:16]}"
        parked = self._parked(key, est)
        if parked is not None:
            return parked
        if not self.replay:
            egress.route(PROVIDER, host=self._host(), op=op)  # before the ledger row: nothing is begun that can't go

        def submit() -> tuple[dict, Any]:
            headers, payload = call()
            self._park(key, headers, payload)
            return headers, payload

        def vendor_id(result: tuple[dict, Any]) -> str | None:
            h = result[0]
            rid = h.get("request-id") or h.get("x-request-id") or h.get("history-item-id")
            return str(rid) if rid else None

        def cost_of(result: tuple[dict, Any]) -> float | None:
            return _number(result[0].get("character-cost") or result[0].get("x-character-count"))

        def summary(result: tuple[dict, Any]) -> dict:
            return {"request_id": vendor_id(result), "client_request_id": client_rid, "replay": self.replay}

        kw = dict(film=self.film, mode=self.mode, approval_id=self.approval_id, rework=self.rework, note=note or op)
        resume = _resume()
        if resume is not None and hasattr(resume, "paid_call"):
            headers, payload = resume.paid_call(est, key=key, submit=submit, wait=None, cost=cost_of,
                                                summarize=summary, request_id_of=vendor_id, **kw)
            row_id = next((r["id"] for r in reversed(ledger.rows(key=key))), None)
        else:  # the job queue isn't installed: the same dance, straight through the ledger
            with ledger.spend(est, key=key, **kw) as row:
                headers, payload = submit()
                if vendor_id((headers, payload)):
                    row.request(vendor_id((headers, payload)))
                row.actual(cost_of((headers, payload)), **summary((headers, payload)))
            row_id = row.id
        vendor_rid = vendor_id((headers, payload))
        return {"headers": headers, "payload": payload, "request_id": str(vendor_rid or client_rid),
                "client_request_id": client_rid, "ledger_row": row_id, "character_cost": cost_of((headers, payload)),
                "estimate": est.to_dict(), "key": key}

    # -- parked answers (live only): paid for, not yet written ---------------------------------------------------

    @staticmethod
    def _park_dir() -> Path:
        return paths.cache_dir() / "paid" / PROVIDER

    def _park(self, key: str, headers: dict, payload: Any) -> None:
        """Keep a paid answer until its output is written (live only: replay spends nothing)."""
        if self.replay:
            return
        folder = self._park_dir()
        folder.mkdir(parents=True, exist_ok=True)
        meta: dict[str, Any] = {"key": key, "headers": headers,
                                "parked_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")}
        if isinstance(payload, dict) and isinstance(payload.get("data"), (bytes, bytearray)):
            jsonio.write_text_atomic(folder / f"{key}.bin", bytes(payload["data"]))
            meta["payload"] = {k: v for k, v in payload.items() if k != "data"}
            meta["data_file"] = f"{key}.bin"
        else:
            meta["payload"] = payload
        jsonio.write_atomic(folder / f"{key}.json", meta)

    def _parked(self, key: str, est: ledger.Estimate) -> dict | None:
        """A paid answer a crashed run left parked for this exact request: used instead of paying again."""
        if self.replay:
            return None
        meta_path = self._park_dir() / f"{key}.json"
        if not meta_path.is_file():
            return None
        try:
            meta = jsonio.read(meta_path)
            payload = meta.get("payload")
            if meta.get("data_file"):
                payload = {**(payload or {}), "data": (self._park_dir() / meta["data_file"]).read_bytes()}
            if isinstance(payload, dict) and payload.get("encoding") == "pcm_s16le" \
                    and not payload.get("channels_checked"):  # parked before channels were kept: said 1 for all
                payload["channels"] = answer_channels(key.split("-", 1)[0], meta.get("headers"),
                                                      payload.get("data"))[0]
        except (OSError, ValueError) as exc:
            log.warn(f"a parked ElevenLabs answer for {key} couldn't be read ({exc}); asking again")
            return None
        headers = _lower(meta.get("headers"))
        cost = _number(headers.get("character-cost") or headers.get("x-character-count"))
        rid = headers.get("request-id") or headers.get("x-request-id") or headers.get("history-item-id")
        resume = _resume()
        for row in (resume.pending(key=key, provider=self.provider) if resume is not None else []):
            ledger.finish(row["id"], status="done", actual=cost, result={"request_id": rid, "recovered":
                                                                         "the answer was parked before the crash"})
        log.info(f"ElevenLabs already answered this request (paid for {meta.get('parked_at')}); using that answer "
                 "instead of paying again")
        row_id = next((r["id"] for r in reversed(ledger.rows(key=key))), None)
        return {"headers": headers, "payload": payload, "request_id": str(rid or key), "client_request_id": None,
                "ledger_row": row_id, "character_cost": cost, "estimate": est.to_dict(), "key": key, "reused": True}

    def _unpark(self, res: dict) -> None:
        """The output is written: the parked answer has done its job."""
        key = res.get("key")
        if not key or self.replay:
            return
        for suffix in (".bin", ".json"):
            (self._park_dir() / f"{key}{suffix}").unlink(missing_ok=True)

    def _free(self, op: str, request: dict, path: str, *, method: str = "GET", json_body: Any = None,
              query: dict | None = None) -> dict:
        if self.replay:
            _h, response = self._replay(op, request)
            return dict(response.get("json") or {}, replay=True)
        headers, body = self._live(method, path, query=query, json_body=json_body)
        data = json.loads(body or b"{}")
        self._record(op, request, headers, {"json": data}, None)
        return data

    def _audio_result(self, op: str, request: dict, fmt: str, *, path: str, query: dict, json_body: Any = None,
                      fields: dict | None = None, files: dict | None = None, accept: str = "*/*",
                      timeout: float = 300) -> tuple[dict, bytes, str, int, int, list | None]:
        """Run one audio call (replay or live). Returns (headers, data, encoding, rate, channels, fake words).

        The channels are the answer's (answer_channels): what it says about itself, else its format's count, never
        an assumed one. Live answers are recorded with the count and where it came from, so a replay writes the same
        take the live call wrote."""
        if self.replay:
            headers, response = self._replay(op, request)
            data = base64.b64decode(response["audio_b64"]) if response.get("audio_b64") else \
                replay.load_payload(PROVIDER, response.get("replay_key", ""), "audio") or b""
            rate = response.get("sample_rate") or (parse_format(fmt)[1] if fmt else 0)
            return (headers, data, response.get("encoding", "pcm_s16le"), int(rate),
                    _replayed_channels(op, response, headers, data), response.get("words"))
        headers, data = self._live("POST", path, query=query, json_body=json_body, fields=fields, files=files,
                                   accept=accept, timeout=timeout)
        kind, rate = parse_format(fmt) if fmt else ("wav", 0)
        ctype = str(headers.get("content-type", "")).lower()
        if data[:4] == b"RIFF":
            encoding = "wav"
        elif kind == "pcm" and "json" not in ctype:
            encoding = "pcm_s16le"
        elif "wav" in ctype:
            encoding = "wav"
        else:
            encoding = "mp3"
        channels, source = answer_channels(op, headers, data)
        self._record(op, request, headers, {"encoding": encoding, "sample_rate": rate, "channels": channels,
                                            "channels_from": source}, data)
        return headers, data, encoding, rate, channels, None

    @staticmethod
    def write_audio(data: bytes, encoding: str, rate: int, channels: int, out: str | Path) -> dict:
        """Write returned audio as WAV at `out` (bit for bit for PCM, wrapped with its `channels`). mp3 is kept beside
        it and decoded by ffmpeg, which reads its channels from the stream. Returns the channels the WAV has."""
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        source = None
        if encoding == "pcm_s16le":
            jsonio.write_text_atomic(out, audio_mod.pcm16_to_wav(data, rate, channels))
        elif encoding == "wav":
            jsonio.write_text_atomic(out, data)
        else:
            source = out.with_suffix(".mp3")
            jsonio.write_text_atomic(source, data)
            ffmpeg = procs.which("ffmpeg")
            if not ffmpeg:
                raise MissingPrerequisite(f"ElevenLabs returned mp3 ({source.name}); turning it into a WAV take needs "
                                          "ffmpeg.", hint="Run `slate doctor --install` (it asks first).")
            procs.run([ffmpeg, "-v", "error", "-y", "-i", str(source), "-c:a", "pcm_s24le", str(out)], check=True,
                      timeout=300)
        with out.open("rb") as fh:
            written = wav_channels(fh.read(4096))
        return {"file": str(out), "source_file": str(source) if source else None, "channels": written or channels,
                "sha256": jsonio.sha256(out), "seconds": round(audio_mod.duration(out), 4)}

    # -- free reads -----------------------------------------------------------------------------------------------

    def subscription(self, *, snapshot: bool = False) -> dict:
        """The plan: tier, characters used and allowed, voice slots, and the best voice format it allows. Free.

        `slate doctor` uses this for the tier. With `snapshot=True` (live only) the remaining balance is recorded in
        the ledger, so spend made outside Slate (a chat connector) shows up when sessions are reconciled.
        """
        if self._subscription is None:
            data = self._free("subscription", {"key": self.key_name}, "/v1/user/subscription")
            out = {k: data.get(k) for k in SUBSCRIPTION_FIELDS if k in data}
            fmt, why = best_format(out.get("tier"))
            out.update({"lossless": format_allowed("pcm_48000", out.get("tier")) is True,
                        "best_voice_format": fmt, "format_reason": why, "replay": bool(data.get("replay"))})
            self._subscription = out
        out = dict(self._subscription)
        if snapshot and not self.replay and out.get("character_limit") is not None:
            ledger.snapshot(self.provider, float(out["character_limit"]) - float(out.get("character_count") or 0),
                            source="elevenlabs subscription")
        return out

    def choose_format(self, want: str = "pcm_48000", family: tuple[str, ...] = VOICE_FORMATS) -> dict:
        """{format, wanted, tier, lossless, reason}: the best allowed format, from the plan's tier."""
        try:
            tier = self.subscription().get("tier")
        except MissingPrerequisite:
            raise
        except Exception as exc:  # noqa: BLE001 - a failed read falls back to asking and handling a refusal
            log.warn(f"couldn't read the ElevenLabs plan ({exc}); asking for {want} and falling back if refused")
            tier = None
        fmt, reason = best_format(tier, want, family)
        return {"format": fmt, "wanted": want, "tier": tier, "lossless": is_lossless(fmt), "reason": reason}

    def voice_info(self, voice_id: str) -> dict:
        """What ElevenLabs says a voice is (name, category: premade, generated, cloned, professional, famous). Free."""
        return self._free("voice", {"voice_id": voice_id}, f"/v1/voices/{urllib.parse.quote(voice_id, safe='')}")

    # -- text to speech -------------------------------------------------------------------------------------------

    def tts(self, *, voice_id: str, text: str, model_id: str | None = None, voice_settings: dict | None = None,
            seed: int | None = None, previous_text: str | None = None, next_text: str | None = None,
            previous_request_ids: list[str] | None = None, next_request_ids: list[str] | None = None,
            output_format: str | None = None, out: str | Path | None = None, language_code: str | None = None,
            note: str | None = None) -> dict:
        """One read of `text` (audio tags included) with its neighbours' plain words as context.

        `previous_request_ids` and `next_request_ids` stitch the read to its neighbours' own recent reads: ElevenLabs
        takes up to three a side (the nearest ones are kept), ahead of their text, only for reads under two hours old
        (the voice stage picks them: slate.voice.tts.neighbour_request_ids).

        Returns the model, format actually used (and any fallback), request ids, billed characters and, with
        `out`, the WAV written there. In replay the result also carries exact word times (`words`).
        """
        if not text or not text.strip():
            raise UsageError("Nothing to say: the text is empty.")
        model = model_id or config.model("tts")
        wanted = output_format or self.choose_format()["format"]
        body: dict[str, Any] = {"text": text, "model_id": model}
        settings = {k: v for k, v in (voice_settings or {}).items() if v is not None}
        if settings:
            body["voice_settings"] = settings
        if seed is not None:
            body["seed"] = int(seed)
        if previous_text:
            body["previous_text"] = previous_text
        if next_text:
            body["next_text"] = next_text
        before = [str(r) for r in previous_request_ids or [] if r][-STITCH_MAX:]
        after = [str(r) for r in next_request_ids or [] if r][:STITCH_MAX]
        if before:
            body["previous_request_ids"] = before
        if after:
            body["next_request_ids"] = after
        if language_code:
            body["language_code"] = language_code
        est = self._estimate("tts", model, chars=len(text))
        path = f"/v1/text-to-speech/{urllib.parse.quote(voice_id, safe='')}"
        res, fmt, fallbacks = self._with_fallback("tts", est, format_chain(wanted), lambda f: (
            {"voice_id": voice_id, "body": body, "format": f}, dict(path=path, query={"output_format": f},
                                                                    json_body=body)), note=note)
        return self._finish_audio(res, out, model=model, fmt=fmt, wanted=wanted, fallbacks=fallbacks,
                                  extra={"voice_id": voice_id, "seed": body.get("seed"), "voice_settings": settings,
                                         "previous_text": previous_text, "next_text": next_text,
                                         "previous_request_ids": before, "next_request_ids": after})

    def _with_fallback(self, op: str, est: ledger.Estimate, chain: list[str],
                       build: Callable[[str], tuple[dict, dict]], note: str | None = None
                       ) -> tuple[dict, str, list[dict]]:
        """Try formats in order. A lossless format the plan refuses (HTTP 400/403/422, nothing billed) falls back to
        the next; anything else is raised. Each attempt is its own ledger row, so the record stays honest."""
        fallbacks: list[dict] = []
        for i, fmt in enumerate(chain):
            request, kw = build(fmt)
            try:
                res = self._spend(op, est, lambda r=request, f=fmt, k=kw: self._audio_call(op, r, f, **k), note=note,
                                  request=request)
            except http.HttpError as exc:
                if exc.status in FORMAT_REJECTED and is_lossless(fmt) and i + 1 < len(chain):
                    fallbacks.append({"format": fmt, "http": exc.status})
                    log.warn(f"ElevenLabs refused {fmt} (HTTP {exc.status}); trying {chain[i + 1]}")
                    continue
                raise
            return res, fmt, fallbacks
        raise UsageError("No output format was accepted.")  # pragma: no cover - the loop returns or raises

    def _audio_call(self, op: str, request: dict, fmt: str, **kw) -> tuple[dict, dict]:
        headers, data, encoding, rate, channels, words = self._audio_result(op, request, fmt, **kw)
        # channels_checked: the count is the answer's own (a parked answer from before it was says 1 for everything)
        return headers, {"data": data, "encoding": encoding, "rate": rate, "channels": channels, "words": words,
                         "channels_checked": True}

    def _finish_audio(self, res: dict, out: str | Path | None, *, model: str, fmt: str, wanted: str | None,
                      fallbacks: list, extra: dict | None = None) -> dict:
        payload = res["payload"]
        lossless = is_lossless(fmt) if fmt else payload["encoding"] in ("wav", "pcm_s16le")
        result = {"model": model, "format": fmt or payload["encoding"], "format_wanted": wanted,
                  "format_fallbacks": fallbacks, "lossless": lossless, "encoding": payload["encoding"],
                  "sample_rate": payload["rate"], "channels": payload["channels"],
                  "request_id": res["request_id"], "client_request_id": res["client_request_id"],
                  "character_cost": res["character_cost"], "ledger_row": res["ledger_row"], "replay": self.replay,
                  **(extra or {})}
        if payload.get("words") is not None:
            result["words"] = payload["words"]
        if out is not None:
            result.update(self.write_audio(payload["data"], payload["encoding"], payload["rate"],
                                           payload["channels"], out))
        else:
            result["audio"] = payload["data"]
        if res.get("reused"):
            result["reused_parked_answer"] = True
        self._unpark(res)
        return result

    # -- Voice Design and Remix -----------------------------------------------------------------------------------

    def _previews(self, op: str, request: dict, fmt: str, path: str, body: dict, est: ledger.Estimate,
                  out_dir: str | Path | None, prefix: str, timeout: float) -> dict:
        def call(req: dict, f: str):
            if self.replay:
                return self._replay(op, req)
            headers, raw = self._live("POST", path, query={"output_format": f}, json_body=body, timeout=timeout)
            data = json.loads(raw or b"{}")
            self._record(op, req, headers, {"json": data}, None)
            return headers, {"json": data}

        chain = format_chain(fmt)
        for i, f in enumerate(chain):
            req = {**request, "format": f}
            try:
                res = self._spend(op, est, lambda r=req, ff=f: call(r, ff), request=req)
            except http.HttpError as exc:
                if exc.status in FORMAT_REJECTED and is_lossless(f) and i + 1 < len(chain):
                    log.warn(f"ElevenLabs refused {f} (HTTP {exc.status}); trying {chain[i + 1]}")
                    continue
                raise
            fmt = f
            break
        data = res["payload"].get("json") or {}
        kind, rate = parse_format(fmt)
        previews = []
        for i, p in enumerate(data.get("previews") or [], start=1):
            raw = base64.b64decode(p.get("audio_base_64") or "")
            row = {"index": i, "generated_voice_id": p.get("generated_voice_id"), "media_type": p.get("media_type"),
                   "duration_secs": p.get("duration_secs"), "language": p.get("language")}
            if self.replay and p.get("words") is not None:
                row["words"] = p["words"]
            if out_dir is not None:
                enc = "wav" if raw[:4] == b"RIFF" else ("pcm_s16le" if kind in ("pcm",) else "mp3")
                channels = answer_channels(op, {"content-type": p.get("media_type") or ""}, raw)[0]
                row.update(self.write_audio(raw, enc, rate, channels, Path(out_dir) / f"{prefix}_{i}.wav"))
            else:
                row["audio"] = raw
            previews.append(row)
        self._unpark(res)
        return {"previews": previews, "text": data.get("text", body.get("text")), "format": fmt,
                "request_id": res["request_id"], "client_request_id": res["client_request_id"],
                "character_cost": res["character_cost"], "ledger_row": res["ledger_row"], "replay": self.replay}

    def design(self, *, voice_description: str, text: str, model_id: str | None = None, seed: int | None = None,
               guidance_scale: float | None = None, loudness: float | None = None, should_enhance: bool = False,
               output_format: str | None = None, out_dir: str | Path | None = None, prefix: str = "preview") -> dict:
        """Voice Design: three previews of a described voice reading `text`. `model_id` is always sent
        (default: the registry's voice_design model, eleven_ttv_v3), because the API's own default is older."""
        lo, hi = DESIGN_LIMITS["voice_description"]
        if not lo <= len(voice_description or "") <= hi:
            raise UsageError(f"A voice description must be {lo}-{hi} characters (got {len(voice_description or '')}).")
        lo, hi = DESIGN_LIMITS["text"]
        if not lo <= len(text or "") <= hi:
            raise UsageError(f"Voice Design preview text must be {lo}-{hi} characters (got {len(text or '')}); "
                             "longer text gives steadier previews.")
        _check_range("guidance_scale", guidance_scale, *DESIGN_LIMITS["guidance_scale"])
        _check_range("loudness", loudness, *DESIGN_LIMITS["loudness"])
        model = model_id or config.model("voice_design")
        fmt = output_format or self.choose_format()["format"]
        body: dict[str, Any] = {"voice_description": voice_description, "model_id": model, "text": text,
                                "auto_generate_text": False, "should_enhance": bool(should_enhance),
                                "stream_previews": False}
        for k, v in (("seed", seed), ("guidance_scale", guidance_scale), ("loudness", loudness)):
            if v is not None:
                body[k] = v
        est = self._estimate("design", model, chars=len(text))
        out = self._previews("design", {"body": body, "format": fmt}, fmt, "/v1/text-to-voice/design", body, est,
                             out_dir, prefix, timeout=600)
        out["model_id"] = model
        return out

    def save_voice(self, *, generated_voice_id: str, voice_name: str, voice_description: str,
                   labels: dict | None = None) -> dict:
        """Keep a designed preview as a voice in the account (uses a voice slot; no credits)."""
        body = {"voice_name": voice_name, "voice_description": voice_description,
                "generated_voice_id": generated_voice_id, "labels": labels or {}}
        return self._free("save_voice", {"body": body}, "/v1/text-to-voice", method="POST", json_body=body)

    def remix(self, *, voice_id: str, voice_description: str, text: str, prompt_strength: float | None = None,
              seed: int | None = None, loudness: float | None = None, guidance_scale: float | None = None,
              output_format: str | None = None, out_dir: str | Path | None = None, prefix: str = "remix") -> dict:
        """Voice Remix: the same voice with only what the description asks changed (for example, a cleaner
        recording). A low prompt_strength keeps the voice's identity. Three previews per call."""
        lo, hi = DESIGN_LIMITS["text"]
        if not lo <= len(text or "") <= hi:
            raise UsageError(f"Remix preview text must be {lo}-{hi} characters (got {len(text or '')}).")
        _check_range("prompt_strength", prompt_strength, 0.0, 1.0)
        _check_range("loudness", loudness, *DESIGN_LIMITS["loudness"])
        model = config.model("voice_remix")
        fmt = output_format or self.choose_format()["format"]
        body: dict[str, Any] = {"voice_description": voice_description, "text": text, "auto_generate_text": False}
        for k, v in (("prompt_strength", prompt_strength), ("seed", seed), ("loudness", loudness),
                     ("guidance_scale", guidance_scale)):
            if v is not None:
                body[k] = v
        est = self._estimate("remix", model, chars=len(text))
        path = f"/v1/text-to-voice/{urllib.parse.quote(voice_id, safe='')}/remix"
        out = self._previews("remix", {"voice_id": voice_id, "body": body, "format": fmt}, fmt, path, body, est,
                             out_dir, prefix, timeout=600)
        out["model_id"] = model
        return out

    # -- audio in: isolator and alignment -------------------------------------------------------------------------

    def isolate(self, *, audio: str | Path, out: str | Path | None = None) -> dict:
        """Voice Isolator: the voice without the background (paid per minute of audio)."""
        audio = Path(audio)
        seconds = audio_mod.duration(audio)
        model = config.model("voice_isolator")
        est = self._estimate("isolate", model, seconds=seconds)
        request = {"audio_sha256": jsonio.sha256(audio), "audio_path": str(audio), "seconds": round(seconds, 3)}
        res = self._spend("isolate", est, lambda: self._audio_call(
            "isolate", request, "", path="/v1/audio-isolation", query={}, fields={}, files={"audio": audio},
            timeout=600), request=request)
        return self._finish_audio(res, out, model=model, fmt="", wanted=None, fallbacks=[],
                                  extra={"source_audio": str(audio)})

    def align(self, *, audio: str | Path, text: str) -> dict:
        """Forced Alignment: word and character times for a known transcript (paid per minute). Slate uses it to
        evaluate against local Whisper, which stays the default aligner: forced alignment always places every word it
        is given, so it can't tell whether a word was actually said."""
        audio = Path(audio)
        if not text.strip():
            raise UsageError("Forced alignment needs the transcript.")
        seconds = audio_mod.duration(audio)
        model = config.model("alignment_eval")
        check = checked_price(model)
        if check:
            est = ledger.estimate(self.provider, "align", model=model,
                                  amount=math.ceil(check["per_minute"] * seconds / 60.0),
                                  basis=f"price check {check['verified_on']}: {seconds / 60:.2f} min × "
                                        f"{check['per_minute']} credits a minute (registry.json still says "
                                        f"{check['registry_per_minute']})")
        else:
            est = self._estimate("align", model, seconds=seconds)
        request = {"audio_sha256": jsonio.sha256(audio), "audio_path": str(audio), "seconds": round(seconds, 3),
                   "text": text}

        def call():
            if self.replay:
                return self._replay("align", request)
            headers, raw = self._live("POST", "/v1/forced-alignment", fields={"text": text}, files={"file": audio},
                                      timeout=600)
            data = json.loads(raw or b"{}")
            self._record("align", request, headers, {"json": data}, None)
            return headers, {"json": data}

        res = self._spend("align", est, call, request=request)
        data = res["payload"].get("json") or {}
        self._unpark(res)
        return {"words": data.get("words") or [], "characters": data.get("characters") or [],
                "loss": data.get("loss"), "model": model, "request_id": res["request_id"],
                "client_request_id": res["client_request_id"], "ledger_row": res["ledger_row"],
                "replay": self.replay}

    # -- music ----------------------------------------------------------------------------------------------------

    def compose_music(self, *, composition_plan: dict | None = None, prompt: str | None = None,
                      music_length_ms: int | None = None, model_id: str | None = None,
                      force_instrumental: bool | None = None, seed: int | None = None,
                      respect_sections_durations: bool = True, output_format: str | None = None,
                      out: str | Path | None = None, note: str | None = None) -> dict:
        """Eleven Music: one composition from a plan or a prompt. `model_id` is always sent (default: the registry's
        music model, music_v2_5), because the API's own default is older.

        The plan must be the shape the model takes (`music_plan_schema`): chunks for music_v2 and later, sections for
        music_v1; a mismatch is refused before anything is spent. `respect_sections_durations` is a music_v1 option
        and is only sent with a section plan (the v2 models always keep chunk durations)."""
        if (composition_plan is None) == (prompt is None):
            raise UsageError("Pass exactly one of composition_plan or prompt.")
        model = model_id or config.model("music")
        body: dict[str, Any] = {"model_id": model}
        if composition_plan is not None:
            problems = plan_problems(composition_plan, model)
            if problems:
                raise UsageError("Eleven Music would reject this composition plan: " + "; ".join(problems[:5]) + ".",
                                 hint="Build the plan for the model it's sent to: `slate score plan` does.")
            shape = plan_shape(composition_plan)
            body["composition_plan"] = composition_plan
            if shape == "sections":
                body["respect_sections_durations"] = bool(respect_sections_durations)
            seconds = sum(int(b["duration_ms"]) for b in composition_plan[shape]) / 1000.0
        else:
            body["prompt"] = prompt
            if music_length_ms is not None:
                _check_range("music_length_ms", music_length_ms, 3000, 600000)
                body["music_length_ms"] = int(music_length_ms)
            seconds = music_length_ms / 1000.0 if music_length_ms else DEFAULT_MUSIC_SECONDS
        if force_instrumental is not None:
            body["force_instrumental"] = bool(force_instrumental)
        if seed is not None:
            body["seed"] = int(seed)
        limit = (config.model_info(model).get("limits") or {}).get("part_seconds_max")
        if limit and seconds > limit:
            log.warn(f"a {seconds:.0f} s part is longer than {limit} s; long plans run out of material, so split it "
                     "at a story turn")
        wanted = output_format or self.choose_format("pcm_48000", MUSIC_FORMATS)["format"]
        est = self._estimate("music", model, seconds=seconds)
        res, fmt, fallbacks = self._with_fallback("music", est, format_chain(wanted, MUSIC_FORMATS), lambda f: (
            {"body": body, "format": f, "seconds": seconds},
            dict(path="/v1/music", query={"output_format": f}, json_body=body, timeout=600)), note=note)
        result = self._finish_audio(res, out, model=model, fmt=fmt, wanted=wanted, fallbacks=fallbacks,
                                    extra={"seed": body.get("seed"), "seconds_planned": seconds})
        length_given = composition_plan is not None or music_length_ms is not None
        if length_given and result.get("seconds") and not 0.67 <= float(result["seconds"]) / seconds <= 1.5:
            # A take at twice or half its plan is what a channel misread looks like (stereo read as mono doubles it).
            log.warn(f"the take runs {result['seconds']:.2f} s where {seconds:.2f} s was planned "
                     f"({result.get('channels')} channel(s) at {result.get('sample_rate')} Hz); listen before using it")
        return result


# ---------------------------------------------------------------------------------------------------------------
# Hooks for the job queue's recovery and balance snapshots (slate.jobs.resume)


def remaining_credits(key: str = PROVIDER) -> float:
    """What's left this period on a key's account, from the free subscription read (replay-aware)."""
    client = Client(key=key, mode="autonomous" if key == "elevenlabs_oneiro" else "supervised")
    sub = client.subscription()
    if sub.get("character_limit") is None:
        raise MissingPrerequisite("ElevenLabs didn't report a character limit for this plan.")
    return float(sub["character_limit"]) - float(sub.get("character_count") or 0)


def _resume():
    """slate.jobs.resume, by its full name (the module the queue itself uses), or None when the queue isn't built."""
    import importlib  # noqa: PLC0415

    try:
        return importlib.import_module("slate.jobs.resume")
    except ModuleNotFoundError as exc:
        if str(exc.name or "").startswith("slate.jobs"):
            return None
        raise


def balance_own() -> float:
    """The balance reader the job queue uses for `slate ledger snapshot` (the owner's key)."""
    return remaining_credits(PROVIDER)


def balance_oneiro() -> float:
    """The balance reader for Oneiro's capped key."""
    return remaining_credits("elevenlabs_oneiro")


# What this module registers with slate.jobs.resume (tests re-install these, since the registry is process-wide).
BALANCE_READERS = {PROVIDER: balance_own, "elevenlabs_oneiro": balance_oneiro}


def _register_resume_hooks() -> None:
    # ElevenLabs answers in the same request it bills, so there's no request to poll after a crash (the queue closes
    # such rows as orphaned and `slate ledger reconcile` settles them). Balances, though, it can read for free.
    resume = _resume()
    if resume is None:
        return
    if hasattr(resume, "register_balance"):
        for name, reader in BALANCE_READERS.items():
            resume.register_balance(name, reader)


_register_resume_hooks()
