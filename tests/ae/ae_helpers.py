"""Helpers for the After Effects lane's tests: synthetic prefs files and fake process results.

The synthetic prefs files follow the format the film pipeline's ae_pref.py and read_ae_report.py parse (CR line
endings, `["Section"]` headers, TAB-quoted entries, TAB-TAB continuation lines, quoted runs and hex bytes). They are
written from that description, never copied from a real prefs file.
"""

from __future__ import annotations

import subprocess

PREAMBLE = [b"", b"# Synthetic After Effects preferences for Slate's tests", b""]


def prefs_lines(*, scripting: bytes = b'"1"', cache: bytes = b'"1"', report: list[bytes] | None = None,
                extra: list[bytes] | None = None) -> list[bytes]:
    lines = PREAMBLE + [
        b'["Coachmarks Settings"]',
        b'\t"Enabled" = "0"',
        b'\t"Simple.Coachmarks.Seen" = "1"',
        b"",
        b'["Concurrent Frame Rendering"]',
        b'\t"Reserved CPU Power" = "25"',
        b"",
        b'["Disk Cache Controls"]',
        b'\t"Enabled 2" = ' + cache,
        b'\t"Folder 7" = "~/Library/Caches"',
        b'\t"Max Size 3" = "92"',
        b"",
        b'["Main Pref Section v2"]',
        b'\t"Mercury GPU Accelerator GUID" = 0A1B2C3D',
        b'\t"Pref_COMP_PATH_DISPLAY_PRE" = "1"',
        b'\t"Pref_LAST_DATE_TIME" = "2026-09-29 20:00:00"',
        b'\t"Pref_SCRIPTING_FILE_NETWORK_SECURITY" = ' + scripting,
        b'\t"Some Bare Number" = 01',
        b'\t"Wrapped Value" = "aaaa"\\',
        b'\t\t"bbbb"',
        b"",
        b'["Most Recently Used (MRU) Search v2"]',
        b'\t"MRU Project Path ID # 0, File Path" = "/Users/someone/film.aep"',
        b"",
    ]
    if report:
        lines += [b'["Settings_Slate"]'] + report + [b""]
    if extra:
        lines += extra
    return lines


def prefs_bytes(**kw) -> bytes:
    return b"\r".join(prefs_lines(**kw))


def settings_entry(key: str, value: str) -> bytes:
    """One app.settings entry as AE writes it: a quoted value (quotes inside become hex bytes)."""
    return b'\t"' + key.encode() + b'" = "' + value.encode("utf-8").replace(b'"', b'"22"') + b'"'


def completed(argv, code: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(list(map(str, argv)), code, out, err)


# ---------------------------------------------------------------------------------------------------------------
# A neutral film, a mock After Effects (node, engine/ae/tests/mock_run.mjs) and a fake media toolchain

import json as _json  # noqa: E402
import re as _re  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

from slate import paths as _paths  # noqa: E402
from slate import procs as _procs  # noqa: E402

FPS_NUM, FPS_DEN, W, H = 24000, 1001, 1280, 720
LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">
  <g transform="translate(10,5)"><rect id="bar" x="0" y="0" width="60" height="10" fill="#3366FF"/></g>
  <path id="swoosh" d="M10 50 C 30 20, 60 20, 90 50 Z" fill="#1C1C1C"/>
</svg>
"""


def make_brand(store: _Path, name: str = "neutral-ae") -> _Path:
    d = store / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "logo.svg").write_text(LOGO_SVG)
    pack = {
        "name": name,
        "palette": {"paper": "#F4F1EA", "ink": "#1C1C1C", "accent": "#3366FF"},
        "type": {"display": {"family": "Mock Sans", "postscript": "MockSans-Regular", "source": "installed",
                             "licence": "test fixture"}},
        "motion": {"eases": {"enter": [0, 0, 0.58, 1], "move": "sine", "pop": [0.34, 1.56, 0.64, 1]},
                   "durations": {"quick": 0.33, "base": 0.5}},
        "logos": {"mark": "logo.svg"},
    }
    (d / "brand.json").write_text(_json.dumps(pack))
    return d


SCENE = {
    "bg": "paper",
    "layers": [
        {"id": "card", "type": "rect", "size": [400, 240], "roundness": 24, "fill": "accent",
         "transform": {"position": [640, 360]}},
        {"id": "title", "type": "text", "text": "Hello", "font": "display", "font_size": 48, "color": "ink",
         "transform": {"position": [640, 200]}},
        {"id": "mark", "type": "logo", "logo": "mark", "width": 200, "pos": [540, 500]},
    ],
    "anims": [
        {"id": "card-in", "layer": "card", "prop": "position", "from": [640, 460], "to": [640, 360],
         "ease": "enter", "dur": "base", "land": "S3/card",
         "event": {"kind": "land", "size": "m", "note": "the card lands"}},
        {"id": "title-fade", "layer": "title", "prop": "opacity",
         "keys": [{"at": {"shot": "S3", "t": 0.5}, "value": 0, "ease": "move"},
                  {"at": {"shot": "S3", "t": 1.25}, "value": 100}]},
        {"id": "card-grow", "layer": "card", "prop": "scale", "from": [90, 90], "to": [100, 100], "ease": "pop",
         "dur": "quick", "start": {"shot": "S3", "frame": 4}},
    ],
}


def make_film(films: _Path, brands: _Path, fid: str = "neutral", *, scene: dict | None = SCENE, mix: bool = True,
              mode: str = "supervised") -> tuple[_Path, dict]:
    """A 264-frame, three-act film at 24000/1001: acts 1 and 3 rendered in code, act 2 built in AE."""
    make_brand(brands)
    d = films / fid
    for sub in ("timing", "renders", "sound/mix", "scenes/ae"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    film = {"schema": 1, "id": fid, "title": "Neutral film", "format": "test", "language": "en", "fps_num": FPS_NUM,
            "fps_den": FPS_DEN, "width": W, "height": H, "aspect": "16:9", "brand": "neutral-ae", "mode": mode,
            "generation": "none", "voice": {}, "targets": {}, "budget": {}, "pins": {}}
    (d / "film.json").write_text(_json.dumps(film))
    fps = FPS_NUM / FPS_DEN
    spans = [("S1", 1, 0, 48), ("S2", 1, 48, 120), ("S3", 2, 120, 192), ("S4", 3, 192, 240), ("S5", 3, 240, 264)]
    shots = [{"id": s, "act": a, "frame_in": i, "frame_out": o, "frames": o - i, "sec_in": round(i / fps, 6),
              "sec_out": round(o / fps, 6), "on_screen": "Hello" if s == "S3" else "", "picture": f"shot {s}"}
             for s, a, i, o in spans]
    shots[0]["vo_frame"] = 6
    doc = {"fps_num": FPS_NUM, "fps_den": FPS_DEN, "width": W, "height": H, "total_frames": 264,
           "acts": [{"index": 1, "name": "Act 1 · Opening", "shots": ["S1", "S2"]},
                    {"index": 2, "name": "Act 2 · The card", "shots": ["S3"]},
                    {"index": 3, "name": "Act 3 · Close", "shots": ["S4", "S5"]}],
           "shots": shots}
    (d / "timing" / "shots.json").write_text(_json.dumps(doc))
    timeline = {"fps": fps, "fps_num": FPS_NUM, "fps_den": FPS_DEN, "shots": {
        "S1": [{"id": "hello", "t": 0.5, "frame": 12, "sec": 0.5005, "film_frame": 12}],
        "S3": [{"id": "card", "t": 1.0, "frame": 24, "sec": 1.001, "film_frame": 144, "start_frame": 16,
                "dur": 0.5, "ease": "enter"}]}}
    (d / "timing" / "timeline.json").write_text(_json.dumps(timeline))
    (d / "timing" / "vo_words.json").write_text(_json.dumps({"shots": {"S1": {"take": "S1.wav", "line": "hello there",
        "words": [{"w": "hello", "start": 0.1, "end": 0.4}, {"w": "there", "start": 0.45, "end": 0.8}]}}}))
    (d / "timing" / "vo_cues.json").write_text(_json.dumps({"cues": [{"shot": "S1", "at": 0.25, "file": "S1.wav"}]}))
    media = {}
    entries = []
    for act, (i, o) in ((1, (0, 120)), (3, (192, 264))):
        f = d / "renders" / f"act{act}.mov"
        f.write_bytes(b"render")
        media[str(f)] = {"width": W, "height": H, "frameRate": fps, "duration": (o - i) / fps, "hasVideo": True}
        entries.append({"act": act, "engine": "remotion", "file": f"renders/act{act}.mov", "frames": [i, o],
                        "samples": 16, "sha256": "0" * 64, "made_at": "2026-09-30T12:00:00+00:00",
                        "bundle_hash": "b" * 16})
    (d / "renders" / "registry.json").write_text(_json.dumps({"entries": entries}))
    if mix:
        m = d / "sound" / "mix" / "final_mix.wav"
        m.write_bytes(b"RIFF")
        media[str(m)] = {"duration": 264 / fps + 0.01, "hasAudio": True, "hasVideo": False}
    if scene is not None:
        (d / "scenes" / "ae" / "act2.json").write_text(_json.dumps(scene))
    return d, media


class FakeMedia:
    """aerender, ffprobe and ffmpeg, simulated: files carry a frame count; failures can be scripted."""

    def __init__(self, spec_frames: dict | None = None):
        self.frames: dict[str, int] = {}
        self.calls: list[list[str]] = []
        self.fail_aerender: dict[int, int] = {}  # rqindex -> number of failing tries left
        self.short_conform = False
        self.plan: list[dict] = []
        self.chunk_dir: _Path | None = None
        self.names = ("h264_c{k}.mp4", "alpha_c{k}.mov")
        self.comp_frames: dict[str, int] = spec_frames or {}
        self.on_frames = None  # callable(png_dir, n) for smoke frame extraction

    def _touch(self, path: str, n: int) -> None:
        p = _Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"media")
        self.frames[str(p)] = n

    def __call__(self, argv, timeout=None, **kw):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        exe = _Path(argv[0]).name
        if exe == "aerender":
            if "-rqindex" in argv:
                k = int(argv[argv.index("-rqindex") + 1])
                if self.fail_aerender.get(k, 0) > 0:
                    self.fail_aerender[k] -= 1
                    return completed(argv, 1, "", "aerender: crashed")
                c = self.plan[k - 1]
                for name in self.names:
                    self._touch(str(self.chunk_dir / name.replace("{k}", str(c["index"]))), c["frames"])
                return completed(argv, 0, "PROGRESS: done")
            out = argv[argv.index("-output") + 1]
            comp = argv[argv.index("-comp") + 1]
            self._touch(out, self.comp_frames.get(comp, 0))
            return completed(argv, 0, "PROGRESS: done")
        if exe == "ffprobe":
            n = self.frames.get(argv[-1])
            # AE's QuickTime output lists the stream twice: "N", "", "N"
            return completed(argv, 0 if n is not None else 1, f"{n}\n\n{n}\n" if n is not None else "")
        if exe == "ffmpeg":
            out = argv[-1]
            if "-f" in argv and "concat" in argv:
                listing = argv[argv.index("-i") + 1]
                parts = [_re.match(r"file '(.*)'", ln).group(1) for ln in _Path(listing).read_text().splitlines()]
                self._touch(out, sum(self.frames.get(p, 0) for p in parts))
            elif out.endswith(".png"):
                src = argv[argv.index("-i") + 1]
                n = self.frames.get(src, 0)
                if self.on_frames:
                    self.on_frames(_Path(out).parent, n)
            else:
                src = argv[argv.index("-i") + 1]
                self._touch(out, self.frames.get(src, 0) - (1 if self.short_conform else 0))
            return completed(argv, 0)
        raise AssertionError(f"unexpected command {argv}")


class MockAE:
    """osascript + After Effects, played by engine/ae/tests/mock_run.mjs in node. Everything else goes to `media`.

    Runs are replayed in one mock session each time (a build, then its audit), and each report lands in the stand-in
    prefs file exactly as AE's app.settings would write it."""

    def __init__(self, prefs_file: _Path, *, media: dict | None = None, state: dict | None = None,
                 other: FakeMedia | None = None):
        self.prefs_file = prefs_file
        state = dict(state or {})
        self.scripting = state.pop("scripting", b'"0"')
        self.state = {"media": media or {}, **state}
        self.history: list[str] = []
        self.other = other or FakeMedia()
        self.results: list[dict] = []

    def __call__(self, argv, timeout=None, **kw):
        argv = [str(a) for a in argv]
        if argv[0] != "/usr/bin/osascript":
            return self.other(argv, timeout=timeout, **kw)
        wrapper = _re.search(r'DoScriptFile "(.*)"$', argv[4]).group(1)
        self.history.append(wrapper)
        session = _paths.data() / "ae" / "mock_session.json"
        session.parent.mkdir(parents=True, exist_ok=True)
        session.write_text(_json.dumps({"state": self.state, "runs": [{"wrapper": w} for w in self.history]}))
        runner = _paths.ENGINE_SRC / "ae" / "tests" / "mock_run.mjs"
        proc = _procs.run(["node", str(runner), "--session", str(session)], timeout=120)
        if proc.returncode != 0:
            raise AssertionError(f"mock AE failed: {proc.stderr[-2000:]}")
        out = _json.loads(proc.stdout)
        last = out["runs"][-1]
        if last.get("error"):
            raise AssertionError(f"the script threw in mock AE: {last['error'][:2000]}")
        self.results.append(out)
        entries = [settings_entry(k, v) for k, v in last["settings"].items()]
        self.prefs_file.write_bytes(prefs_bytes(scripting=self.scripting, report=entries))
        return completed(argv, 0, "0\n")
