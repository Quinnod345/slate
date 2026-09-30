"""The ES3 kit's parity with the Remotion kit: the Python mirror and the ExtendScript file against the shared
cross-language vectors (the ExtendScript through its node runner), plus logos as AE shape data (svg2ae)."""

from __future__ import annotations

import json
import math

import pytest

from slate import paths, procs
from slate.ae import eases, svg2ae

SHARED = paths.REPO / "tests" / "vectors" / "eases.json"
SHARED_T90 = paths.REPO / "tests" / "vectors" / "t90.json"
LOCAL = paths.ENGINE_SRC / "ae" / "tests" / "fixtures" / "eases.local.json"
RUNNER = paths.ENGINE_SRC / "ae" / "tests" / "run_vectors.mjs"


def _close(got, want, tol):
    return abs(got - want) <= tol * max(1.0, abs(want))


# ---- the Python mirror against the shared vectors -------------------------------------------------------------


@pytest.mark.skipif(not SHARED.is_file(), reason="the shared vectors aren't written yet (engine-kit)")
def test_python_mirror_matches_the_shared_vectors():
    doc = json.loads(SHARED.read_text())
    tol = doc["tolerance"]
    for e in doc["eases"]:
        d = eases.normalize(e["spec"])
        for u, want in e["samples"]:
            assert _close(eases.value(d, u), want, tol), (e["name"], u)
        assert _close(eases.t90(d), e["t90"], tol), e["name"]
        if "ae" in e:
            got = eases.ae_ease(d["points"])
            for side in ("out", "in"):
                for k in ("influence", "speed"):
                    assert _close(got[side][k], e["ae"][side][k], tol), (e["name"], side, k)
    t = json.loads(SHARED_T90.read_text())
    for r in t["t90"]:
        d = eases.normalize(r["spec"])
        for key, level in (("t50", 0.5), ("t90", 0.9), ("t99", 0.99)):
            assert _close(eases.t_at(d, level), r[key], t["tolerance"]), (r["name"], key)
    for r in t["start_for"]:
        assert _close(eases.start_for(r["land"], r["dur"], r["spec"]), r["start"], t["tolerance"]), r["ease"]


def test_python_mirror_matches_the_local_stand_in():
    doc = json.loads(LOCAL.read_text())
    for c in doc["cases"]:
        assert abs(eases.value(doc["eases"][c["ease"]], c["u"]) - c["value"]) <= doc["tolerance"]["value"]
    for name, want in doc["t90"].items():
        assert abs(eases.t90(doc["eases"][name]) - want) <= doc["tolerance"]["t90"]


def test_exact_endpoints_and_the_kits_readings():
    for raw in ([0.2, 0, 0, 1], "cubic-bezier(0.4, 0.14, 0.3, 1)", "sine", "sway", "linear", "spring",
                {"kind": "spring0", "stiffness": 12}, {"bezier": [0.42, 0, 0.58, 1]}, {"cubic_bezier": "0.25, 0.1, 0.25, 1"}):
        assert eases.value(raw, 0) == 0.0 and eases.value(raw, -3) == 0.0 and eases.value(raw, math.nan) == 0.0
        assert eases.value(raw, 1) == 1.0 and eases.value(raw, 9) == 1.0
    with pytest.raises(Exception):
        eases.normalize([1.2, 0, 0.5, 1])
    assert eases.leaves_range([0.34, 1.56, 0.64, 1]) and not eases.leaves_range([0.2, 0, 0, 1])


def test_ae_conversion_gives_back_the_curve():
    for p in ([0.2, 0, 0, 1], [0.42, 0, 0.58, 1], [0.34, 1.56, 0.64, 1], [0.36, -0.4, 0.6, 1], [0.8, 0, 0.2, 1]):
        for dv in (1.0, -250.0, 1234.5):
            seg = eases.seg_ease(p, dv)
            worst = max(abs(eases.ae_segment_value(seg, dv, i / 200) - eases.value(p, i / 200)) for i in range(201))
            assert worst < 5e-3, (p, dv, worst)
    # a firm deceleration over a 1200 px move in 0.8 s: out 20% at 3.5x the average speed, in 80% at rest
    seg = eases.seg_ease([0.2, 0.7, 0.2, 1.0], 1200 / 0.8)
    assert seg["out"] == pytest.approx([5250.0, 20.0]) and seg["inn"] == pytest.approx([0.0, 80.0])


# ---- the ExtendScript file, through its node runner -----------------------------------------------------------


def _node():
    node = procs.which("node")
    if not node:
        pytest.skip("node isn't installed")
    return node


def test_extendscript_runner_passes_the_shared_vectors():
    proc = procs.run([_node(), str(RUNNER), "--json"], timeout=120)
    res = json.loads(proc.stdout)
    assert proc.returncode == 0 and res["ok"], res["failures"][:5]
    assert res["source"] == ("shared" if SHARED.is_file() else "local")
    assert res["counts"]["value"] > 100 and res["lint"] == []


def test_extendscript_runner_passes_the_local_stand_in():
    proc = procs.run([_node(), str(RUNNER), "--json", "--vectors", str(LOCAL)], timeout=120)
    res = json.loads(proc.stdout)
    assert proc.returncode == 0 and res["ok"] and res["source"] == "given-local"


def test_extendscript_runner_fails_on_a_wrong_vector(tmp_path):
    doc = json.loads(LOCAL.read_text())
    doc["cases"][5]["value"] += 0.01
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(doc))
    proc = procs.run([_node(), str(RUNNER), "--json", "--vectors", str(bad)], timeout=120)
    res = json.loads(proc.stdout)
    assert proc.returncode == 1 and not res["ok"] and res["failures"][0]["check"] == "value"


# ---- logos ----------------------------------------------------------------------------------------------------


def test_svg_paths_become_ae_shapes():
    data = svg2ae.convert_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                               '<path id="a" d="M0 0 L10 0 L10 10 Z" fill="#abc"/>'
                               '<path d="m 1 1 c 1 0 2 1 2 2 s 1 2 2 2" fill="none"/></svg>')
    a, b = data["items"]
    assert a == {"id": "a", "fill": "#AABBCC", "paths": [{"v": [[0, 0], [10, 0], [10, 10]], "i": [[0, 0]] * 3,
                                                           "o": [[0, 0]] * 3, "c": True}], "bbox": [0, 0, 10, 10]}
    assert b["id"] == "p2" and b["fill"] is None and b["paths"][0]["c"] is False
    # relative cubic from (1,1): c1 (2,1), c2 (3,2), end (3,3); then "s" reflects c2 through (3,3) to (3,4)
    assert b["paths"][0]["v"] == [[1, 1], [3, 3], [5, 5]]
    assert b["paths"][0]["o"][0] == [1, 0] and b["paths"][0]["i"][1] == [0, -1]
    assert b["paths"][0]["o"][1] == [0, 1] and b["paths"][0]["i"][2] == [-1, 0]


def test_basic_shapes_arcs_and_transforms():
    data = svg2ae.convert_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
        '<g transform="translate(100,0) scale(2)"><circle id="c" cx="10" cy="10" r="5" fill="black"/></g>'
        '<rect id="r" x="0" y="0" width="20" height="10" rx="2" fill="rgb(255,0,0)"/>'
        '<polygon id="t" points="0,0 10,0 5,8" fill="#000"/>'
        '<path id="arc" d="M0 50 A 25 25 0 0 1 50 50" fill="#123456"/>'
        '<defs><path id="hidden" d="M0 0 L1 1"/></defs>'
        '<linearGradient id="g"/></svg>')
    by = {i["id"]: i for i in data["items"]}
    assert data["viewBox"] == [0.0, 0.0, 200.0, 100.0]
    assert "hidden" not in by
    assert by["c"]["bbox"] == [110.0, 10.0, 130.0, 30.0] and by["c"]["fill"] == "#000000"
    assert len(by["c"]["paths"][0]["v"]) == 4 and by["c"]["paths"][0]["c"] is True
    assert by["r"]["fill"] == "#FF0000" and len(by["r"]["paths"][0]["v"]) == 8
    assert by["t"]["paths"][0]["v"] == [[0, 0], [10, 0], [5, 8]]
    arc = by["arc"]["paths"][0]
    assert arc["v"][0] == [0, 50] and arc["v"][-1] == pytest.approx([50, 50], abs=1e-3)
    top = min(v[1] for v in arc["v"])
    assert top == pytest.approx(25, abs=0.5)  # a half circle of radius 25 over its chord
    assert any("linearGradient" in w for w in data["warnings"])


def test_duplicate_ids_become_unique():
    data = svg2ae.convert_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4">'
                               '<path id="x" d="M0 0 L1 1"/><path id="x" d="M1 1 L2 2"/></svg>')
    assert [i["id"] for i in data["items"]] == ["x", "x-2"]


def test_brand_logos_read_the_pack(tmp_path):
    (tmp_path / "logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2 2">'
                                       '<path d="M0 0 L2 2" fill="#010203"/></svg>')
    pack = {"logos": {"mark": "logo.svg", "notes": "not a logo"}}
    out = svg2ae.brand_logos(pack, tmp_path)
    assert list(out) == ["mark"] and out["mark"]["items"][0]["fill"] == "#010203"
    listed = svg2ae.brand_logos({"logos": [{"name": "m", "svg": "logo.svg"}]}, tmp_path)
    assert list(listed) == ["m"]
