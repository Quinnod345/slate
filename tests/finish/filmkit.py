"""A tiny synthetic film for the finish tests, made with ffmpeg and numpy in a temp folder.

Two acts, three shots, 40 frames at the fixture format's 25 fps (1.6 s) and 160×90: act renders as ProRes 422 HQ
10-bit (smooth moving gradients, so an honest H.264 master clears 40 dB), a 48 kHz / 24-bit mix with four stems,
shots, visual events, voice cues and words, captions, and voice takes. Everything neutral: no brand, no real film's
size or rate. The film itself is made by slate.state (film.create), with the fixture formats and brands behind small
fakes of slate.formats and slate.brand.pack, as the state tests do.
"""

from __future__ import annotations

import json
import sys
import types
import wave
from pathlib import Path

import numpy as np

FIXTURES = Path(__file__).parent / "fixtures"
FORMATS = FIXTURES / "formats"
BRANDS = FIXTURES / "brands"
REFS = ("truth", "look_motion", "voice", "sound")
ACTS = [("Opening", [("S01", 12, "Plans take minutes."), ("S02", 13, "")]), ("Close", [("S03", 15, "Try it today.")])]
SR = 48000


def fake_formats() -> types.ModuleType:
    mod = types.ModuleType("slate.formats")

    def load(name: str) -> dict:
        path = FORMATS / f"{name}.json"
        if not path.is_file():
            from slate.errors import UsageError

            raise UsageError(f"No format called {name!r} (fixture)")
        return json.loads(path.read_text())

    mod.load = load
    mod.names = lambda: sorted(p.stem for p in FORMATS.glob("*.json"))
    mod.safe_zones = lambda fmt, platform: (fmt.get("safe_zones") or {}).get(platform) or {}
    mod.FAKE = True
    return mod


def fake_brand_pack(store: Path) -> types.ModuleType:
    mod = types.ModuleType("slate.brand.pack")

    def find(name: str) -> Path:
        path = store / name
        if not (path / "brand.json").is_file():
            from slate.errors import UsageError

            raise UsageError(f"No brand pack called {name!r} (fixture)")
        return path

    def load(name: str) -> dict:
        p = Path(name)
        path = p if p.is_dir() else find(name)
        return json.loads((path / "brand.json").read_text())

    def missing_references(pack: dict) -> list[str]:
        refs = pack.get("references") or {}
        return [k for k in REFS if not (isinstance(refs.get(k), dict) and refs[k].get("approved_by"))]

    mod.find, mod.load, mod.missing_references = find, load, missing_references
    mod.references_approved = lambda pack: not missing_references(pack)
    mod.approved_claims = lambda pack: list((pack.get("truth") or {}).get("approved_claims") or [])
    mod.FAKE = True
    return mod


def install_fakes(store: Path, setitem=None) -> None:
    """Put the fixture brands in `store` and the fakes in sys.modules (setitem: pytest's monkeypatch.setitem)."""
    import shutil

    for pack in BRANDS.iterdir():
        if not (store / pack.name).exists():
            shutil.copytree(pack, store / pack.name)
    put = setitem or (lambda d, k, v: d.__setitem__(k, v))
    put(sys.modules, "slate.formats", fake_formats())
    put(sys.modules, "slate.brand.pack", fake_brand_pack(store))


def ffmpeg(*args: str) -> None:
    from slate import procs

    proc = procs.run(["ffmpeg", "-v", "error", "-nostdin", "-y", *args], timeout=120)
    assert proc.returncode == 0, proc.stderr


def render_act(path: Path, frames: int, size: tuple[int, int], rate: str, seed: int, *, source: str = "gradients",
               speed: float = 0.02) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    src = (f"gradients=s={w}x{h}:r={rate}:speed={speed}:seed={seed}" if source == "gradients"
           else f"testsrc2=s={w}x{h}:r={rate}")
    ffmpeg("-f", "lavfi", "-i", src, "-frames:v", str(frames), "-c:v", "prores_ks", "-profile:v", "3",
           "-vendor", "apl0", "-pix_fmt", "yuv422p10le", "-color_primaries", "bt709", "-color_trc", "bt709",
           "-colorspace", "bt709", str(path))


def write_wav24(path: Path, data: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.asarray(data, dtype=np.float64)
    if data.ndim == 1:
        data = np.stack([data, data], axis=1)
    ints = np.clip(np.round(data * (2 ** 23 - 1)), -(2 ** 23), 2 ** 23 - 1).astype("<i4")
    raw = ints.reshape(-1)
    b = np.zeros((raw.size, 3), dtype=np.uint8)
    b[:, 0] = raw & 0xFF
    b[:, 1] = (raw >> 8) & 0xFF
    b[:, 2] = (raw >> 16) & 0xFF
    with wave.open(str(path), "wb") as w:
        w.setnchannels(data.shape[1])
        w.setsampwidth(3)
        w.setframerate(SR)
        w.writeframes(b.tobytes())


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1) + "\n")


def build(film_dir: Path, *, renders: bool = True, mix: bool = True, captions: bool = True, voice: bool = True,
          replay_voice: bool = False) -> Path:
    """Fill an existing film folder (made by slate.state film.create) with timing, renders, sound and captions."""
    from slate import clock as clock_mod
    from slate import jsonio

    fj = json.loads((film_dir / "film.json").read_text())
    clock = clock_mod.from_film(fj)
    size = (int(fj["width"]), int(fj["height"]))
    rate = f"{clock.fps_num}/{clock.fps_den}"
    shots, acts, at = [], [], 0
    for i, (name, members) in enumerate(ACTS, 1):
        ids = []
        a_in = at
        for sid, frames, vo in members:
            shots.append({"id": sid, "act": i, "act_name": name, "frame_in": at, "frame_out": at + frames,
                          "frames": frames, "sec_in": round(clock.secs_of(at), 6),
                          "sec_out": round(clock.secs_of(at + frames), 6), "vo": vo, "on_screen": f"card {sid}"})
            ids.append(sid)
            at += frames
        acts.append({"index": i, "name": name, "shots": ids, "frame_in": a_in, "frame_out": at, "frames": at - a_in})
    total = at
    write_json(film_dir / "timing/shots.json", {"generated": "fixture", "fps_num": clock.fps_num,
                                                "fps_den": clock.fps_den,
                                                "width": size[0], "height": size[1], "total_frames": total,
                                                "total_seconds": round(clock.secs_of(total), 6), "acts": acts,
                                                "shots": shots})
    write_json(film_dir / "timing/visual_events/act1.json",
               [{"shot": "S01", "id": "tap-plan", "t": round(clock.secs_of(6), 6), "kind": "tap", "size": "small"},
                {"shot": "S02", "id": "card-lands", "t": round(clock.secs_of(18), 6), "kind": "land"}])
    write_json(film_dir / "timing/visual_events/act2.json",
               [{"shot": "S03", "id": "logo-stamp", "t": round(clock.secs_of(30), 6), "kind": "stamp"}])
    if voice:
        write_json(film_dir / "timing/vo_cues.json", {"cues": [
            {"shot": "S01", "at": 0.08, "file": "voice/takes/S01/t01.wav", "gain": 0},
            {"shot": "S03", "at": 0.04, "file": "voice/takes/S03/t01.wav", "gain": 0}]})
        write_json(film_dir / "timing/vo_words.json", {"shots": {
            "S01": {"take": "t01", "line": "Plans take minutes.",
                    "words": [{"w": "Plans", "start": 0.0, "end": 0.12}, {"w": "take", "start": 0.13, "end": 0.26},
                              {"w": "minutes.", "start": 0.27, "end": 0.4}]},
            "S03": {"take": "t01", "line": "Try it today.",
                    "words": [{"w": "Try", "start": 0.0, "end": 0.1}, {"w": "it", "start": 0.11, "end": 0.18},
                              {"w": "today.", "start": 0.19, "end": 0.5}]}}})
        takes = {"shots": {}}
        for sid in ("S01", "S03"):
            takes["shots"][sid] = {"selected": "t01", "takes": {"t01": {"raw": f"voice/takes/{sid}/t01.wav"}}}
            write_json(film_dir / f"voice/takes/{sid}/t01.json",
                       {"shot": sid, "take": "t01", "text": "x", "sent": "x", "model": "eleven_v4",
                        "voice_id": "fixture-voice-0002", "voice_source": "designed", "voice_settings": {},
                        "seed": 1, "format": "pcm_48000", "request_id": "req-fixture", "file": f"t01.wav",
                        "sha256": "0" * 64, "seconds": 0.5, "replay": bool(replay_voice),
                        "created_at": "2026-09-30T00:00:00+00:00"})
        write_json(film_dir / "voice/takes.json", takes)
    if renders:
        entries = []
        for a in acts:
            f = film_dir / f"renders/act{a['index']}.mov"
            render_act(f, a["frames"], size, rate, seed=a["index"])
            entries.append({"act": a["index"], "engine": "remotion", "file": f"renders/act{a['index']}.mov",
                            "frames": [a["frame_in"], a["frame_out"]], "samples": 16, "sha256": jsonio.sha256(f),
                            "made_at": "2026-09-30T00:00:00+00:00", "bundle_hash": "fixture"})
        write_json(film_dir / "renders/registry.json", {"generated": "fixture", "entries": entries})
    if mix:
        n = int(round(clock.secs_of(total) * SR))
        t = np.arange(n) / SR
        stems = {"dialogue": 0.1 * np.sin(2 * np.pi * 220 * t), "effects": 0.03 * np.sin(2 * np.pi * 880 * t),
                 "ambience": np.zeros(n), "music": 0.05 * np.sin(2 * np.pi * 110 * t)}
        for k, v in stems.items():
            write_wav24(film_dir / f"sound/mix/stems/{k}.wav", v)
        write_wav24(film_dir / "sound/mix/final_mix.wav", sum(stems.values()))
        write_json(film_dir / "sound/mix/final_mix.json", {"generated": "fixture", "mix": "sound/mix/final_mix.wav",
                                                           "integrated_lufs": -14.0})
    if captions:
        fid = fj["id"]
        (film_dir / "voice/captions").mkdir(parents=True, exist_ok=True)
        (film_dir / f"voice/captions/{fid}.srt").write_text("1\n00:00:00,080 --> 00:00:00,480\nPlans take minutes.\n\n"
                                                            "2\n00:00:01,040 --> 00:00:01,540\nTry it today.\n")
        (film_dir / f"voice/captions/{fid}.vtt").write_text("WEBVTT\n\n00:00.080 --> 00:00.480\nPlans take minutes.\n\n"
                                                            "00:01.040 --> 00:01.540\nTry it today.\n")
        write_json(film_dir / "timing/captions.json", {"cues": [
            {"start": 0.08, "end": 0.48, "text": "Plans take minutes."},
                                                                {"start": 1.04, "end": 1.54, "text": "Try it today."}]})
    return film_dir


def make(films_dir: Path, film_id: str = "f1", *, mode: str = "supervised", brand: str = "onboarded-brand",
         format: str = "finish-tiny", **kw) -> Path:
    """Create the film through slate.state, then fill it. Needs install_fakes() first."""
    from slate.state import film

    d = film.create(film_id, brand=brand, format=format, mode=mode, git=False, films_dir=films_dir)
    return build(d, **kw)


def pass_gates(film_dir: Path, names: list[str] | None = None, *, extra: dict | None = None) -> None:
    """Record PASS for every gate a final delivery needs (the tests' stand-in for a clean `slate qa` run)."""
    from slate import gates

    if names is None:
        from slate.finish import deps

        names = [n for n in (deps.qa_gate_names() or []) if n != "psnr"]  # psnr is the master's own, measured
        names += deps.sound_gate_names()  # `slate verify`'s, required when the master carries the mix
    def fixture_engine(name: str) -> dict:
        """Represent a fresh fixture result under the gate's current rule."""
        revision = gates.rule_revision(name)
        metadata = {"rule_revision": revision} if revision is not None else {}
        return gates.engine("fixture", "1", **metadata)

    results = [gates.GateResult.pass_(n, "fixture: passed", engine=fixture_engine(n)) for n in names]
    for n, status in (extra or {}).items():
        results.append(gates.GateResult(n, status, f"fixture: {status}", engine=fixture_engine(n)))
    gates.write_report(film_dir, results)


def cli(film_dir: Path, *argv: str) -> tuple[int, str, str]:
    """Run `slate <argv> --film <film_dir>` in-process: (exit code, stdout, stderr)."""
    from slate.cli import run_cli

    return run_cli([*argv, "--film", str(film_dir)])


def cli_json(film_dir: Path, *argv: str) -> tuple[int, dict]:
    import json as _json

    code, out, err = cli(film_dir, *argv, "--json")
    try:
        return code, _json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
    except (ValueError, IndexError) as exc:
        raise AssertionError(f"not JSON: {out!r} / {err!r}") from exc
