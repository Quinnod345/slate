"""The two packs Slate ships: brands/_template (every key, empty) and brands/demo (an invented neutral studio)."""

from __future__ import annotations

import json
from pathlib import Path

from slate import jsonio, paths
from slate.brand import motiondoc, onboard, pack, validate

TEMPLATE = paths.BRANDS_REPO / "_template"
DEMO = paths.BRANDS_REPO / "demo"
SCHEMA_KEYS = set(json.loads((paths.SCHEMAS_DIR / "brand.schema.json").read_text())["properties"])


def test_repo_holds_only_the_neutral_packs():
    packs = sorted(p.name for p in paths.BRANDS_REPO.iterdir() if (p / "brand.json").is_file())
    assert packs == ["_template", "demo"]


def test_both_packs_match_the_schema():
    for folder in (TEMPLATE, DEMO):
        assert jsonio.validate(jsonio.read(folder / "brand.json"), "brand") == [], folder.name


def test_template_has_every_key_and_empty_values():
    raw = jsonio.read(TEMPLATE / "brand.json")
    assert set(raw) == SCHEMA_KEYS, set(raw) ^ SCHEMA_KEYS
    assert raw["references"] == {"truth": None, "look_motion": None, "voice": None, "sound": None}
    assert raw["palette"]["roles"] == {} and raw["type"]["families"] == [] and raw["logos"] == {}
    assert raw["motion"]["eases"] == {} and raw["truth"]["approved_claims"] == []
    assert raw["allow_generator_uploads"] is False
    readme = (TEMPLATE / "README.md").read_text()
    for key in SCHEMA_KEYS:  # every key is documented
        assert f"`{key}`" in readme, key


def test_both_packs_pass_every_check():
    for folder in (TEMPLATE, DEMO):
        results = validate.validate(folder)
        assert {r.status for r in results} == {"PASS"}, [(r.gate, r.status, r.summary) for r in results]


def test_demo_is_complete_and_neutral():
    p = pack.load("demo")
    assert p["ownership"]["kind"] == "neutral" and p["display_name"] == "Demo Studio"
    fams = p["type"]["families"]
    assert {f["family"] for f in fams} == {"Public Sans"} and all(f["source"] == "google" for f in fams)
    assert all("Open Font License" in f["licence"] and f["postscript"] for f in fams)
    assert [lg["name"] for lg in pack.logos(p)] == ["mark", "mark-inverse"] and all(lg["exists"] for lg in pack.logos(p))
    mark = (DEMO / "logos" / "mark.svg").read_text()
    assert 'id="keystone"' in mark and "<text" not in mark
    law = pack.motion(p)
    assert set(law["roles"]) == {"entrance", "move", "state", "exit", "relayout"}
    assert all(isinstance(e, dict) and e.get("lineage") for e in law["eases"].values())
    assert law["overshoot"]["allowed"] is False and law["signature"]["name"]
    # the sound palette: proof 2's taste as a starting point, no chimes
    s = p["sound"]
    assert "chimes" in s["banned_timbres"] and any("wood" in m for m in s["materials"])
    assert any("pad" in i for i in s["instrumentation"]) and any("felt" in i for i in s["instrumentation"])
    assert s["palette"]["levels"]["lu_vs_voice"] == {"ui": -17.0, "fx": -11.2}  # proof 2's levels, stated
    # placeholder references, marked unapproved
    for key in pack.REFERENCES:
        rec = p["references"][key]
        assert rec["placeholder"] is True and rec["approved_by"] is None and rec["sha256"] is None
    st = pack.status(p)
    assert st["status"] == "needs onboarding" and st["missing"] == list(pack.REFERENCES)
    assert st["next"]["command"].startswith("slate brand init")  # the repo's pack is copied, never onboarded


def test_demo_generated_docs_match_brand_json():
    raw = jsonio.read(DEMO / "brand.json")
    assert motiondoc.check(DEMO, raw) == {"motion.json": "current", "MOTION.md": "current"}
    assert (DEMO / onboard.TRUTH_MD).read_text() == onboard.render_truth(raw)
    assert jsonio.read(DEMO / "motion.json")["eases"] == raw["motion"]["eases"]


def test_every_module_reads_the_demo_cleanly():
    from slate.ae import spec
    from slate.motion import eases
    from slate.sound.effects import palette as sound_palette
    from slate.voice import profile

    p = pack.load("demo")
    law = eases.law_from(pack.motion(p))
    assert law.problems == [] and not law.neutral
    prof = profile.load(p)
    assert prof["voice_id"] and profile.policy_problems(prof) == []
    pal = sound_palette.from_brand(p)
    assert pal["from_brand"] and "pitched risers" in pal["banned_timbres"]
    section, warnings = spec.brand_section({"brand": "demo"})
    assert warnings == [] and section["fonts"]["display"]["postscript"] == "PublicSans-Bold"
    assert set(section["eases"]) == set(law.eases)


def test_demo_skill_is_where_brand_taste_lives():
    sk = pack.skills(pack.load("demo"))
    assert [s["name"] for s in sk] == ["demo-studio-taste"] and sk[0]["description"]
    assert Path(sk[0]["folder"], "SKILL.md").is_file()
