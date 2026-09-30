"""The real media backend on tiny synthetic videos: frame counts, decoded-pixel reads, joins with their proofs, and
splices with frame hashes and PSNR at the seams. Skipped when ffmpeg isn't installed.

Everything runs at 24000/1001 fps, so the mid-frame seeks are exercised at a fractional rate.
"""

import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from slate import jsonio, procs
from slate.clock import Clock
from slate.errors import Refused
from slate.render import joins, splice
from slate.render import media as media_mod
from slate.render.chunks import Chunk

pytestmark = pytest.mark.skipif(not (procs.which("ffmpeg") and procs.which("ffprobe")),
                                reason="ffmpeg isn't installed")

W, H = 64, 36
FPS = (24000, 1001)


def rgb_frame(i: int, variant: int = 0) -> np.ndarray:
    """A frame with a bar that moves 3 px a frame, a tint that changes every frame and a moving line."""
    img = np.zeros((H, W, 3), np.uint16)
    x = (i * 3) % (W - 4)
    img[:, x:x + 4, 0] = 60000
    img[:, :, 1] = (i * 997 + variant * 20000) % 65535
    img[i % H, :, 2] = 40000
    return img


def write_video(path: Path, frames: list[np.ndarray], codec: str) -> Path:
    args = [procs.require("ffmpeg"), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb48le", "-s", f"{W}x{H}",
            "-framerate", f"{FPS[0]}/{FPS[1]}", "-i", "-"]
    if codec == "lossless":
        args += ["-c:v", "libx264", "-qp", "0", "-preset", "veryfast", "-pix_fmt", "yuv422p10le"]
    else:
        args += media_mod.PRORES_HQ
    args += media_mod.BT709 + [str(path)]
    path.parent.mkdir(parents=True, exist_ok=True)
    procs.run(args, input=b"".join(f.tobytes() for f in frames), text=False, check=True, timeout=120)
    return path


@pytest.fixture
def real_media(monkeypatch, tmp_path):
    """The ffmpeg backend, with film.json read straight from disk (no state module needed)."""
    monkeypatch.setattr(media_mod, "_state", {"backend": None})
    state = types.ModuleType("slate.state")
    state.__path__ = []
    film_mod = types.ModuleType("slate.state.film")
    film_mod.load = lambda d: json.loads((Path(d) / "film.json").read_text())
    monkeypatch.setitem(sys.modules, "slate.state", state)
    monkeypatch.setitem(sys.modules, "slate.state.film", film_mod)
    film_dir = tmp_path / "film"
    (film_dir / "renders").mkdir(parents=True)
    (film_dir / "film.json").write_text(json.dumps({"fps_num": FPS[0], "fps_den": FPS[1], "width": W, "height": H}))
    return media_mod.backend(), film_dir


def test_count_and_read_decoded_frames(real_media, tmp_path):
    m, _film = real_media
    lossless = write_video(tmp_path / "a.mkv", [rgb_frame(i) for i in range(12)], "lossless")
    prores = write_video(tmp_path / "a.mov", [rgb_frame(i) for i in range(12)], "prores")
    assert m.count_frames(lossless) == 12 and m.count_frames(prores) == 12
    assert m.count_frames(tmp_path / "missing.mov") == 0
    a = m.read_frames(lossless, [0, 5, 11])
    b = m.read_frames(prores, [5])
    assert sorted(a) == [0, 5, 11] and a[5].dtype == np.uint16
    # ProRes against the lossless frame it came from: very close, not identical; a neighbour is far away
    assert 40 < media_mod.psnr(a[5], b[5]) < float("inf")
    assert media_mod.psnr(a[5], m.read_frames(prores, [6])[6]) < 35
    assert m.is_intra(prores) and not m.is_intra(lossless)
    with pytest.raises(Refused, match="shorter"):
        m.read_frames(prores, [12])


def _chunks(tmp_path: Path, bounds, codec: str) -> list[Chunk]:
    ext = ".mkv" if codec == "lossless" else ".mov"
    out = []
    for k, (a, b) in enumerate(bounds):
        path = write_video(tmp_path / f"c{k}{ext}", [rgb_frame(i) for i in range(a, b + 1)], codec)
        out.append(Chunk(k, a, b, path))
    return out


def test_lossless_chunks_join_into_one_prores_encode_with_proof(real_media, tmp_path):
    m, _film = real_media
    parts = _chunks(tmp_path, [(0, 9), (10, 19), (20, 24)], "lossless")
    out = tmp_path / "act.mov"
    proof = joins.assemble(parts, out, codec="lossless", media=m)
    assert proof["status"] == "PASS", proof["problems"]
    assert proof["method"] == "psnr" and proof["frames"] == 25
    assert all(j["match"] and j["best_offset"] == 0 for j in proof["joins"])
    assert m.probe(out)["codec_name"] == "prores"
    # the same pixels as encoding the whole sequence to ProRes in one go
    direct = write_video(tmp_path / "direct.mov", [rgb_frame(i) for i in range(25)], "prores")
    got, want = m.read_frames(out, range(25)), m.read_frames(direct, range(25))
    assert all(np.array_equal(got[i], want[i]) for i in range(25))


def test_a_frame_lost_at_a_join_fails_the_proof(real_media, tmp_path, monkeypatch):
    m, _film = real_media
    parts = _chunks(tmp_path, [(0, 9), (10, 19)], "lossless")
    real_encode = m.join_encode

    def lossy_join(paths, out, *, frames=None):
        # drop the second chunk's first frame and double its last, so the count still adds up
        broken = [rgb_frame(i) for i in range(0, 10)] + [rgb_frame(i) for i in range(11, 20)] + [rgb_frame(19)]
        src = write_video(tmp_path / "broken.mkv", broken, "lossless")
        real_encode([src], out, frames=frames)

    monkeypatch.setattr(m, "join_encode", lossy_join)
    proof = joins.assemble(parts, tmp_path / "act.mov", codec="lossless", media=m)
    assert proof["frames"] == 20 and proof["status"] == "FAIL"
    assert any("chunk 1 first" in p for p in proof["problems"])


def test_prores_chunks_join_by_stream_copy_with_hash_proof(real_media, tmp_path):
    m, _film = real_media
    parts = _chunks(tmp_path, [(0, 7), (8, 15)], "prores")
    proof = joins.assemble(parts, tmp_path / "act.mov", codec="prores", media=m)
    assert proof["status"] == "PASS" and proof["method"] == "hash"
    assert all(j["match"] for j in proof["joins"]) and len(proof["joins"]) == 4


def _base_and_patch(tmp_path, patch_frames, variant_frames=()):
    base = write_video(tmp_path / "film" / "renders" / "act1.mov", [rgb_frame(i) for i in range(30)], "prores")
    patch = write_video(tmp_path / "patch.mov",
                        [rgb_frame(i, 1 if i in variant_frames else 0) for i in patch_frames], "prores")
    return base, patch


def test_splice_replaces_the_range_and_proves_both_joins(real_media, tmp_path):
    m, film_dir = real_media
    base, patch = _base_and_patch(tmp_path, range(10, 20), variant_frames=range(12, 18))
    before = m.read_frames(base, range(30))
    patch_frames = m.read_frames(patch, range(10))
    sha_before = jsonio.sha256(base)
    proof = splice.splice(film_dir, base, patch, 10, 19)
    assert proof["status"] == "PASS" and proof["gate"]["status"] == "PASS"
    assert [(j["frame"], j["match"]) for j in proof["joins"]] == [(10, True), (19, True), (9, True), (20, True)]
    assert [e["best_offset"] for e in proof["edges"]] == [0, 0]
    assert proof["sha256_before"] == sha_before != proof["sha256_after"] == jsonio.sha256(base)
    after = m.read_frames(base, range(30))
    assert m.count_frames(base) == 30
    assert all(np.array_equal(after[i], before[i]) for i in list(range(10)) + list(range(20, 30)))
    assert all(np.array_equal(after[10 + k], patch_frames[k]) for k in range(10))
    assert not splice.pre_splice_path(base).exists()
    assert not list(base.parent.glob(".splice-*"))


def test_splice_refuses_a_patch_rendered_one_frame_off(real_media, tmp_path):
    m, film_dir = real_media
    base, patch = _base_and_patch(tmp_path, range(11, 21))  # frames 11-20, offered as 10-19
    sha = jsonio.sha256(base)
    with pytest.raises(Refused, match=r"looks \+1 frame\(s\) off"):
        splice.splice(film_dir, base, patch, 10, 19)
    assert jsonio.sha256(base) == sha
    assert not splice.pre_splice_path(base).exists() and not list(base.parent.glob(".splice-*"))


def test_splice_refuses_a_patch_of_the_wrong_length(real_media, tmp_path):
    _m, film_dir = real_media
    base, patch = _base_and_patch(tmp_path, range(10, 19))
    with pytest.raises(Refused, match="has 9 frames; frames 10–19 need 10"):
        splice.splice(film_dir, base, patch, 10, 19)


def test_splice_refuses_what_it_cant_cut_exactly(real_media, tmp_path):
    _m, film_dir = real_media
    base = write_video(tmp_path / "film" / "renders" / "act1.mkv", [rgb_frame(i) for i in range(20)], "lossless")
    patch = write_video(tmp_path / "patch.mov", [rgb_frame(i) for i in range(5, 10)], "prores")
    with pytest.raises(Refused, match="isn't intra-only"):
        splice.splice(film_dir, base, patch, 5, 9)


def test_splice_at_the_very_start_and_end(real_media, tmp_path):
    m, film_dir = real_media
    base, patch = _base_and_patch(tmp_path, range(0, 6), variant_frames=range(1, 5))
    proof = splice.splice(film_dir, base, patch, 0, 5)
    assert proof["status"] == "PASS" and [j["frame"] for j in proof["joins"]] == [0, 5, 6]
    tail = write_video(tmp_path / "tail.mov", [rgb_frame(i, 1 if i in range(25, 29) else 0) for i in range(24, 30)],
                       "prores")
    proof = splice.splice(film_dir, base, tail, 24, 29)
    assert proof["status"] == "PASS" and [j["frame"] for j in proof["joins"]] == [24, 29, 23]
    assert m.count_frames(base) == 30


def test_cut_copy_lands_on_exact_frames_at_a_fractional_rate(real_media, tmp_path):
    m, _film = real_media
    src = write_video(tmp_path / "src.mov", [rgb_frame(i) for i in range(30)], "prores")
    clock = Clock(*FPS)
    m.cut_copy(src, 0, 11, tmp_path / "head.mov", clock)
    m.cut_copy(src, 17, None, tmp_path / "tail.mov", clock)
    assert m.count_frames(tmp_path / "head.mov") == 11 and m.count_frames(tmp_path / "tail.mov") == 13
    whole = m.read_frames(src, [10, 17, 29])
    assert np.array_equal(m.read_frames(tmp_path / "head.mov", [10])[10], whole[10])
    tail = m.read_frames(tmp_path / "tail.mov", [0, 12])
    assert np.array_equal(tail[0], whole[17]) and np.array_equal(tail[12], whole[29])


def test_ffprobe_output_listing_the_stream_twice_is_read_once(monkeypatch, tmp_path):
    """After Effects' QuickTime files list the video stream twice (a stream group); the films' frame counts learned to
    take the first answer. No ffmpeg needed: ffprobe's answer is stood in."""
    from types import SimpleNamespace

    f = tmp_path / "ae.mov"
    f.write_bytes(b"not really a movie")
    answers = {"count": "240\n240\n", "probe": "codec_name=prores\nwidth=64\nheight=36\npix_fmt=yuv422p10le\n"
                                              "r_frame_rate=25/1\ncodec_name=none\nwidth=0\nheight=0\n"}

    def fake_run(argv, **kw):
        out = answers["count"] if "-count_frames" in argv else answers["probe"]
        return SimpleNamespace(stdout=out, stderr="", returncode=0)

    monkeypatch.setattr(media_mod.procs, "run", fake_run)
    monkeypatch.setattr(media_mod.procs, "require", lambda tool, hint=None: tool)
    m = media_mod.FFmpegMedia()
    assert m.count_frames(f) == 240
    info = m.probe(f)
    assert info["codec_name"] == "prores" and info["width"] == 64 and m.is_intra(f)
