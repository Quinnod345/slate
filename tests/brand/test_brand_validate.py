"""`slate brand validate`: the rules a pack must keep, each refusal proven."""

from __future__ import annotations

import json
from pathlib import Path

from slate import jsonio, paths
from slate.brand import pack, validate

from brand_helpers import write_git


def _gate(results, name):
    return next(r for r in results if r.gate == f"brand.{name}")


def _edit(folder: Path, fn) -> None:
    raw = jsonio.read(folder / "brand.json")
    fn(raw)
    (folder / "brand.json").write_text(json.dumps(raw, indent=1))


def test_a_font_file_anywhere_in_a_pack_fails(studio):
    (studio / "logos" / "Brand-Bold.otf").write_bytes(b"OTTO")
    (studio / "fonts").mkdir()
    (studio / "fonts" / "brand.woff2").write_bytes(b"wOF2")
    g = _gate(validate.validate("studio"), "fonts")
    assert g.status == "FAIL" and "font files" in g.summary
    assert g.details["font_files"] == ["fonts/brand.woff2", "logos/Brand-Bold.otf"]


def test_fonts_are_named_by_reference_with_a_licence(studio):
    _edit(studio, lambda r: r["type"]["families"].__setitem__(0, {
        "role": "sans", "family": "Brand Sans", "weights": [400], "licence": "per brand",
        "source": {"kind": "kit", "url": "https://fonts.example.invalid/brand-sans.woff2"}}))
    g = _gate(validate.validate("studio"), "fonts")
    assert g.status == "FAIL" and "is a font file" in g.summary
    _edit(studio, lambda r: r["type"]["families"].__setitem__(0, {
        "role": "sans", "family": "Brand Sans", "weights": [400], "source": "installed"}))
    g = _gate(validate.validate("studio"), "fonts")
    assert g.status == "FAIL" and "no licence noted" in g.summary
    _edit(studio, lambda r: r["type"]["families"].__setitem__(0, {
        "role": "sans", "family": "Brand Sans", "weights": [400], "source": {"kind": "kit",
                                                                            "url": "https://use.example.invalid/k.css"},
        "licence": "unknown: to confirm"}))
    g = _gate(validate.validate("studio"), "fonts")
    assert g.status == "REVIEW" and "to confirm" in g.summary


def test_a_real_brand_inside_the_repo_fails(studio, monkeypatch):
    fake_repo = studio.parent
    monkeypatch.setattr(paths, "REPO", fake_repo)  # as if the store were inside Slate's repo
    g = _gate(validate.validate(str(studio)), "location")
    assert g.status == "FAIL" and "real brand inside Slate's repo" in g.summary


def test_company_material_never_sits_in_a_repository_with_a_remote(store):
    from slate.brand import create

    folder = Path(create.init("corp", company_material=True, organisation="Corp")["dir"])
    assert _gate(validate.validate("corp"), "location").status == "PASS"
    write_git(store, remote="origin")  # the whole store became a repository with a remote
    g = _gate(validate.validate("corp"), "location")
    assert g.status == "FAIL" and "stays on this Mac" in g.summary and g.details["remotes"] == ["origin"]
    # a brand the owner owns may live in a private repository
    create.init("mine")
    assert _gate(validate.validate("mine"), "location").status == "PASS"
    assert folder.is_dir()


def test_motion_docs_that_drift_from_brand_json_fail(studio):
    assert _gate(validate.validate("studio"), "motion").status == "PASS"
    _edit(studio, lambda r: r["motion"]["durations"]["base"].__setitem__("seconds", 0.45))
    g = _gate(validate.validate("studio"), "motion")
    assert g.status == "FAIL" and "no longer match" in g.summary and g.details["docs"]["motion.json"] == "stale"


def test_a_motion_law_by_path_fails_because_engines_read_brand_json_as_it_is(studio):
    _edit(studio, lambda r: r.__setitem__("motion", "motion.json"))
    g = _gate(validate.validate("studio"), "motion")
    assert g.status == "FAIL" and "must be inline" in g.summary


def test_an_unreadable_ease_fails_and_a_missing_lineage_is_reviewed(studio):
    _edit(studio, lambda r: r["motion"]["eases"].__setitem__("wobble", {"kind": "boing", "lineage": "?"}))
    assert _gate(validate.validate("studio"), "motion").status == "FAIL"
    _edit(studio, lambda r: r["motion"]["eases"].__setitem__("wobble", [0.3, 0.1, 0.3, 1]))
    from slate.brand import motiondoc

    motiondoc.write(studio, jsonio.read(studio / "brand.json"))
    g = _gate(validate.validate("studio"), "motion")
    assert g.status == "REVIEW" and "no lineage for wobble" in g.summary


def test_logos_must_be_svgs_the_engines_can_build(studio):
    (studio / "logos" / "mark.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                                               '<rect id="a" width="4" height="4"/></svg>')
    g = _gate(validate.validate("studio"), "logos")
    assert g.status == "FAIL" and "keystone 'keystone' isn't a shape id" in g.summary
    (studio / "logos" / "mark.svg").write_text("not an svg")
    assert _gate(validate.validate("studio"), "logos").status == "FAIL"
    _edit(studio, lambda r: r["logos"].__setitem__("gone", {"file": "logos/gone.svg"}))
    assert any("isn't in the pack" in e for e in _gate(validate.validate("studio"), "logos").evidence)


def test_a_declared_text_pair_below_its_bar_fails(studio):
    _edit(studio, lambda r: r["contrast_pairs"].append({"fg": "neutral.300", "bg": "page", "role": "body"}))
    g = _gate(validate.validate("studio"), "contrast")
    assert g.status == "FAIL" and "neutral.300 on page" in g.summary


def test_palette_values_and_roles_must_be_colours(studio):
    _edit(studio, lambda r: r["palette"].__setitem__("mud", "brownish"))
    assert "isn't a colour" in _gate(validate.validate("studio"), "palette").summary
    _edit(studio, lambda r: (r["palette"].pop("mud"), r["palette"]["roles"].__setitem__("page", "nowhere")))
    assert "isn't a token" in _gate(validate.validate("studio"), "palette").summary


def test_the_uploads_decision_is_stated_one_way(studio):
    _edit(studio, lambda r: (r.__setitem__("allow_generator_uploads", True),
                             r["grounding"].__setitem__("uploads_allowed", False)))
    g = _gate(validate.validate("studio"), "uploads")
    assert g.status == "FAIL" and not pack.uploads_allowed(pack.load("studio"))


def test_brand_skills_need_a_skill_md_with_name_and_description(studio):
    (studio / "skills" / "half").mkdir()
    assert _gate(validate.validate("studio"), "skills").status == "FAIL"
    (studio / "skills" / "half" / "SKILL.md").write_text("---\nname: half\n---\nno description\n")
    assert "name and a description" in _gate(validate.validate("studio"), "skills").summary


def test_a_voice_that_breaks_the_voice_policy_fails(studio):
    _edit(studio, lambda r: r["voice"].update(source="clone", clone_of="a colleague", clone_kind="instant"))
    g = _gate(validate.validate("studio"), "voice")
    assert g.status == "FAIL" and "voice policy" in g.summary


def test_sound_materials_and_banned_timbres_cannot_overlap(studio):
    _edit(studio, lambda r: r["sound"]["banned_timbres"].append("clave"))
    assert "both a material and a banned timbre" in _gate(validate.validate("studio"), "sound").summary


def test_validate_all_sees_every_pack(store, studio):
    everything = validate.validate_all()
    assert {"_template", "demo", "studio"} <= set(everything)
