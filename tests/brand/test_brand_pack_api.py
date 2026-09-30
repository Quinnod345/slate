"""slate.brand.pack, the interface other modules call (docs/ARCHITECTURE.md §12)."""

from __future__ import annotations

import json

import pytest

from slate import jsonio, paths
from slate.brand import pack
from slate.errors import UsageError


def _write(folder, data):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "brand.json").write_text(json.dumps(data))
    return folder


def test_find_by_name_path_and_brand_json(store):
    f = _write(store / "acme", {"name": "acme"})
    assert pack.find("acme") == f.resolve()
    assert pack.find(str(f)) == f.resolve() and pack.find(str(f / "brand.json")) == f.resolve()
    assert pack.find("demo") == (paths.BRANDS_REPO / "demo").resolve()  # the repo's neutral packs come first
    with pytest.raises(UsageError) as exc:
        pack.find("nobody")
    assert exc.value.exit_code == 2 and "slate brand init nobody" in exc.value.hint


def test_load_adds_the_folder_and_mirrors_the_uploads_decision(store):
    _write(store / "acme", {"name": "acme", "allow_generator_uploads": True})
    p = pack.load("acme")
    assert p["_dir"] == str((store / "acme").resolve()) and p["_source"] == "store"
    assert p["grounding"]["uploads_allowed"] is True  # what the generate stage reads
    assert pack.load(p) is p  # an already loaded pack passes through
    raw = jsonio.read(store / "acme" / "brand.json")
    assert "grounding" not in raw  # loading never writes


def test_uploads_default_off_and_a_disagreement_means_no():
    assert pack.uploads_allowed({}) is False
    assert pack.uploads_allowed({"allow_generator_uploads": True}) is True
    assert pack.uploads_allowed({"grounding": {"uploads_allowed": True}}) is True
    assert pack.uploads_allowed({"allow_generator_uploads": True, "grounding": {"uploads_allowed": False}}) is False


def test_generate_stage_refuses_uploads_unless_the_pack_allows_them(store, tmp_path):
    from slate.gen import policy

    _write(store / "closed", {"name": "closed"})
    _write(store / "open", {"name": "open", "allow_generator_uploads": True})
    asset = tmp_path / "logo.png"
    asset.write_bytes(b"x")
    assert policy.check_uploads(tmp_path, pack.load("closed"), [asset])  # refused, with the reason
    assert policy.check_uploads(tmp_path, pack.load("open"), [asset]) == []


def test_palette_is_flat_hex_with_roles_added():
    p = pack.load("demo")
    pal = pack.palette(p)
    assert pal["neutral.50"] == "#F3F0EA" and pal["clay.500"] == "#A4532F" and pal["chalk"] == "#FFFFFF"
    assert pal["page"] == "#F3F0EA" and pal["accent"] == "#A4532F" and pal["inverse"] == "#1F2D33"
    assert all(v.startswith("#") and len(v) in (7, 9) for v in pal.values())
    assert "roles" not in pal and "tones" not in pal and "notes" not in pal
    assert pack.palette_roles(p)["accentInk"] == "#7E3C20"


def test_palette_tokens_keep_names_and_roles_never_override_a_token():
    p = {"palette": {"ink": "#111111", "gray": {"900": "#222222"}, "roles": {"ink": "gray.900", "page": "#FAFAFA"}}}
    pal = pack.palette(p)
    assert pal["ink"] == "#111111"  # a token named ink wins over the role of that name
    assert pal["page"] == "#FAFAFA" and pal["gray.900"] == "#222222"


def test_contrast_pairs_are_measured_and_the_gate_agrees(tmp_path):
    from slate.qa import access

    p = pack.load("demo")
    pairs = pack.contrast_pairs(p)
    assert len(pairs) == 9 and all(r["ok"] for r in pairs)
    body = next(r for r in pairs if r["fg"] == "ink" and r["bg"] == "page")
    assert body["role"] == "body" and body["needs"] == 4.5 and body["ratio"] == pytest.approx(15.26, abs=0.01)
    large = next(r for r in pairs if r["role"] == "large" and r["fg"] == "accent")
    assert large["needs"] == 3.0
    # the accessibility gate reads the same pairs through this module and gets the same numbers
    gate_pairs, gate_palette, source = access.brand_pairs(p)
    rows, short = access.check_pairs(gate_pairs, gate_palette)
    assert source == "slate.brand.pack.contrast_pairs" and short == []
    assert [r["ratio"] for r in rows] == [r["ratio"] for r in pairs]


def test_contrast_pairs_report_what_falls_short_or_cant_be_read():
    p = {"palette": {"pale": "#DDDDDD", "white": "#FFFFFF"},
         "contrast_pairs": [{"fg": "pale", "bg": "white", "role": "body"}, {"fg": "nope", "bg": "white"},
                            {"fg": "#767676", "bg": "white", "role": "body"}]}
    rows = pack.contrast_pairs(p)
    assert rows[0]["ok"] is False and "below the 4.5:1 body" in rows[0]["problem"]
    assert rows[1]["ok"] is False and "isn't a colour or a palette token" in rows[1]["problem"]
    assert rows[2]["ok"] is True and rows[2]["ratio"] == pytest.approx(4.54, abs=0.01)


def test_truth_accessors():
    p = {"truth": {"banned_words": ["free", {"word": "cheap"}, "free", " "],
                   "approved_claims": ["Plain claim.", {"id": "c9", "text": "Sourced claim.", "source": "web"},
                                       {"text": ""}],
                   "never_claim": [{"text": "Invented quotes", "why": "the law"}, "Unbuilt features"],
                   "claim_authority": "A. Person"}}
    assert pack.banned_words(p) == ["free", "cheap"]
    claims = pack.approved_claims(p)
    assert claims == [{"id": "claim-1", "text": "Plain claim."}, {"source": "web", "id": "c9", "text": "Sourced claim."}]
    assert pack.never_claim(p) == ["Invented quotes", "Unbuilt features"]
    assert pack.claim_authority(p) == {"name": "A. Person"}
    # a pack that doesn't state its list is unknown (the gate is UNCHECKED); an empty list says there are none
    assert pack.banned_words({}) is None and not pack.declares_banned_words({})
    assert pack.banned_words({"truth": {"banned_words": []}}) == []
    assert pack.declares_banned_words({"truth": {"banned_words": []}})


def test_motion_is_a_copy_of_the_law():
    p = pack.load("demo")
    law = pack.motion(p)
    law["eases"]["arrive"]["bezier"][0] = 0.99
    assert pack.motion(p)["eases"]["arrive"]["bezier"][0] == 0.2
    assert pack.motion({}) == {}


def test_a_motion_path_is_readable_here_but_flagged(store):
    f = _write(store / "pathy", {"name": "pathy", "motion": "motion.json"})
    (f / "motion.json").write_text(json.dumps({"eases": {"in": [0.2, 0.8, 0.2, 1]}}))
    p = pack.load("pathy")
    assert p["motion"]["eases"]["in"] == [0.2, 0.8, 0.2, 1] and p["_problems"]


def test_other_modules_read_packs_through_this_interface(store):
    """The callers built before this module (state, qa, story, voice, sound, ae, engine, ledger, finish) find and read
    packs through these functions."""
    from slate.engine import bridge
    from slate.qa import brandinfo
    from slate.state import deps
    from slate.story import inputs

    film = store.parent / "films" / "f1"
    film.mkdir(parents=True)
    (film / "film.json").write_text(json.dumps({"id": "f1", "brand": "demo"}))
    assert deps.brand_find("demo") == pack.find("demo")
    assert deps.brand_load("demo")["name"] == "demo"
    assert deps.brand_missing_references(deps.brand_load("demo")) == list(pack.REFERENCES)
    got, source = brandinfo.load("demo")
    assert source == "slate.brand.pack" and brandinfo.banned_words(got) == pack.banned_words(got)
    assert [c["text"] for c in inputs.approved_claims(inputs.load_brand("demo"))] == \
        [c["text"] for c in pack.approved_claims(pack.load("demo"))]
    assert bridge.brand_path(film) == pack.find("demo")
    from slate import ledger

    assert ledger.brand_approved("demo") is False


def test_list_packs_sees_the_repo_and_the_store(store):
    _write(store / "acme", {"name": "acme"})
    _write(store / "demo", {"name": "demo"})
    rows = pack.list_packs()
    names = [(r["name"], r["where"], r["shadowed"]) for r in rows]
    assert ("demo", "repo", False) in names and ("acme", "store", False) in names
    assert ("demo", "store", True) in names  # a store pack named like a neutral one is hidden by it


def test_skills_are_listed_from_the_pack(store):
    f = _write(store / "acme", {"name": "acme"})
    (f / "skills" / "acme-taste").mkdir(parents=True)
    (f / "skills" / "acme-taste" / "SKILL.md").write_text("---\nname: acme-taste\ndescription: How Acme looks.\n---\n")
    (f / "skills" / "half-done").mkdir()
    sk = {s["name"]: s for s in pack.skills(pack.load("acme"))}
    assert sk["acme-taste"]["description"] == "How Acme looks." and sk["half-done"]["has_skill_md"] is False


def test_save_refuses_the_repo_packs():
    from slate.errors import Refused

    with pytest.raises(Refused):
        pack.save(paths.BRANDS_REPO / "demo", jsonio.read(paths.BRANDS_REPO / "demo" / "brand.json"))
