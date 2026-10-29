"""`slate bench higgsedit`: the protocol, the refusal to run the unfunded Higgsedit arm, the quality measurement on
real (synthetic) video, and the decision rule on every path: keep, drop and not decided."""

from __future__ import annotations

import json
from copy import deepcopy
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
    clips.excerpt(master, folder / "social.mkv", 0, FPS,
                  vf="scale=90:160:force_original_aspect_ratio=increase,crop=90:160,setsar=1")
    return folder


@pytest.fixture(autouse=True)
def small_social_format(monkeypatch):
    """Keep the real portrait contract, using a small neutral format for these offline tests."""
    original = bench.formats.load

    def load(name, *args, **kwargs):
        fmt = deepcopy(original(name, *args, **kwargs))
        if name == "social-reel" and kwargs.get("aspect") == "9:16":
            fmt.update(width=90, height=160)
        return fmt

    monkeypatch.setattr(bench.formats, "load", load)


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


MASTER_MEDIA = {"width": 160, "height": 90, "fps_num": FPS, "fps_den": 1}
CUT_MEDIA = {**MASTER_MEDIA, "width": 90, "height": 160}
PROTO = {"version": bench.VERSION, "master": "/fixture/master.mkv", "master_sha256": jsonio.sha256_bytes("master"),
         "master_media": MASTER_MEDIA, "format": {"name": "social-reel", "aspect": "9:16", "width": 90, "height": 160},
         "cut": {"ranges": [[0, FPS]], "frames": FPS}, "rule": deepcopy(bench.RULE),
         "channel": {}, "arms": {}, "measure": {}}
PROTO["protocol_sha256"] = bench._protocol_hash(PROTO)


def _seal(run):
    run["run_sha256"] = jsonio.sha256_obj({k: v for k, v in run.items() if k != "run_sha256"})
    return run


def _run(arm, wall, ssim=0.97, share=0.0, frames=True, cut_key=None):
    return _seal({"arm": arm, "wall_s": wall, "frames_match": frames, "frames": FPS if frames else FPS - 4,
                  "frames_reference": FPS, "frames_compared": FPS if frames else FPS - 4, "ranges": [[0, FPS]],
                  "cut": f"/fixture/{arm}.mkv", "master": PROTO["master"], "binding": bench._binding(PROTO),
                  "cut_sha256": jsonio.sha256_bytes(cut_key or arm), "cut_media": deepcopy(CUT_MEDIA),
                  "master_media": deepcopy(MASTER_MEDIA),
                  "psnr": {"mean_db": 32.0, "min_db": 28.0, "frames": FPS if frames else FPS - 4},
                  "ssim": {"mean": ssim, "min": ssim - 0.02, "below_floor": 0, "below_floor_share": share},
                  "app_credits": None})


def _review(verdict="acceptable", cut_key="higgsedit"):
    return {"arm": "higgsedit", "verdict": verdict, "by": "fixture-person", "note": None, "at": "2026-10-01T10:00:00",
            "binding": bench._binding(PROTO), "cut_sha256": jsonio.sha256_bytes(cut_key),
            "remotion_cut_sha256s": [jsonio.sha256_bytes("remotion")]}


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
    bench.record_run(folder, arm="remotion", cut=media_dir / "social.mkv", wall_s=300)
    bench.record_run(folder, arm="higgsedit", cut=media_dir / "social.mkv", wall_s=120, app_credits=12.5)
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
                                "--cut", str(media_dir / "social.mkv"), "--wall-s", "200", "--json"])
    assert code == 0 and json.loads(out)["run"]["ssim"]["mean"] == 1.0
    code, out, _ = cli.run_cli(["bench", "higgsedit", "measure", "--film", str(film), "--arm", "remotion", "--json"])
    assert code == 2
    code, out, _ = cli.run_cli(["bench", "higgsedit", "review", "--film", str(film), "--arm", "higgsedit",
                                "--verdict", "not-acceptable", "--by", "Quinn", "--json"])
    assert code == 3 and "measured cut" in json.loads(out)["message"]
    code, out, _ = cli.run_cli(["bench", "higgsedit", "report", "--film", str(film), "--json"])
    assert code == 0 and "higgsedit: at least 1 measured run" in json.loads(out)["missing"]


@pytest.fixture
def bound_benchmark(tmp_path, monkeypatch):
    """Exercise file bindings with real bytes and a recorded, deterministic local measurement backend."""
    from slate.qa import media

    monkeypatch.setattr(media, "video_info", lambda *_a, **_kw: deepcopy(MASTER_MEDIA))
    master = tmp_path / "master.mkv"
    master.write_bytes(b"master-v1")
    cut = tmp_path / "cut-a.mkv"
    cut.write_bytes(b"cut-a")
    other = tmp_path / "cut-b.mkv"
    other.write_bytes(b"cut-b")
    folder = tmp_path / "bench"
    protocol = bench.plan(master=master, cut_s=1.0, out=folder)

    def compare(cut_file, master_file, *, ranges):
        return {"cut": str(cut_file), "master": str(master_file), "ranges": deepcopy(ranges),
                "cut_media": deepcopy(CUT_MEDIA), "master_media": deepcopy(MASTER_MEDIA),
                "frames_cut": FPS, "frames_reference": FPS, "frames_compared": FPS, "frames_match": True,
                "reference": "reframed", "psnr": {"mean_db": 99.0, "min_db": 99.0, "frames": FPS},
                "ssim": {"mean": 1.0, "min": 1.0, "below_floor": 0, "below_floor_share": 0.0},
                "engine": {"name": "recorded local fixture", "version": "1"}}

    monkeypatch.setattr(quality, "compare", compare)
    bench.record_run(folder, arm="remotion", cut=cut, wall_s=300)
    bench.record_run(folder, arm="higgsedit", cut=cut, wall_s=120)
    bench.record_review(folder, arm="higgsedit", verdict="acceptable", by="fixture-person")
    assert bench.report(folder)["verdict"] == "keep"
    return folder, master, cut, other, compare, protocol


def test_obsolete_cut_review_cannot_authorize_replacement_bytes():
    data = {"runs": [_run("remotion", 300), _run("higgsedit", 100, cut_key="new-cut")], "reviews": [_review()]}
    verdict = bench.decide(PROTO, data)
    assert verdict["verdict"] == "incomplete"
    assert any("does not name a measured cut" in reason for reason in verdict["reasons"])
    assert any("person's review" in missing for missing in verdict["missing"])


def test_different_master_binding_is_incomplete_even_with_good_quality():
    run = _run("higgsedit", 100)
    run["binding"]["master_sha256"] = jsonio.sha256_bytes("another-master")
    _seal(run)
    verdict = bench.decide(PROTO, {"runs": [_run("remotion", 300), run], "reviews": [_review()]})
    assert verdict["verdict"] == "incomplete"
    assert any("unbound" in reason for reason in verdict["reasons"])


@pytest.mark.parametrize("change", ["ranges", "master", "channel"])
def test_replanning_preserves_protocol_measurements_and_reviews(bound_benchmark, change):
    folder, master, _cut, _other, _compare, _protocol = bound_benchmark
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    kwargs = {"master": master, "cut_s": 1.0, "out": folder}
    if change == "ranges":
        kwargs["ranges"] = f"{FPS}-{FPS * 2}"
    elif change == "channel":
        kwargs["channel"] = "instagram_reels"
    else:
        alternative = master.with_name("another-master.mkv")
        alternative.write_bytes(b"another-master")
        kwargs["master"] = alternative
    with pytest.raises(UsageError, match="evidence is preserved"):
        bench.plan(**kwargs)
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before
    repeated = bench.plan(master=master, cut_s=1.0, out=folder)
    assert repeated["protocol_sha256"] == json.loads(before["protocol.json"])["protocol_sha256"]
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


def test_replaced_protocol_cannot_reuse_old_runs_or_reviews(bound_benchmark):
    folder, _master, _cut, _other, _compare, _protocol = bound_benchmark
    measurements = (folder / "measurements.json").read_bytes()
    protocol = jsonio.read(folder / "protocol.json")
    protocol["cut"]["ranges"] = [[FPS, FPS * 2]]
    protocol["protocol_sha256"] = bench._protocol_hash(protocol)
    jsonio.write_atomic(folder / "protocol.json", protocol)
    verdict = bench.report(folder)
    assert verdict["verdict"] == "incomplete"
    assert any("unbound" in reason for reason in verdict["reasons"])
    assert (folder / "measurements.json").read_bytes() == measurements


def test_new_unreviewed_cut_cannot_join_a_winning_median(bound_benchmark):
    folder, _master, _cut, other, _compare, _protocol = bound_benchmark
    bench.record_run(folder, arm="higgsedit", cut=other, wall_s=10)
    verdict = bench.report(folder)
    assert verdict["verdict"] == "incomplete" and verdict["speedup"] > 2.5
    assert any(jsonio.sha256(other) in missing for missing in verdict["missing"])
    bench.record_review(folder, arm="higgsedit", verdict="acceptable", by="fixture-person")
    assert bench.report(folder)["verdict"] == "keep"


def test_cli_can_review_an_older_distinct_cut_without_remeasuring(bound_benchmark):
    folder, _master, cut, other, _compare, _protocol = bound_benchmark
    bench.record_run(folder, arm="higgsedit", cut=other, wall_s=10)
    data = jsonio.read(folder / "measurements.json")
    data["reviews"] = []
    jsonio.write_atomic(folder / "measurements.json", data)
    runs = deepcopy(data["runs"])
    for path, expected in ((cut, "incomplete"), (other, "keep")):
        code, out, err = cli.run_cli(["bench", "higgsedit", "review", "--out", str(folder), "--arm", "higgsedit",
                                    "--cut", str(path), "--verdict", "acceptable", "--by", "fixture-person", "--json"])
        assert code == 0, err
        result = json.loads(out)
        assert result["review"]["cut_sha256"] == jsonio.sha256(path)
        assert result["verdict"] == expected
    assert jsonio.read(folder / "measurements.json")["runs"] == runs


def test_later_rejection_of_exact_bytes_supersedes_acceptance(bound_benchmark):
    folder, _master, cut, _other, _compare, _protocol = bound_benchmark
    bench.record_review(folder, arm="higgsedit", cut=cut, verdict="not-acceptable", by="fixture-person")
    verdict = bench.report(folder)
    assert verdict["verdict"] == "drop" and verdict["review"]["verdict"] == "not-acceptable"
    assert len(jsonio.read(folder / "measurements.json")["reviews"]) == 2


@pytest.mark.parametrize("which", ["master", "cut"])
def test_changed_bytes_invalidate_report_and_refuse_review(bound_benchmark, which):
    folder, master, cut, _other, _compare, _protocol = bound_benchmark
    before = (folder / "measurements.json").read_bytes()
    (master if which == "master" else cut).write_bytes(b"changed")
    verdict = bench.report(folder)
    assert verdict["verdict"] == "incomplete"
    assert any("bytes changed" in reason for reason in verdict["reasons"])
    with pytest.raises(UsageError, match="not current"):
        bench.record_review(folder, arm="higgsedit", verdict="acceptable", by="fixture-person")
    assert (folder / "measurements.json").read_bytes() == before


@pytest.mark.parametrize("which", ["master", "cut"])
def test_comparison_mutation_records_nothing(bound_benchmark, which):
    folder, master, cut, _other, compare, _protocol = bound_benchmark
    before = (folder / "measurements.json").read_bytes()

    def changes_bytes(cut_file, master_file, **kwargs):
        result = compare(cut_file, master_file, **kwargs)
        (master if which == "master" else cut).write_bytes(b"changed during compare")
        return result

    with pytest.raises(UsageError, match="changed during"):
        bench.record_run(folder, arm="higgsedit", cut=cut, wall_s=100, comparer=changes_bytes)
    assert (folder / "measurements.json").read_bytes() == before


@pytest.mark.parametrize("field,value", [("wall_s", float("nan")), ("wall_s", float("inf")), ("wall_s", 0),
                                         ("wall_s", -1), ("app_credits", float("nan")),
                                         ("app_credits", float("inf")), ("app_credits", -1)])
def test_invalid_run_arguments_are_refused_before_storage(bound_benchmark, field, value):
    folder, _master, cut, _other, _compare, _protocol = bound_benchmark
    before = (folder / "measurements.json").read_bytes()
    kwargs = {"wall_s": 100, "app_credits": 1, field: value}
    with pytest.raises(UsageError, match="finite"):
        bench.record_run(folder, arm="higgsedit", cut=cut, **kwargs)
    assert (folder / "measurements.json").read_bytes() == before


@pytest.mark.parametrize("field,value", [("wall_s", float("nan")), ("wall_s", float("inf")),
                                         ("app_credits", float("nan")), ("ssim.mean", float("nan")),
                                         ("ssim.min", float("inf")), ("ssim.below_floor_share", float("nan")),
                                         ("psnr.mean_db", float("nan"))])
def test_malformed_historical_measurements_stay_incomplete(field, value):
    run = _run("higgsedit", 100)
    if "." in field:
        block, key = field.split(".")
        run[block][key] = value
    else:
        run[field] = value
    _seal(run)
    verdict = bench.decide(PROTO, {"runs": [_run("remotion", 300), run], "reviews": [_review()]})
    assert verdict["verdict"] == "incomplete"
    assert any("finite" in reason for reason in verdict["reasons"])


@pytest.mark.parametrize("fault", ["clock", "aspect", "master_metadata", "ranges", "coverage", "psnr_coverage", "nan_ssim"])
def test_wrong_quality_identity_or_coverage_records_nothing(bound_benchmark, fault):
    folder, _master, cut, _other, compare, _protocol = bound_benchmark
    before = (folder / "measurements.json").read_bytes()

    def bad_result(*args, **kwargs):
        q = compare(*args, **kwargs)
        if fault == "clock":
            q["cut_media"]["fps_num"] += 1
        elif fault == "aspect":
            q["cut_media"].update(width=160, height=90)
        elif fault == "master_metadata":
            q["master_media"]["width"] += 1
        elif fault == "ranges":
            q["ranges"] = [[FPS, FPS * 2]]
        elif fault == "coverage":
            q["frames_compared"] -= 1
        elif fault == "psnr_coverage":
            q["psnr"]["frames"] -= 1
        else:
            q["ssim"]["mean"] = float("nan")
        return q

    with pytest.raises(UsageError, match="no run was recorded"):
        bench.record_run(folder, arm="higgsedit", cut=cut, wall_s=100, comparer=bad_result)
    assert (folder / "measurements.json").read_bytes() == before


def test_legacy_unbound_evidence_stays_incomplete_and_is_not_upgraded(bound_benchmark):
    folder, master, _cut, _other, _compare, _protocol = bound_benchmark
    protocol = jsonio.read(folder / "protocol.json")
    protocol["version"] = 1
    protocol.pop("protocol_sha256")
    jsonio.write_atomic(folder / "protocol.json", protocol)
    evidence = (folder / "measurements.json").read_bytes()
    verdict = bench.report(folder)
    assert verdict["verdict"] == "incomplete" and any("legacy" in reason for reason in verdict["reasons"])
    with pytest.raises(UsageError, match="preserved"):
        bench.plan(master=master, cut_s=1.0, out=folder)
    assert (folder / "measurements.json").read_bytes() == evidence


def test_nonfinite_speedup_cannot_keep_even_when_each_wall_time_is_finite():
    verdict = bench.decide(PROTO, {"runs": [_run("remotion", 1e308), _run("higgsedit", 1e-308)],
                                   "reviews": [_review()]})
    assert verdict["verdict"] == "incomplete" and "measured speedup is not finite" in verdict["reasons"]


def test_other_master_is_refused_before_comparison_and_storage(bound_benchmark):
    folder, master, cut, _other, _compare, _protocol = bound_benchmark
    alternative = master.with_name("different-master.mkv")
    alternative.write_bytes(b"different master")
    before = (folder / "measurements.json").read_bytes()

    def forbidden_compare(*_a, **_kw):
        pytest.fail("a different master must be refused before running the comparer")

    with pytest.raises(UsageError, match="differs from the frozen"):
        bench.record_run(folder, arm="higgsedit", cut=cut, master=alternative, wall_s=100, comparer=forbidden_compare)
    assert (folder / "measurements.json").read_bytes() == before


def test_review_of_unmeasured_cut_bytes_is_refused(bound_benchmark):
    folder, _master, _cut, other, _compare, _protocol = bound_benchmark
    before = (folder / "measurements.json").read_bytes()
    with pytest.raises(UsageError, match="have not been measured"):
        bench.record_review(folder, arm="higgsedit", cut=other, verdict="acceptable", by="fixture-person")
    assert (folder / "measurements.json").read_bytes() == before


def test_new_remotion_cut_requires_a_new_comparison_review(bound_benchmark):
    folder, _master, cut, other, _compare, _protocol = bound_benchmark
    bench.record_run(folder, arm="remotion", cut=other, wall_s=300)
    verdict = bench.report(folder)
    assert verdict["verdict"] == "incomplete"
    assert any("current Remotion cuts" in reason for reason in verdict["reasons"])
    bench.record_review(folder, arm="higgsedit", cut=cut, verdict="acceptable", by="fixture-person")
    assert bench.report(folder)["verdict"] == "keep"


def test_genuine_short_cut_is_measured_but_cannot_win(bound_benchmark):
    folder, _master, cut, _other, compare, _protocol = bound_benchmark

    def short_result(*args, **kwargs):
        q = compare(*args, **kwargs)
        q.update(frames_cut=FPS - 4, frames_compared=FPS - 4, frames_match=False)
        q["psnr"]["frames"] = FPS - 4
        return q

    bench.record_run(folder, arm="higgsedit", cut=cut, wall_s=100, comparer=short_result)
    verdict = bench.report(folder)
    assert verdict["verdict"] == "drop"
    assert any("frame count" in reason for reason in verdict["reasons"])


def test_unbound_measurements_and_reviews_are_preserved_as_incomplete(bound_benchmark):
    folder, _master, _cut, _other, _compare, _protocol = bound_benchmark
    data = jsonio.read(folder / "measurements.json")
    data["runs"][1].pop("binding")
    data["reviews"][0].pop("cut_sha256")
    jsonio.write_atomic(folder / "measurements.json", data)
    before = (folder / "measurements.json").read_bytes()
    verdict = bench.report(folder)
    assert verdict["verdict"] == "incomplete"
    assert any("legacy" in reason for reason in verdict["reasons"])
    assert (folder / "measurements.json").read_bytes() == before


def test_decision_thresholds_do_not_round_a_failure_into_a_keep():
    for run, reason in ((_run("higgsedit", 300 / 1.4999), "not genuinely faster"),
                        (_run("higgsedit", 100, ssim=0.899999), "mean SSIM")):
        verdict = bench.decide(PROTO, {"runs": [_run("remotion", 300), run], "reviews": [_review()]})
        assert verdict["verdict"] == "drop" and any(reason in row for row in verdict["reasons"])


@pytest.mark.parametrize("collection", ["runs", "reviews"])
@pytest.mark.parametrize("value", [0, "", {}, None])
def test_falsy_malformed_collections_are_explicitly_incomplete(bound_benchmark, collection, value):
    folder, _master, _cut, _other, _compare, _protocol = bound_benchmark
    data = jsonio.read(folder / "measurements.json")
    data[collection] = value
    jsonio.write_atomic(folder / "measurements.json", data)
    before = (folder / "measurements.json").read_bytes()
    verdict = bench.report(folder)
    assert verdict["verdict"] == "incomplete"
    assert any("not a list" in reason for reason in verdict["reasons"])
    assert (folder / "measurements.json").read_bytes() == before


@pytest.mark.parametrize("field", ["master_media", "format", "cut", "rule", "channel", "arms", "measure"])
def test_malformed_protocol_collections_refuse_without_rewriting_evidence(bound_benchmark, field):
    folder, _master, _cut, _other, _compare, _protocol = bound_benchmark
    protocol = jsonio.read(folder / "protocol.json")
    protocol[field] = []
    protocol["protocol_sha256"] = bench._protocol_hash(protocol)
    jsonio.write_atomic(folder / "protocol.json", protocol)
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    decision = bench.decide(protocol, jsonio.read(folder / "measurements.json"))
    assert decision["verdict"] == "incomplete" and any("malformed" in reason for reason in decision["reasons"])
    if field == "master_media":
        assert bench.report(folder)["verdict"] == "incomplete"
    else:
        with pytest.raises(UsageError, match="malformed"):
            bench.report(folder)
        assert {p.name: p.read_bytes() for p in folder.iterdir()} == before
    assert (folder / "measurements.json").read_bytes() == before["measurements.json"]


def test_overflowing_credit_totals_stay_incomplete_and_json_safe():
    a, b = _run("higgsedit", 100), _run("higgsedit", 110)
    for run in (a, b):
        run["app_credits"] = 1e308
        _seal(run)
    verdict = bench.decide(PROTO, {"runs": [_run("remotion", 300), a, b], "reviews": [_review()]})
    assert verdict["verdict"] == "incomplete"
    assert any("total app credits" in reason for reason in verdict["reasons"])
    json.dumps(verdict, allow_nan=False)


@pytest.mark.parametrize("values", [[0.899999] * FPS, [0.799999] + [1.0] * (FPS - 1)])
def test_comparer_preserves_ssim_precision_at_decision_boundaries(tmp_path, monkeypatch, values):
    """Use ffmpeg-shaped recorded statistics so rounding cannot promote a failed measurement."""
    from slate.qa import media

    cut, master = tmp_path / "cut.mkv", tmp_path / "master.mkv"
    cut.write_bytes(b"fixture cut")
    master.write_bytes(b"fixture master")
    monkeypatch.setattr(media, "video_info", lambda *_a, **_kw: {**MASTER_MEDIA, "frames": FPS})
    monkeypatch.setattr(media, "engine", lambda *_a, **_kw: {"name": "recorded ffmpeg statistics", "version": "1"})

    def recorded_run(argv, *, cwd, **_kwargs):
        graph = argv[argv.index("-lavfi") + 1]
        if "psnr=" in graph:
            text = "\n".join(f"n:{i + 1} psnr_avg:99.0" for i in range(FPS))
            name = "psnr.log"
        else:
            text = "\n".join(f"n:{i + 1} All:{value}" for i, value in enumerate(values))
            name = "ssim.log"
        (Path(cwd) / name).write_text(text)

    monkeypatch.setattr(quality.procs, "run", recorded_run)
    result = quality.compare(cut, master, ranges=[[0, FPS]])
    assert result["ssim"]["mean"] == sum(values) / FPS
    assert result["ssim"]["min"] == min(values)
    assert result["ssim"]["below_floor_share"] == sum(v < quality.SSIM_FRAME_FLOOR for v in values) / FPS
    if values[0] == 0.899999:
        assert result["ssim"]["mean"] < bench.RULE["ssim_mean_min"]
