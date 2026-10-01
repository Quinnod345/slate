"""The delivery's README is written from the set's own records (L-sound-S032), and every delivery keeps the code and
data behind it: a commit of the film's small files, tagged with the version (L-render-D021)."""

from __future__ import annotations

from pathlib import Path

import filmkit
from slate import gates, jsonio, procs
from slate.finish import snapshot
from slate.finish.film import Film

BALANCE = {
    "kind": "balance_check", "unit": "word", "voice_lufs": -16.25, "split": True,
    "per_family": {"accents": {"integrated_lu": -16.7, "hits": 12,
                               "hit_momentary_lu": {"median": -19.5, "max": -11.6, "min": -25.0}},
                   "score": {"integrated_lu": -20.4, "hits": 0, "hit_momentary_lu": None}},
    "clarity": {"rule_db": 12.0, "band_hz": [1000, 5000], "units": 978, "min_db": 6.2, "median_db": 36.0,
                "failing": []},
    "never_over": {"min_db": 0.4, "rule_db": 0.0},
}


def _mastered(make_film, film_id: str, *, git: bool = False) -> Path:
    d = make_film(film_id, mode="autonomous", format="finish-reel")
    if git:
        from slate.state import scaffold

        assert scaffold.git_init(d, f"Start film {film_id}")["initialized"]
    code, _out, err = filmkit.cli(d, "master")
    assert code == 0, err
    filmkit.pass_gates(d)
    return d


def _git(d: Path, *args: str) -> str:
    return (procs.run(["git", *args], cwd=d, timeout=60, check=True).stdout or "").strip()


def test_the_readme_is_the_records_own_numbers(make_film):
    d = _mastered(make_film, "rd")
    filmkit.write_json(d / "sound/mix/balance_check.json", BALANCE)
    code, out = filmkit.cli_json(d, "deliver")
    assert code == 0, out
    folder = d / "deliver/v1"
    m = jsonio.read(folder / "manifest.json")
    kinds = {f["kind"]: f for f in m["files"]}
    assert "readme" in kinds and "balance_report" in kinds  # listed, hashed and delivered like every file
    text = (folder / "README.md").read_text()
    # every standing gate's verdict, as the manifest (and so qa/gates.json) has it
    report = gates.load_report(d)
    for r in m["gates"]["results"]:
        assert f"| {r['gate']} | {r['status']} |" in text
        assert report[r["gate"]].status == r["status"]
    # the balance report's numbers, read from the copy in the set
    assert jsonio.read(folder / "sound/balance_check.json")["clarity"]["min_db"] == 6.2
    assert "-16.25 LUFS" in text
    assert "| accents | -16.7 | -19.5 | -11.6 | 12 |" in text
    assert "| 6.2 dB | ≥ 12.0 dB |" in text and "| 36.0 dB |" in text and "| 0 of 978 | 0 |" in text
    assert "Never over" in text and "0.4 dB" in text
    # every delivered file is in it, with its hash
    for f in m["files"]:
        if f["kind"] != "readme":
            assert f"`{f['file']}`" in text and f["sha256"][:12] in text
    assert "Don't edit it by hand" in text


def test_without_a_balance_report_the_readme_says_so(make_film):
    d = _mastered(make_film, "rd2")
    code, out = filmkit.cli_json(d, "deliver")
    assert code == 0, out
    text = (d / "deliver/v1/README.md").read_text()
    assert "No balance report was written for this mix" in text
    assert "No snapshot: the film folder isn't a git repository" in text  # the fixture film has no git


def test_every_delivery_keeps_the_code_and_data_behind_it(make_film):
    d = _mastered(make_film, "snap", git=True)
    (d / "scenes" / "act1").mkdir(parents=True, exist_ok=True)
    (d / "scenes" / "act1" / "index.tsx").write_text("export default function Act1() { return null; }\n")
    code, out = filmkit.cli_json(d, "deliver")
    assert code == 0, out
    m = jsonio.read(d / "deliver/v1/manifest.json")
    snap = m["snapshot"]
    assert snap["repo"] is True and snap["commit"] and snap["tag"] == "deliver/v1" and snap["committed"] is True
    assert _git(d, "rev-parse", "deliver/v1^{commit}") == snap["commit"]
    committed = _git(d, "show", "--name-only", "--format=", snap["commit"]).splitlines()
    assert "scenes/act1/index.tsx" in committed  # the scene code behind the render
    assert not [f for f in _git(d, "ls-tree", "-r", "--name-only", snap["commit"]).splitlines()
                if f.endswith((".mov", ".mp4", ".wav"))]  # heavy media stays out
    assert f"tagged `deliver/v1`" in (d / "deliver/v1/README.md").read_text()
    assert out["snapshot"]["commit"] == snap["commit"]


def test_a_tag_on_another_commit_is_never_moved(make_film):
    d = _mastered(make_film, "tags", git=True)
    film = Film(d)
    first = snapshot.commit(film, "Deliver v1")
    tagged = snapshot.tag(film, first, "deliver/v1", "v1")
    assert tagged["tag"] == "deliver/v1"
    (d / "notes.md").write_text("a change\n")
    second = snapshot.commit(film, "Deliver v1 again")
    assert second["commit"] != first["commit"]
    again = snapshot.tag(film, second, "deliver/v1", "v1 again")
    assert again["tag"] == "deliver/v1-2" and _git(d, "rev-parse", "deliver/v1^{commit}") == first["commit"]
    assert snapshot.tag(film, first, "deliver/v1", "v1")["tag"] == "deliver/v1"  # the same commit keeps its tag
