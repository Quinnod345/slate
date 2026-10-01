"""Review and phone cuts and thumbnails (review_cuts.sh and slack_clip.sh, generalized).

Every cut is made from one source, decoded once:

- the **master** (masters/master.json's mezzanine file) when it's intact and made from the film's current renders
  and mix;
- otherwise the **act renders** joined in film order plus the final mix, the way the films made their review cuts
  (a packet never waits for the masters).

Before the final mix exists, the cuts play the voice alone, every take placed on its cue the way the mix will place
it (timing/vo_cues.json, through slate.sound.vo), instead of silence; the packet says so. A proposed mix can stand in
for the film's own (`slate review --audio <mix>`), so a new mix is reviewed against the current picture.

Sizes come from the film: the phone cut's short side is [finish.review] phone_short_side (360 by default, never
upscaled), the review cut is half the film's size. H.264 is VideoToolbox when this ffmpeg has it (the CPU stays free
for renders), else libx264; audio is AAC at 48 kHz; never -shortest. Every cut's frame count is checked against the
film's.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from slate import jsonio, log
from slate.errors import MissingPrerequisite, SlateError, UsageError
from slate.finish import burnin, deps, media, settings
from slate.finish.film import Film

VOICE_ONLY = "review/voice_only.wav"  # the voice placed on its cues, for cuts made before the mix exists


@dataclass
class Source:
    """Where a cut's picture and sound come from."""

    kind: str  # "master" or "renders"
    files: list[Path]
    offsets: list[int]  # the film frame each file starts at
    audio: Path | None = None  # a separate sound file (renders, or a proposed mix over a master)
    master_audio: bool = False
    notes: list[str] = field(default_factory=list)
    sound: str | None = None  # what the sound is: "final mix", "voice only", "proposed mix", "the master's own"

    def describe(self, film: Film) -> dict:
        out = {"kind": self.kind, "files": [film.rel(f) for f in self.files],
               "audio": film.rel(self.audio) if self.audio else ("in the master" if self.master_audio else None),
               "notes": self.notes}
        out["sound"] = self.sound or ("the master's own" if self.master_audio and not self.audio else
                                      "final mix" if self.audio else "silent")
        return out

    def ffmpeg_inputs(self, tmp: Path) -> tuple[list[str], str | None, int]:
        """(input args, audio map or None, how many inputs) for this source."""
        if self.kind == "master":
            if self.audio is not None:  # another sound over the master's picture (a proposed mix)
                return ["-i", str(self.files[0]), "-i", str(self.audio)], "1:a:0", 2
            return ["-i", str(self.files[0])], ("0:a:0" if self.master_audio else None), 1
        lst = media.concat_list(self.files, tmp / "acts.txt")
        args = ["-f", "concat", "-safe", "0", "-i", str(lst)]
        if self.audio is not None:
            return args + ["-i", str(self.audio)], "1:a:0", 2
        return args, None, 1

    def locate(self, frame: int) -> tuple[Path, int]:
        """(file, frame index inside it) for a film frame."""
        for f, off, nxt in zip(self.files, self.offsets, [*self.offsets[1:], None]):
            if frame >= off and (nxt is None or frame < nxt):
                return f, frame - off
        return self.files[-1], frame - self.offsets[-1]


def voice_only(film: Film) -> tuple[Path | None, str | None]:
    """The voice alone, every take placed on its cue exactly as the mix places it (slate.sound.vo), as a WAV the
    film's length: what review cuts play until the final mix exists. Kept in review/voice_only.wav and made again
    only when the cue sheet or a take changes. (None, why) when there's no voice to place."""
    cue_sheet = film.path("timing/vo_cues.json")
    if not cue_sheet.is_file():
        return None, "there's no cue sheet (timing/vo_cues.json) to place the voice from"
    sfilm, vo, wav = deps.module("slate.sound.film"), deps.module("slate.sound.vo"), deps.module("slate.qa.wav")
    if sfilm is None or vo is None or wav is None:
        return None, "the sound module isn't installed, so the voice can't be placed"
    try:
        sf = sfilm.load(film.dir)
        cues = vo.cues(sf)
        if not cues:
            return None, "the cue sheet places no takes"
        takes = {str(c.get("file")): sf.resolve_media(str(c.get("file"))) for c in cues if c.get("file")}
        missing = [k for k, p in takes.items() if not Path(p).is_file()]
        if missing:
            return None, f"the take {missing[0]} isn't on disk"
        key = {"cues": jsonio.sha256(cue_sheet), "takes": {k: jsonio.sha256(p) for k, p in sorted(takes.items())},
               "sr": sf.sr, "samples": sf.n}
        out = film.path(VOICE_ONLY)
        record = out.with_suffix(".json")
        old = jsonio.read(record, default=None) if record.is_file() else None
        if out.is_file() and isinstance(old, dict) and old.get("key") == key:
            return out, None
        stem, placed = vo.render_stem(sf)
        out.parent.mkdir(parents=True, exist_ok=True)
        wav.write(out, stem, sf.sr, 24)
        jsonio.write_atomic(record, jsonio.mark_generated({"key": key, "placed": placed,
                                                           "why": "review cuts before the final mix exists"},
                                                          "slate review"))
        return out, None
    except (SlateError, OSError, ValueError, KeyError) as exc:
        return None, f"the voice couldn't be placed ({getattr(exc, 'message', exc)})"


def source_for(film: Film, *, prefer_master: bool = True) -> Source:
    """The best source for cuts right now (see the module docstring)."""
    notes = []
    if prefer_master:
        m = film.primary_master()
        if m and m["intact"] and film.master_current()[0]:
            return Source("master", [m["path"]], [0], None, media.has_audio(m["path"]))
        if m:
            notes.append("the master is older than the current renders or mix, so the cut is made from those")
    renders = film.act_renders()
    mix = film.mix()
    sound = "final mix"
    if mix is None:
        mix, why = voice_only(film)
        if mix is not None:
            sound = "voice only"
            notes.append("there's no final mix yet, so the cut plays the voice alone, each take placed on its cue")
        else:
            sound = "silent"
            notes.append(f"there's no final mix yet, so the cut is silent ({why})")
    return Source("renders", [r["path"] for r in renders], [r["frame_in"] for r in renders], mix, False, notes,
                  sound=sound)


def with_audio(film: Film, mix: Path, *, prefer_master: bool = True) -> Source:
    """The current picture (the master when it's current, else the act renders) with another sound: a proposed mix
    to review against it (`slate review --audio`)."""
    mix = Path(mix).expanduser()
    if not mix.is_absolute():
        mix = (Path.cwd() / mix) if (Path.cwd() / mix).is_file() else film.path(mix)
    if not mix.is_file():
        raise UsageError(f"There's no mix at {mix}.", hint="Give the WAV of the mix to review (--audio <file>).")
    if not media.has_audio(mix):
        raise UsageError(f"{mix.name} has no sound in it.")
    pic = source_for(film, prefer_master=prefer_master)
    notes = [n for n in pic.notes if "no final mix" not in n]
    notes.append(f"the sound is a proposed mix ({mix.name}), not the film's own")
    log.info(f"review: the picture from the {pic.kind}, the sound from {mix}")
    return Source(pic.kind, pic.files, pic.offsets, mix, False, notes, sound="proposed mix")


def _check_frames(film: Film, path: Path, what: str) -> int:
    """The cut holds exactly the film's frames, and its sound runs the picture's length (within a frame)."""
    got = media.frames(path)
    if got != film.total_frames:
        path.unlink(missing_ok=True)
        raise SlateError(f"The {what} came out at {got} frames; the film has {film.total_frames}.",
                         hint="slate render list shows whether an act render is short.")
    sound = media.audio_problem(path, film.clock.secs_of(1))
    if sound:
        path.unlink(missing_ok=True)
        raise SlateError(f"The {what} is wrong: {sound}.",
                         hint="Check the mix's length against the film (slate verify).")
    return got


def phone_cut(film: Film, out: Path, *, source: Source | None = None, burn: bool = True,
              right: str | None = None) -> dict:
    """A small H.264 cut for a phone, optionally with the timecode and shot id burned into a bar below."""
    media.require()
    cfg = settings.load()["review"]
    source = source or source_for(film)
    w, h = film.size
    pw, ph = media.fit_short_side(w, h, int(cfg.get("phone_short_side") or 360))
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(f".{out.stem}.part{out.suffix}")
    how = burnin.method() if burn else None
    with tempfile.TemporaryDirectory(prefix="slate-cut-") as tmp_s:
        tmp = Path(tmp_s)
        inputs, audio_map, n_in = source.ffmpeg_inputs(tmp)
        graph = f"[0:v]scale={pw}:{ph}:flags=lanczos,setsar=1"
        bar = 0
        if burn:
            bar = burnin.bar_height(ph, pw)
            graph += f",pad={pw}:{ph + bar}:0:0:black"
            if how == "drawtext":
                graph += "," + burnin.drawtext_chain(film, width=pw, y0=ph, bar=bar, right=right) + "[v]"
            else:
                pattern = burnin.pillow_strips(film, tmp / "labels", pw, bar, right=right)
                inputs += ["-framerate", film.rate, "-i", pattern]
                graph += f"[p];[p][{n_in}:v]overlay=0:{ph}:eof_action=pass:format=auto[v]"
        else:
            graph += "[v]"
        argv = ["ffmpeg", "-v", "error", "-nostdin", "-y", *inputs, "-filter_complex", graph, "-map", "[v]"]
        if audio_map:
            argv += ["-map", audio_map]
        argv += media.h264_args(cfg.get("encoder", "auto"), crf=cfg.get("phone_crf"), preset="medium",
                                maxrate=cfg.get("phone_maxrate"), bitrate=cfg.get("phone_maxrate"), profile="main")
        if audio_map:
            argv += ["-c:a", "aac", "-b:a", str(cfg.get("phone_audio_bitrate") or "96k"), "-ar", "48000"]
        argv += ["-movflags", "+faststart", "-f", "mp4", str(part)]
        media.run(argv, what="Making the phone cut", timeout=media.timeout_for(film.total_frames, 0.5))
    _check_frames(film, part, "phone cut")
    part.replace(out)
    out_info = {"file": out, "width": pw, "height": ph + bar, "picture": [pw, ph], "frames": film.total_frames,
                "source": source.describe(film), "encoder": media.h264_encoder(cfg.get("encoder", "auto")),
                "sound": bool(audio_map)}
    if burn:
        out_info["burnin"] = burnin.describe(film, how or "pillow")
    return out_info


def review_half(film: Film, out: Path, *, source: Source | None = None) -> dict:
    """The whole film at half its size, for a desktop look (no burn-in)."""
    media.require()
    cfg = settings.load()["review"]
    source = source or source_for(film)
    w, h = media.half_size(*film.size)
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(f".{out.stem}.part{out.suffix}")
    with tempfile.TemporaryDirectory(prefix="slate-cut-") as tmp_s:
        inputs, audio_map, _n = source.ffmpeg_inputs(Path(tmp_s))
        argv = ["ffmpeg", "-v", "error", "-nostdin", "-y", *inputs, "-vf", f"scale={w}:{h}:flags=lanczos,setsar=1",
                "-map", "0:v:0"]
        if audio_map:
            argv += ["-map", audio_map]
        argv += media.h264_args(cfg.get("encoder", "auto"), crf=cfg.get("half_crf"), preset="medium",
                                maxrate=cfg.get("half_maxrate"), bitrate=cfg.get("half_maxrate"), profile="high")
        if audio_map:
            argv += ["-c:a", "aac", "-b:a", str(cfg.get("half_audio_bitrate") or "192k"), "-ar", "48000"]
        argv += ["-movflags", "+faststart", "-f", "mp4", str(part)]
        media.run(argv, what="Making the review cut", timeout=media.timeout_for(film.total_frames, 0.5))
    _check_frames(film, part, "review cut")
    part.replace(out)
    return {"file": out, "width": w, "height": h, "frames": film.total_frames, "source": source.describe(film),
            "sound": bool(audio_map)}


# ---------------------------------------------------------------------------------------------------------------
# Platform files (a format's encode.files: the App Store preview's H.264, the hero loop's MP4 and WebM)

CONTAINERS = {"mp4": "mp4", "m4v": "mp4", "mov": "mov", "webm": "webm"}
ENCODERS = {"h264": "libx264", "vp9": "libvpx-vp9", "av1": "libsvtav1"}


def _platform_video_args(video: dict) -> list[str]:
    """Encoder arguments for one platform file's picture, from its spec (sizes and rates always the film's own)."""
    codec = video.get("codec")
    encoder = ENCODERS.get(codec)
    if encoder is None:
        raise SlateError(f"No encoder for a {codec!r} platform file; the formats know h264, vp9 and av1.")
    if not media.has_encoder(encoder):
        raise MissingPrerequisite(f"This ffmpeg has no {encoder}, which the platform's {codec} file needs.",
                                  hint="Install an ffmpeg built with it (`brew install ffmpeg`), or run `slate doctor`.")
    args = ["-c:v", encoder]
    if codec == "h264":
        args += ["-preset", str(video.get("preset") or "medium")]
        args += ["-b:v", str(video["bitrate"])] if video.get("bitrate") else ["-crf", str(video.get("crf", 20))]
        if video.get("maxrate"):
            args += ["-maxrate", str(video["maxrate"]), "-bufsize", str(video.get("bufsize") or video["maxrate"])]
        if video.get("profile"):
            args += ["-profile:v", str(video["profile"])]
        if video.get("level"):
            args += ["-level:v", str(video["level"])]
    elif codec == "vp9":
        args += (["-b:v", str(video["bitrate"])] if video.get("bitrate")
                 else ["-b:v", "0", "-crf", str(video.get("crf", 33))])
        args += ["-deadline", "good", "-cpu-used", "2", "-row-mt", "1"]
    else:
        args += ["-crf", str(video.get("crf", 35)), "-preset", str(video.get("preset") or "8")]
    return args + ["-pix_fmt", str(video.get("pix_fmt") or "yuv420p"), *media.BT709]


def platform_file(film: Film, spec: dict, out: Path, *, source: Source | None = None) -> dict:
    """One platform file, encoded from the source (the master, in a delivery) to the format's spec: the film's own
    size and clock, exactly its frames, and sound only when the spec asks for it (a hero loop carries no audio track
    at all, so browsers may autoplay it)."""
    media.require()
    source = source or source_for(film)
    video, sound = spec.get("video") or {}, spec.get("audio")
    container = CONTAINERS.get(str(spec.get("container")))
    if container is None:
        raise SlateError(f"Unknown container {spec.get('container')!r} for {spec.get('id')}.")
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(f".{out.stem}.part{out.suffix}")
    with tempfile.TemporaryDirectory(prefix="slate-platform-") as tmp_s:
        inputs, audio_map, _n = source.ffmpeg_inputs(Path(tmp_s))
        keep_sound = bool(sound) and audio_map is not None
        argv = ["ffmpeg", "-v", "error", "-nostdin", "-y", *inputs, "-map", "0:v:0"]
        argv += ["-map", audio_map] if keep_sound else []
        argv += _platform_video_args(video)
        if keep_sound:
            argv += ["-c:a", "aac", "-b:a", str(sound.get("bitrate") or "256k"), "-ac", str(sound.get("channels") or 2),
                     "-ar", str(sound.get("rate") or 48000)]
        else:
            argv += ["-an"]
        if spec.get("faststart") and container in ("mp4", "mov"):
            argv += ["-movflags", "+faststart"]
        argv += ["-f", container, str(part)]
        media.run(argv, what=f"Encoding {spec.get('label') or spec.get('id')}",
                  timeout=media.timeout_for(film.total_frames, 1.0))
    _check_frames(film, part, str(spec.get("label") or spec.get("id")))
    part.replace(out)
    return {"file": out, "id": spec.get("id"), "label": spec.get("label"), "container": spec.get("container"),
            "codec": video.get("codec"), "frames": film.total_frames, "sound": keep_sound,
            "bytes": out.stat().st_size}


def thumbnail_frames(film: Film) -> list[int]:
    """Film frames for the thumbnails: film.json "deliver": {"thumbnails": [...]} (frames or shot ids), else the
    middle of each act's longest shot, at most [finish.deliver] thumbnails of them, spread across the film."""
    wanted = ((film.data.get("deliver") or {}).get("thumbnails") if isinstance(film.data.get("deliver"), dict)
              else None)
    by_id = {str(s.get("id")): s for s in film.shots}
    total = film.total_frames
    frames: list[int] = []
    if isinstance(wanted, list) and wanted:
        for w in wanted:
            if isinstance(w, int) and 0 <= w < total:
                frames.append(w)
            elif str(w) in by_id:
                s = by_id[str(w)]
                frames.append((int(s["frame_in"]) + int(s["frame_out"]) - 1) // 2)
        return sorted(set(frames))
    for a in film.acts:
        members = [by_id[i] for i in a.get("shots") or [] if i in by_id]
        if not members:
            continue
        s = max(members, key=lambda x: int(x["frame_out"]) - int(x["frame_in"]))
        frames.append((int(s["frame_in"]) + int(s["frame_out"]) - 1) // 2)
    limit = max(1, int(settings.load().get("deliver", {}).get("thumbnails") or 4))
    if len(frames) > limit:
        step = (len(frames) - 1) / (limit - 1) if limit > 1 else 0
        frames = [frames[round(i * step)] for i in range(limit)] if limit > 1 else [frames[len(frames) // 2]]
    return sorted(set(frames)) or [total // 2]


def thumbnails(film: Film, out_dir: Path, *, source: Source | None = None,
               frames: list[int] | None = None) -> list[dict]:
    """Full-size PNG stills at the chosen film frames (read exactly by frame number)."""
    from PIL import Image  # noqa: PLC0415

    qa_media = deps.module("slate.qa.media")
    if qa_media is None:
        raise MissingPrerequisite("Thumbnails are read through slate.qa, which isn't installed.")
    source = source or source_for(film)
    frames = frames or thumbnail_frames(film)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_file: dict[Path, list[tuple[int, int]]] = {}
    for f in frames:
        path, local = source.locate(f)
        by_file.setdefault(path, []).append((f, local))
    out = []
    for path, pairs in by_file.items():
        got = qa_media.read_frames(path, [local for _f, local in pairs], pix_fmt="rgb24")
        for f, local in pairs:
            arr = got.get(local)
            if arr is None:
                raise SlateError(f"Couldn't read frame {f} from {film.rel(path)} for a thumbnail.")
            shot = film.shot_at(f)
            dest = out_dir / f"{film.id}_f{f:06d}.png"
            Image.fromarray(arr).save(dest)
            out.append({"file": dest, "frame": f, "shot": str(shot.get("id")) if shot else None,
                        "timecode": film.timecode(f)})
    return sorted(out, key=lambda x: x["frame"])
