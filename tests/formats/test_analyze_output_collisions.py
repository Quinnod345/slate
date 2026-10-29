"""Batch analysis must retain one source-bound report per input, including names that look like generated suffixes.

These tests script the analysis writer or call the pure allocator. They never decode a film, load a model or spend.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from slate import analyze, cli
from slate.analyze.cli import _outs


@pytest.mark.parametrize(
    ("videos", "names"),
    [
        (["/one/a.mov", "/two/b.mov"], ["a", "b"]),
        (["/one/a.mov", "/two/a.mov"], ["a", "a-2"]),
        (["/one/a.mov", "/two/a.mov", "/three/a-2.mov"], ["a", "a-3", "a-2"]),
        (["/three/a-2.mov", "/one/a.mov", "/two/a.mov"], ["a-2", "a", "a-3"]),
        (["/one/a.mov", "/three/a-2.mov", "/two/a.mov"], ["a", "a-2", "a-3"]),
        (
            ["/one/a.mov", "/two/a.mov", "/three/a-2.mov", "/four/a-3.mov", "/five/a-4.mov"],
            ["a", "a-5", "a-2", "a-3", "a-4"],
        ),
        (
            ["/one/a.mov", "/two/a.mov", "/three/a-2.mov", "/four/a-2.mov"],
            ["a", "a-3", "a-2", "a-2-2"],
        ),
        (["/one/...mov", "/two/..mov", "/three/video-2.mov"], ["video", "video-3", "video-2"]),
        (["", "/one/..mov", "/two/...mov"], ["video", "video-2", "video-3"]),
        (["/one/A.mov", "/two/a.mov", "/three/a-2.mov"], ["A", "a-3", "a-2"]),
        (
            ["/one/caf\u00e9.mov", "/two/cafe\u0301.mov", "/three/caf\u00e9-2.mov"],
            ["caf\u00e9", "cafe\u0301-3", "caf\u00e9-2"],
        ),
    ],
)
def test_batch_folders_reserve_literal_names_and_allocated_suffixes(videos, names, tmp_path):
    out = tmp_path / "study"
    assert _outs(videos, str(out)) == [str(out / name) for name in names]


@pytest.mark.parametrize(
    ("videos", "out", "expected"),
    [
        (["/one/a.mov"], "~/literal study", ["~/literal study"]),
        (["/one/...mov"], "~/literal study", ["~/literal study"]),
        (["/one/a.mov", "/two/a.mov", "/three/a-2.mov"], None, [None, None, None]),
        (["/one/a.mov", "/two/a.mov"], "", ["", ""]),
    ],
)
def test_single_video_and_default_output_contract_stays_exact(videos, out, expected):
    assert _outs(videos, out) == expected


@pytest.mark.parametrize(
    ("sources", "expected_names", "literal_name"),
    [
        (["/one/a.mov", "/two/a.mov", "/three/a-2.mov"], {"a", "a-2", "a-3"}, "a-2"),
        (["/one/...mov", "/two/..mov", "/three/video-2.mov"], {"video", "video-2", "video-3"}, "video-2"),
    ],
)
@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_cli_keeps_source_reports_in_child_folders_for_every_input_order(
    sources, expected_names, literal_name, order, slate_home, monkeypatch
):
    videos = [sources[index] for index in order]
    out = slate_home["tmp"] / "study"

    def scripted_analysis(video, **kwargs):
        # Model only the writer contract; these paths are labels, not files to probe or decode.
        folder = Path(kwargs["out"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "analysis.json").write_text(json.dumps({"source": video}), encoding="utf-8")
        (folder / "analysis.md").write_text(f"Source: {video}\n", encoding="utf-8")
        return {
            "out": str(folder),
            "video": {"path": video, "name": Path(video).name},
            "steps": {"cuts": {"status": "PASS", "summary": "scripted report writer"}},
            "scenes": [],
            "eases": {"moves": []},
        }

    monkeypatch.setattr(analyze, "run", scripted_analysis)
    code, stdout, stderr = cli.run_cli(["analyze", *videos, "--cuts", "--out", str(out), "--json"])
    assert code == 0, stderr
    rows = json.loads(stdout)["analyses"]
    assert len(rows) == 3
    assert len({row["out"] for row in rows}) == 3
    assert {Path(row["out"]).name for row in rows} == expected_names
    assert (out / literal_name / "analysis.md").read_text(encoding="utf-8") == f"Source: {sources[2]}\n"
    for row, video in zip(rows, videos):
        assert Path(row["out"]).parent == out
        assert row["video"] == video
        assert json.loads(Path(row["analysis"]).read_text(encoding="utf-8")) == {"source": video}
        assert Path(row["report"]).read_text(encoding="utf-8") == f"Source: {video}\n"
