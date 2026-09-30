"""Delivery masters from a film's After Effects project, rendered by aerender in chunks (masters.sh, generalized).

`slate ae build --go` builds the project and queues the master comp as chunks: one render-queue item per chunk,
each with two stock output modules (H.264 and "High Quality with Alpha", ProRes 4444), so one aerender pass writes
both masters' frames. A stock output module's codec can't be set by script and Slate never adds templates to the
person's preferences, so the ProRes 422 HQ master is conformed from the 4444 frames by ffmpeg. `render` then:

- refuses without the person's go, for autonomous films, and with After Effects open (exit 5: each aerender is a
  second AE, swap grows with the GUI open, and AE rewrites its prefs file when it quits);
- switches AE's disk cache off for the run and always puts it back, even on failure or Ctrl-C (it once filled 60 GB),
  through slate.ae.prefs, the only way Slate edits that file;
- renders each chunk in a fresh aerender (`-rqindex k -mem_usage 25 50`; one long aerender grew until the Mac
  swapped and aborted), up to three tries, and skips a chunk whose outputs already hold the right frame count, so a
  stopped run resumes where it stopped;
- watches free space under the output folder and stops its own aerender (never anyone else's) under 4 GB;
- conforms each 4444 chunk to ProRes 422 HQ (prores_ks profile 3, 10-bit 4:2:2, BT.709 tags) between passes, never
  alongside aerender, and deletes the 4444;
- joins the chunks by stream copy (each H.264 chunk starts on an IDR frame; ProRes is intra-only) and muxes the final
  mix once: AAC 320 kb/s 48 kHz for H.264, PCM 24-bit 48 kHz for ProRes. Never `-shortest`: the mix is a few
  microseconds shorter than the picture, and -shortest dropped the last four frames once;
- checks every frame count exactly: each chunk, each conform, each master equals the film's total frames.

Frame counts come from `ffprobe -count_packets`, taking the first number: for AE's QuickTime output ffprobe also
lists the stream inside a stream group, so the count comes out twice.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from slate import config, jsonio, log, paths, procs
from slate.ae import driver, guard, prefs
from slate.errors import MissingPrerequisite, Refused, SlateError

MIN_FREE_GB = 4.0
TRIES = 3
RETRY_SLEEP_S = 10.0

Runner = Callable[..., Any]


# ---------------------------------------------------------------------------------------------------------------
# Small media helpers (shared with slate.ae.acts and slate.ae.smoke)


def frames(path: str | os.PathLike, *, runner: Runner | None = None) -> int | None:
    """A video's frame count (packets counted), taking the first number ffprobe prints; None when unreadable."""
    p = Path(path)
    if not p.is_file():
        return None
    run = runner or procs.run
    proc = run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                "stream=nb_read_packets", "-of", "csv=p=0", str(p)], timeout=900)
    for line in (getattr(proc, "stdout", "") or "").splitlines():
        token = line.strip().rstrip(",")
        if re.fullmatch(r"\d+", token):
            return int(token)
    return None


def conform_hq(src: str | os.PathLike, dst: str | os.PathLike, *, runner: Runner | None = None) -> Path:
    """ProRes 4444 (or anything) -> ProRes 422 HQ, 10-bit 4:2:2, tagged BT.709, video only."""
    run = runner or procs.run
    argv = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-c:v", "prores_ks", "-profile:v", "3",
            "-vendor", "apl0", "-pix_fmt", "yuv422p10le", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-colorspace", "bt709", str(dst)]
    proc = run(argv, timeout=3600)
    if getattr(proc, "returncode", 1) != 0:
        raise SlateError(f"ffmpeg couldn't conform {Path(src).name} to ProRes 422 HQ: {(proc.stderr or '')[-400:]}")
    return Path(dst)


def join(parts: list[Path], out: Path, *, mix: Path | None, kind: str, runner: Runner | None = None) -> Path:
    """Join chunks by stream copy and mux the mix once (never -shortest). kind: "h264" or "prores"."""
    run = runner or procs.run
    listing = out.with_suffix(out.suffix + ".parts.txt")
    listing.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
    argv = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing)]
    if mix is not None:
        argv += ["-i", str(mix), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy"]
        argv += (["-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-movflags", "+faststart"] if kind == "h264" else
                 ["-c:a", "pcm_s24le", "-ar", "48000"])
    else:
        argv += ["-map", "0:v:0", "-c:v", "copy"] + (["-movflags", "+faststart"] if kind == "h264" else [])
    argv.append(str(out))
    assert "-shortest" not in argv  # never: it drops the picture's last frames to match a shorter mix
    proc = run(argv, timeout=7200)
    listing.unlink(missing_ok=True)
    if getattr(proc, "returncode", 1) != 0:
        raise SlateError(f"ffmpeg couldn't join the {kind} master: {(proc.stderr or '')[-400:]}")
    return out


# ---------------------------------------------------------------------------------------------------------------
# The disk watchdog


class Watchdog:
    """Stops this render's own aerender when free space under `folder` drops below `min_free_gb`.

    The film pipeline's watchdog ran `pkill -f aerender`, which also killed anyone else's aerender on the Mac; this
    one only signals processes whose command line holds this film's project path.
    """

    def __init__(self, folder: Path, aep: Path, *, min_free_gb: float = MIN_FREE_GB, every_s: float = 5.0,
                 free_bytes: Callable[[Path], int] | None = None, killer: Callable[[int], None] | None = None):
        self.folder, self.aep = Path(folder), Path(aep)
        self.min_free = min_free_gb * (1 << 30)
        self.every_s = every_s
        self.free_bytes = free_bytes or procs.disk_free_bytes
        self.killer = killer or (lambda pid: os.kill(pid, signal.SIGTERM))
        self.tripped = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def check(self) -> bool:
        if self.free_bytes(self.folder) < self.min_free:
            if not self.tripped:
                log.warn(f"under {self.min_free / (1 << 30):.0f} GB free under {self.folder}: stopping this render's "
                         "aerender")
            self.tripped = True
            for pid in procs.pgrep(str(self.aep), full=True):
                if pid != os.getpid():
                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        self.killer(pid)
        return self.tripped

    def __enter__(self) -> "Watchdog":
        def loop() -> None:
            while not self._stop.wait(self.every_s):
                self.check()
        self.check()
        self._thread = threading.Thread(target=loop, name="slate-ae-disk-watchdog", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)


@contextlib.contextmanager
def _signals_raise() -> Iterator[None]:
    """SIGTERM and SIGHUP become KeyboardInterrupt while masters run, so the cache is always put back."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def handler(signum, _frame):
        raise KeyboardInterrupt(f"signal {signum}")

    saved = {s: signal.signal(s, handler) for s in (signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for s, h in saved.items():
            signal.signal(s, h)


# ---------------------------------------------------------------------------------------------------------------
# Queue and render


def _read_spec(film_dir: Path) -> dict:
    from slate.ae import spec as ae_spec  # noqa: PLC0415

    return ae_spec.read_spec(film_dir)


def check_plan(plan: list[dict], total: int) -> None:
    """The chunks must tile the film exactly: contiguous, in order, every frame once."""
    expect = 0
    for c in plan:
        if c["start"] != expect or c["frames"] != c["end"] - c["start"] + 1 or c["frames"] <= 0:
            raise Refused(f"The masters plan doesn't tile the film: chunk {c['index']} is frames "
                          f"{c['start']}–{c['end']}.", hint="Re-queue: slate ae masters --requeue --go")
        expect = c["end"] + 1
    if expect != total:
        raise Refused(f"The masters plan covers {expect} frames; the film has {total}.",
                      hint="Re-queue: slate ae masters --requeue --go")


def queue(film_dir: str | os.PathLike, *, go: bool = False, chunks: int | None = None,
          runner: Runner | None = None) -> dict:
    """Re-queue the master comp as chunks in the film's existing project (opens AE). Nothing is rebuilt.

    After it runs, save and quit After Effects, then render with `slate ae masters --go`.
    """
    from slate.ae import spec as ae_spec  # noqa: PLC0415

    film_dir = Path(film_dir)
    guard.person_start(go, "Queueing the masters in After Effects", film_dir=film_dir, action="ae_master")
    spec = _read_spec(film_dir)
    if chunks:
        spec["masters"] = spec.get("masters") or ae_spec.masters_section(film_dir, {}, spec["film"]["total_frames"], chunks)
        spec["masters"]["chunks"] = ae_spec.chunk_plan(spec["film"]["total_frames"], chunks)
    if not spec.get("masters"):
        raise Refused("This film's build spec has no masters plan.", hint="slate ae build --go")
    spec_path = ae_spec.write_spec(film_dir, spec)
    res = driver.expect_ok(driver.run_jsx(driver.script("queue_masters.jsx"), args={"spec": str(spec_path)},
                                          runner=runner), "Queueing the masters")
    record = {"queued_at": _now(), "chunks": spec["masters"]["chunks"], "queue": res["report"].get("queue"),
              "aep": spec["paths"]["aep"]}
    aep = Path(spec["paths"]["aep"])
    if aep.is_file():
        record["aep_sha256"] = jsonio.sha256(aep)
    jsonio.write_atomic(film_dir / "ae" / "masters_queue.json", record)
    return record


def render(film_dir: str | os.PathLike, *, go: bool = False, runner: Runner | None = None,
           chunks: int | None = None, keep_chunks: bool = False, min_free_gb: float = MIN_FREE_GB,
           allow_silent: bool = False, cache_off: bool = True, sleep: Callable[[float], None] = time.sleep,
           free_bytes: Callable[[Path], int] | None = None) -> dict:
    """Render the film's delivery masters from its AE project. Returns what was made and how (see the docstring)."""
    film_dir = Path(film_dir)
    guard.person_start(go, "Rendering the masters with aerender", film_dir=film_dir, action="ae_master")
    guard.require_ae_closed("rendering the masters")
    spec = _read_spec(film_dir)
    m = spec.get("masters")
    if not m:
        raise Refused("This film's project has no masters queue.", hint="slate ae build --go")
    plan = m["chunks"]
    total = int(spec["film"]["total_frames"])
    if chunks and len(plan) != int(chunks):
        raise Refused(f"The project is queued in {len(plan)} chunks, not {chunks}.",
                      hint=f"Re-queue first: slate ae masters --requeue --chunks {chunks} --go, then save and quit "
                           "After Effects and run this again.")
    check_plan(plan, total)
    aep = Path(spec["paths"]["aep"])
    if not aep.is_file():
        raise MissingPrerequisite(f"The film's AE project isn't built yet ({aep}).", hint="slate ae build --go")
    queued = jsonio.read(film_dir / "ae" / "masters_queue.json", default=None)
    if queued and queued.get("aep_sha256") and queued["aep_sha256"] != jsonio.sha256(aep):
        log.warn("The project changed since the masters were queued; the frame counts below will prove the queue "
                 "still matches (re-queue with --requeue if you changed the master comp).")
    mix = Path(spec["audio"]["file"]) if spec.get("audio") else None
    if mix is None and not allow_silent:
        raise MissingPrerequisite("The film has no final mix yet, and a master without its mix isn't deliverable.",
                                  hint="slate mix (or pass --silent for a picture-only master)")
    run = runner or procs.run
    aerender = driver.aerender_path()
    chunk_dir = paths.ensure_dir(Path(m["dir"]))
    final_dir = paths.ensure_dir(film_dir / "masters")
    fid = spec["film"]["id"]
    out_h264 = final_dir / f"{fid}_H264.mp4"
    out_hq = final_dir / f"{fid}_ProRes422HQ.mov"
    per_frame_s = float((config.load().get("ae") or {}).get("seconds_per_frame_timeout") or 5.0)
    started = time.time()
    done: list[dict] = []
    cm = prefs.disk_cache_off() if cache_off else contextlib.nullcontext({"toggled": False})
    with _signals_raise(), cm as cache, Watchdog(chunk_dir, aep, min_free_gb=min_free_gb, free_bytes=free_bytes) as wd:
        for c in plan:
            k, want = int(c["index"]), int(c["frames"])
            h = chunk_dir / m["primary_name"].replace("{k}", str(k))
            a = chunk_dir / m["alpha_name"].replace("{k}", str(k))
            q = chunk_dir / f"hq_c{k}.mov"
            if frames(h, runner=run) == want and frames(q, runner=run) == want:
                log.info(f"chunk {k}: already complete ({want} frames)")
                done.append({"chunk": k, "frames": want, "tries": 0, "resumed": True})
                continue
            ok, tries = False, 0
            log_path = chunk_dir / f"c{k}.log"
            for tries in range(1, TRIES + 1):
                for p in (h, a, q):
                    p.unlink(missing_ok=True)
                log.info(f"chunk {k}/{len(plan)}: frames {c['start']}–{c['end']} (try {tries})")
                argv = [aerender, "-project", str(aep), "-rqindex", str(plan.index(c) + 1), "-mem_usage", "25", "50",
                        "-v", "ERRORS_AND_PROGRESS"]
                with driver.ae_lock(bridge=False, owner=f"slate-ae-masters-{fid}"):
                    proc = run(argv, timeout=max(1800.0, want * per_frame_s))
                log_path.write_text((getattr(proc, "stdout", "") or "") + (getattr(proc, "stderr", "") or ""),
                                    encoding="utf-8")
                if wd.tripped:
                    raise SlateError(f"Stopped: under {min_free_gb:g} GB free under {chunk_dir}.",
                                     hint="Free some space, then run this again; finished chunks are kept.")
                gh, ga = frames(h, runner=run), frames(a, runner=run)
                if gh == want and ga == want:
                    ok = True
                    break
                log.warn(f"chunk {k}: H.264 {gh if gh is not None else 'nothing'}, 4444 "
                         f"{ga if ga is not None else 'nothing'} of {want} frames; retrying")
                if tries < TRIES:
                    sleep(RETRY_SLEEP_S)
            if not ok:
                raise SlateError(f"Chunk {k} failed {TRIES} times.", hint=f"Its log is {log_path}.")
            conform_hq(a, q, runner=run)
            if frames(q, runner=run) != want:
                raise SlateError(f"Chunk {k}'s ProRes 422 HQ conform came out short.")
            a.unlink(missing_ok=True)
            done.append({"chunk": k, "frames": want, "tries": tries, "resumed": False})
        parts_h = [chunk_dir / m["primary_name"].replace("{k}", str(c["index"])) for c in plan]
        parts_q = [chunk_dir / f"hq_c{c['index']}.mov" for c in plan]
        log.info("joining the H.264 master")
        join(parts_h, out_h264, mix=mix, kind="h264", runner=run)
        got_h = frames(out_h264, runner=run)
        if got_h != total:
            raise SlateError(f"The H.264 master is {got_h} frames, not {total}.")
        log.info("joining the ProRes 422 HQ master")
        join(parts_q, out_hq, mix=mix, kind="prores", runner=run)
        got_q = frames(out_hq, runner=run)
        if got_q != total:
            raise SlateError(f"The ProRes 422 HQ master is {got_q} frames, not {total}.")
    if not keep_chunks:
        for p in parts_h + parts_q:
            p.unlink(missing_ok=True)
    result = {
        "film": fid, "frames": total, "h264": str(out_h264), "prores_422_hq": str(out_hq),
        "sha256": {"h264": jsonio.sha256(out_h264), "prores_422_hq": jsonio.sha256(out_hq)},
        "chunks": done, "mix": str(mix) if mix else None, "aep": str(aep), "aep_sha256": jsonio.sha256(aep),
        "cache": cache, "working_space": spec["project"]["working_space"], "made_at": _now(),
        "seconds": round(time.time() - started, 1),
        "checks": {"frames": "every chunk, conform and master matched the plan exactly"},
    }
    jsonio.write_atomic(final_dir / "masters.json", jsonio.mark_generated(result, "slate ae masters"))
    from slate.ae import provenance  # noqa: PLC0415

    provenance.note(film_dir, [out_h264, out_hq], producer="slate ae masters",
                    inputs=[aep] + ([mix] if mix else []))
    return result


def _now() -> str:
    import datetime as _dt  # noqa: PLC0415

    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")
