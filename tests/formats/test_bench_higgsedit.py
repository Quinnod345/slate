"""`slate bench higgsedit`: the protocol, the refusal to run the unfunded Higgsedit arm, the quality measurement on
real (synthetic) video, and the decision rule on every path: keep, drop and not decided."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import formats_clips as clips
from slate import cli, jsonio
from slate.bench import higgsedit as bench
from slate.bench import quality
from slate.errors import MissingPrerequisite, SpendRefused, UsageError

FPS = 24


@pytest.fixture(scope="module")
def media_dir(tmp_path_factory):
    folder = tmp_path_factory.mktemp("bench-media")
    master = clips.pattern_master(folder, seconds=2.0, fps=FPS)
    clips.excerpt(master, folder / "same.mkv", 0, FPS)
    clips.excerpt(master, folder / "noisy.mkv", 0, FPS, vf="noise=alls=80:allf=t")
    clips.excerpt(master, folder / "short.mkv", 0, FPS - 4)
    clips.excerpt(master, folder / "vertical.mkv", 0, FPS, vf="crop=50:90,setsar=1")
    return folder


def _film(slate_home, media_dir) -> Path:
    film = slate_home["films"] / "demo-film"
    (film / "masters").mkdir(parents=True)
    (film / "film.json").write_text(json.dumps({"id": "demo-film", "format": "brand-film", "fps_num": FPS, "fps_den": 1,
                                                "width": 160, "height": 90}))
    (film / "masters" / "demo-film_master.mkv").write_bytes((media_dir / "master.mkv").read_bytes())
    return film


# ---------------------------------------------------------------------------------------------------------------
# Quality


def test_an_identical_excerpt_scores_perfect_and_noise_scores_lower(media_dir):
    same = quality.compare(media_dir / "same.mkv", media_dir / "master.mkv", ranges=[[0, FPS]])
    assert same["frames_match"] and same["frames_compared"] == FPS and same["reference"] == "same size"
    assert same["ssim"]["mean"] == 1.0 and same["psnr"]["min_db"] == 99.0
    noisy = quality.compare(media_dir / "noisy.mkv", media_dir / "master.mkv", ranges=[[0, FPS]])
    assert noisy["ssim"]["mean"] < 0.9 and noisy["psnr"]["mean_db"] < 30


def test_a_cut_list_joins_ranges_and_a_new_aspect_is_compared_reframed(media_dir):
    joined = quality.compare(media_dir / "same.mkv", media_dir / "master.mkv", ranges=[[0, 12], [12, FPS]])
    assert joined["ssim"]["mean"] == 1.0 and joined["frames_reference"] == FPS
    vertical = quality.compare(media_dir / "vertical.mkv", media_dir / "master.mkv", ranges=[[0, FPS]])
    assert vertical["reference"].startswith("reframed") and vertical["ssim"]["mean"] > 0.8
    short = quality.compare(media_dir / "short.mkv", media_dir / "master.mkv", ranges=[[0, FPS]])
    assert not short["frames_match"]


def test_quality_refuses_mismatched_clocks_and_ranges(media_dir, tmp_path):
    other = clips.pattern_master(tmp_path, seconds=1.0, fps=FPS + 1)
    with pytest.raises(UsageError):
        quality.compare(other, media_dir / "master.mkv")
    with pytest.raises(UsageError):
        quality.compare(media_dir / "same.mkv", media_dir / "master.mkv", ranges=[[0, 10_000]])
    assert quality.parse_ssim_stats("n:1 Y:0.99 U:0.98 V:0.97 All:0.985 (18.2)\nn:2 Y:1 U:1 V:1 All:inf (inf)") == [0.985, 1.0]


# ---------------------------------------------------------------------------------------------------------------
# The decision rule (pure)


def _run(arm, wall, ssim=0.97, share=0.0, frames=True):
    return {"arm": arm, "wall_s": wall, "frames_match": frames, "psnr": {"mean_db": 32.0, "min_db": 28.0},
            "ssim": {"mean": ssim, "min": ssim - 0.02, "below_floor": 0, "below_floor_share": share}, "app_credits": None}


def _review(verdict="acceptable"):
    return {"arm": "higgsedit", "verdict": verdict, "by": "Quinn", "note": None, "at": "2026-10-01T10:00:00"}


PROTO = {"rule": bench.RULE}


def test_nothing_is_decided_without_both_arms_and_a_review():
    empty = bench.decide(PROTO, {"runs": [], "reviews": []})
    assert empty["verdict"] == "incomplete" and len(empty["missing"]) == 3
    no_review = bench.decide(PROTO, {"runs": [_run("remotion", 300), _run("higgsedit", 100)], "reviews": []})
    assert no_review["verdict"] == "incomplete" and no_review["speedup"] == 3.0
    assert no_review["missing"] == ["higgsedit: a person's review of the channel cut beside the master"]


def test_keep_only_when_faster_above_the_floor_and_acceptable():
    keep = bench.decide(PROTO, {"runs": [_run("remotion", 300), _run("remotion", 320), _run("remotion", 280),
                                         _run("higgsedit", 150), _run("higgsedit", 170), _run("higgsedit", 5000)],
                                "reviews": [_review()]})
    assert keep["verdict"] == "keep" and keep["reasons"] == []
    assert keep["arms"]["higgsedit"]["wall_s_median"] == 170 and keep["arms"]["remotion"]["runs"] == 3


@pytest.mark.parametrize("runs, review, why", [
    ([_run("remotion", 300), _run("higgsedit", 250)], _review(), "not genuinely faster"),
    ([_run("remotion", 300), _run("higgsedit", 100, ssim=0.85)], _review(), "mean SSIM"),
    ([_run("remotion", 300), _run("higgsedit", 100, share=0.2)], _review(), "of frames are under"),
    ([_run("remotion", 300), _run("higgsedit", 100, frames=False)], _review(), "frame count"),
    ([_run("remotion", 300), _run("higgsedit", 100)], _review("not-acceptable"), "reviewed the Higgsedit cut as not-acceptable"),
])
def test_drop_says_exactly_why(runs, review, why):
    verdict = bench.decide(PROTO, {"runs": runs, "reviews": [review]})
    assert verdict["verdict"] == "drop" and any(why in r for r in verdict["reasons"])


def test_a_broken_remotion_arm_means_the_harness_is_wrong_not_a_verdict():
    verdict = bench.decide(PROTO, {"runs": [_run("remotion", 300, ssim=0.5), _run("higgsedit", 100)],
                                   "reviews": [_review()]})
    assert verdict["verdict"] == "incomplete" and "harness is wrong" in verdict["reasons"][0]


# ---------------------------------------------------------------------------------------------------------------
# The workflow and the refusal


def test_run_refuses_because_the_higgsedit_arm_bills_unfunded_app_credits():
    with pytest.raises(SpendRefused) as err:
        bench.refuse_run()
    assert "app credits" in err.value.message and "Quinn" in err.value.message
    assert err.value.details["needs"] == "quinn" and err.value.exit_code == 4


def test_plan_measure_review_report_end_to_end(slate_home, media_dir):
    film = _film(slate_home, media_dir)
    protocol = bench.plan(film_dir=film, channel="TikTok", cut_s=1.0)
    folder = film / "bench" / "higgsedit"
    assert protocol["channel"]["key"] == "tiktok" and protocol["format"]["name"] == "social-reel"
    assert protocol["cut"] == {"seconds": 1.0, "ranges": [[0, FPS]], "frames": FPS,
                               "note": protocol["cut"]["note"]}
    assert protocol["master"].endswith("demo-film_master.mkv") and protocol["status"] == "not run: needs Quinn"
    assert any("slate variant demo-film" in s for s in protocol["arms"]["remotion"]["steps"])
    report = (folder / "REPORT.md").read_text()
    assert "needs Quinn" in report and "app credits" in report and "## The decision rule" in report
    assert "1.5" in report and "0.9" in report
    assert "**Channel:** TikTok" in report and "PSNR and SSIM" in report and ".." not in report
    bench.record_run(folder, arm="remotion", cut=media_dir / "same.mkv", wall_s=300)
    bench.record_run(folder, arm="higgsedit", cut=media_dir / "same.mkv", wall_s=120, app_credits=12.5)
    assert bench.report(folder)["verdict"] == "incomplete"  # no review yet
    bench.record_review(folder, arm="higgsedit", verdict="acceptable", by="Quinn", note="reads as the same film")
    verdict = bench.report(folder)
    assert verdict["verdict"] == "keep" and verdict["speedup"] == 2.5
    saved = jsonio.read(folder / "report.json")
    assert saved["verdict"] == "keep" and saved["arms"]["higgsedit"]["app_credits"] == 12.5
    assert "**Verdict: keep.**" in (folder / "REPORT.md").read_text()


def test_hero_channels_and_missing_masters_are_refused_clearly(slate_home, media_dir):
    with pytest.raises(UsageError) as err:
        bench.plan(channel="website")
    assert "hero placement" in err.value.message
    folder = slate_home["tmp"] / "bench"
    bench.plan(channel="reels", out=folder)
    with pytest.raises(MissingPrerequisite) as err:
        bench.record_run(folder, arm="remotion", cut=media_dir / "same.mkv", wall_s=10)
    assert "master" in err.value.message
    with pytest.raises(UsageError):
        bench.record_review(folder, arm="higgsedit", verdict="great", by="Quinn")
    with pytest.raises(UsageError):
        bench.record_review(folder, arm="higgsedit", verdict="acceptable", by=" ")
    with pytest.raises(MissingPrerequisite):
        bench.report(slate_home["tmp"] / "never-planned")


def test_the_command(slate_home, media_dir):
    film = _film(slate_home, media_dir)
    code, out, err = cli.run_cli(["bench", "higgsedit", "--film", str(film), "--length", "1", "--json"])
    assert code == 0, err
    planned = json.loads(out)
    assert planned["verdict"] == "incomplete" and planned["status"] == "not run: needs Quinn"
    code, out, _ = cli.run_cli(["bench", "higgsedit", "run", "--film", str(film), "--json"])
    assert code == 4 and json.loads(out)["error"] == "spend_refused" and "app credits" in json.loads(out)["message"]
    code, out, _ = cli.run_cli(["bench", "higgsedit", "measure", "--film", str(film), "--arm", "remotion",
                                "--cut", str(media_dir / "same.mkv"), "--wall-s", "200", "--json"])
    assert code == 0 and json.loads(out)["run"]["ssim"]["mean"] == 1.0
    code, out, _ = cli.run_cli(["bench", "higgsedit", "measure", "--film", str(film), "--arm", "remotion", "--json"])
    assert code == 2
    code, out, _ = cli.run_cli(["bench", "higgsedit", "review", "--film", str(film), "--arm", "higgsedit",
                                "--verdict", "not-acceptable", "--by", "Quinn", "--json"])
    assert code == 0 and json.loads(out)["verdict"] == "incomplete"  # still no Higgsedit run
    code, out, _ = cli.run_cli(["bench", "higgsedit", "report", "--film", str(film), "--json"])
    assert code == 0 and json.loads(out)["missing"] == ["higgsedit: at least 1 measured run"]
