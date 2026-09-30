"""Brand onboarding: each step's inputs, outputs and checklist (docs/PLAN.md §4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slate import jsonio, paths
from slate.brand import create, motiondoc, onboard, pack
from slate.errors import Refused, UsageError

from brand_helpers import make_png, minimal_pdf, needs_ffmpeg, write_app, write_git, write_proof, write_wav, speechy


def _items(res):
    return {i["id"]: i for i in res["items"]}


# ----------------------------------------------------------------------------------------------------------- truth
def test_truth_step_records_sources_authority_and_claims_and_writes_truth_md(fresh, tmp_path):
    code = tmp_path / "product"
    code.mkdir()
    write_git(code, commit="feedface" * 5)
    res = onboard.truth("fresh", source=str(code), source_id="app", authority="Pat Lee", authority_role="Head of brand",
                        claims=["Fresh keeps every recipe in one place."], claim_source="app",
                        banned=["revolutionary"], never=["Fresh has a mobile app"])
    it = _items(res)
    assert it["sources"]["done"] and "feedface" * 5 in jsonio.read(fresh / "brand.json")["truth"]["sources"][0]["pin"]
    assert it["authority"]["done"] and it["claims"]["done"] and it["banned"]["done"] and it["reference"]["done"]
    assert not it["approved"]["done"] and res["ready_to_approve"]
    assert "slate brand approve fresh truth --file onboarding/truth/TRUTH.md --by \"Pat Lee\"" == it["approved"]["next"]
    md = (fresh / onboard.TRUTH_MD).read_text()
    assert "Pat Lee" in md and "Fresh keeps every recipe in one place." in md and "revolutionary" in md
    assert md == onboard.render_truth(jsonio.read(fresh / "brand.json"))
    checklist = (fresh / "onboarding" / "truth" / "CHECKLIST.md").read_text()
    assert "- [x] Sources of truth" in checklist and "- [ ] Approved by the claim authority" in checklist
    assert jsonio.read(fresh / "onboarding" / "truth" / "step.json")["reference"] == "truth"
    assert (fresh / "onboarding" / "CHECKLIST.md").is_file()


def test_truth_md_doesnt_change_when_nothing_changed(fresh):
    onboard.truth("fresh", authority="Pat Lee", claims=["One claim."])
    first = (fresh / onboard.TRUTH_MD).read_bytes()
    onboard.truth("fresh")
    assert (fresh / onboard.TRUTH_MD).read_bytes() == first  # re-running never un-approves an approved file


def test_a_brand_the_owner_doesnt_own_names_its_authority_at_that_organisation(store):
    create.init("partner", organisation="Partner Co")
    res = onboard.truth("partner", authority="Sam Doe")
    assert not _items(res)["authority"]["done"] and "Partner Co" in _items(res)["authority"]["evidence"]
    res = onboard.truth("partner", authority_org="Partner Co")
    assert _items(res)["authority"]["done"]


def test_unpinned_and_unsourced_truth_stays_open(fresh):
    res = onboard.truth("fresh", source="https://fresh.example.invalid/about", source_id="site",
                        claims=["Uncited claim."])
    it = _items(res)
    assert it["sources"]["done"]  # a URL pins itself to the date it was read
    assert not it["claims"]["done"] and "without a source" in it["claims"]["evidence"]
    res = onboard.truth("fresh", source="work:~/code/corp", source_id="corp-code")
    assert not _items(res)["sources"]["done"] and "not pinned: corp-code" in _items(res)["sources"]["evidence"]


def test_truth_import_merges_a_draft(fresh, tmp_path):
    draft = tmp_path / "truth.json"
    draft.write_text(json.dumps({"truth": {"sources": [{"id": "kb", "kind": "document", "where": "handbook",
                                                        "pin": "2026-09-30"}],
                                           "approved_claims": [{"id": "c1", "text": "Claim one.", "source": "kb"}],
                                           "claim_authority": {"name": "Pat Lee"}, "sample_data": {"x": 1}}}))
    res = onboard.truth("fresh", import_file=str(draft))
    it = _items(res)
    assert it["sources"]["done"] and it["claims"]["done"] and it["authority"]["done"] and it["sample"]["done"]


def test_onboarding_never_runs_on_the_repo_packs():
    with pytest.raises(Refused) as exc:
        onboard.truth("demo", authority="Someone")
    assert "neutral pack" in exc.value.message and "--from-pack demo" in exc.value.hint
    assert onboard.overview("demo")["status"] == "needs onboarding"  # reading is fine


# ------------------------------------------------------------------------------------------------------------ look
def test_look_step_reads_the_products_own_design_language_in_place(fresh, tmp_path):
    app = write_app(tmp_path / "kettle")
    write_git(app, commit="abc123" * 6 + "abcd")
    res = onboard.look("fresh", from_app=[str(app)])
    inputs = jsonio.read(fresh / onboard.LOOK_INPUTS)
    (sid, src), = inputs["sources"].items()
    found = src["found"]
    names = {e["name"] for e in found["eases"]}
    assert {"ease-out-soft", "ease-in-out-calm", "ease-snap", "ease-in-exit", "ease-spring", "enter-soft",
            "arriveEase", "sheetArrive", "chipSnap", "fade"} <= names
    assert "junk-ease" not in names  # dependencies are skipped
    by = {e["name"]: e for e in found["eases"]}
    assert by["ease-out-soft"]["bezier"] == [0.22, 1.0, 0.36, 1.0] and "tokens.css" in by["ease-out-soft"]["lineage"]
    assert by["sheetArrive"]["kind"] == "spring0" and by["chipSnap"]["peak"] > 1.0
    assert {d["name"]: d["seconds"] for d in found["durations"]} == pytest.approx(
        {"dur-quick": 0.18, "dur-base": 0.32, "dur-slow": 0.6, "stagger": 0.03})
    colours = {c["name"]: c["value"] for c in found["colours"]}
    assert colours["paper"] == "#F6F3EE" and colours["Leaf"] == "#2F6B4F" and colours["brand"] == "#2F6B4F"
    assert {a["name"]: a["target"] for a in found["aliases"]}["accent"] == "kettle-500"
    fams = {f["family"] for f in found["fonts"]}
    assert "Kettle Grotesk" in fams and "KettleGrotesk-Bold" in fams
    assert src["read"] == "in_place" and src["pin"] == "abc123" * 6 + "abcd"
    # the checklist: components and product motion are in
    it = _items(res)
    assert it["components"]["done"] and it["product_motion"]["done"]
    assert not it["reference_films"]["done"] and not it["moodboards"]["done"] and not it["approved"]["done"]


def test_look_step_proposes_a_law_one_ease_per_job_and_adopts_it_when_there_is_none(fresh, tmp_path):
    onboard.look("fresh", from_app=[str(write_app(tmp_path / "kettle"))])
    proposal = jsonio.read(fresh / onboard.LOOK_PROPOSAL)
    roles = proposal["roles"]
    assert roles["entrance"] == "ease-out-soft" and roles["move"] == "ease-in-out-calm"
    assert roles["state"] == "ease-snap" and roles["exit"] == "ease-in-exit"
    assert set(proposal["durations"]) == {"dur-quick", "dur-base", "dur-slow"}
    assert proposal["staggers"] == pytest.approx({"char": 0.06, "word": 0.09, "line": 0.12, "block": 0.18})
    assert "stagger" in proposal["stagger_notes"] and proposal["candidates"]
    raw = jsonio.read(fresh / "brand.json")
    assert raw["motion"]["roles"] == roles and all(e.get("lineage") for e in raw["motion"]["eases"].values())
    from slate.motion import eases as kit

    assert kit.law_from(raw["motion"]).problems == []
    assert motiondoc.check(fresh, raw) == {"motion.json": "current", "MOTION.md": "current"}
    assert "ease-out-soft" in (fresh / "MOTION.md").read_text()
    # palette and type filled from the product (never over what a person set)
    assert raw["palette"]["roles"]["accent"] == "kettle-500" and raw["palette"]["roles"]["page"] == "paper"
    fams = {f["family"]: f for f in raw["type"]["families"]}
    assert "Kettle Grotesk" in fams and fams["Kettle Grotesk"]["licence"].startswith("unknown")


def test_a_second_run_keeps_the_law_and_adopt_replaces_it(fresh, tmp_path):
    app = write_app(tmp_path / "kettle")
    onboard.look("fresh", from_app=[str(app)])
    raw = jsonio.read(fresh / "brand.json")
    raw["motion"]["signature"] = {"name": "pour", "what": "the kettle pours"}
    raw["motion"]["eases"]["ease-out-soft"]["job"] = "entrances, tuned by a person"
    (fresh / "brand.json").write_text(json.dumps(raw))
    onboard.look("fresh", from_app=[str(app)])
    kept = jsonio.read(fresh / "brand.json")["motion"]
    assert kept["eases"]["ease-out-soft"]["job"] == "entrances, tuned by a person"
    onboard.look("fresh", adopt=True)
    adopted = jsonio.read(fresh / "brand.json")["motion"]
    assert adopted["eases"]["ease-out-soft"]["job"] == "entrance" and adopted["signature"]["name"] == "pour"


def test_reference_films_measured_with_fit_ease(fresh, tmp_path):
    import numpy as np

    from slate.motion.eases import bezier_at

    t = np.linspace(0, 0.6, 37)
    v = 100 + 400 * np.array([bezier_at([0.42, 0.13, 0.27, 0.9], x / 0.6) for x in t])
    one = tmp_path / "film_a.json"
    one.write_text(json.dumps({"moves": {"lift": {"t": t.tolist(), "v": v.tolist(), "source": "film-a.mp4 1.2s"}}}))
    two = tmp_path / "film_b.json"
    two.write_text(json.dumps({"push": {"bezier": [0.45, 0.87, 0.14, 0.91], "rms": 0.024, "duration_s": 0.53,
                                        "source": "film-b.mp4 0:01"}, "pull": {"error": "blurred"}}))
    res = onboard.look("fresh", measured=[str(one), str(two)])
    measured = jsonio.read(fresh / onboard.LOOK_INPUTS)["measured"]
    # fit_ease measures the move between 2% and 98% of its travel, so the shape matches, not the exact parameters
    for u in (0.25, 0.5, 0.75):
        assert bezier_at(measured["lift"]["bezier"], u) == pytest.approx(bezier_at([0.42, 0.13, 0.27, 0.9], u), abs=0.05)
    assert measured["lift"]["fit_rms"] < 0.01
    assert "fit_ease" in measured["lift"]["lineage"] and measured["push"]["fit_rms"] == 0.024
    assert _items(res)["reference_films"]["done"]
    assert any("pull" in n and "blurred" in n for n in res["notes"])


def test_moodboards_are_recorded_by_direction_and_grounding(fresh, tmp_path):
    a = make_png(tmp_path / "a1.png")
    b = make_png(tmp_path / "b1.png", (20, 40, 60))
    onboard.look("fresh", moodboards=[str(a)], direction="warm paper", grounded_in="higgsfield:kit-123")
    res = onboard.look("fresh", moodboards=[str(b)], direction="night", grounded_in="elevenlabs:kit-9")
    boards = jsonio.read(fresh / "brand.json")["look"]["moodboards"]
    assert [bd["direction"] for bd in boards] == ["warm paper", "night"]
    assert (fresh / boards[0]["files"][0]).is_file()  # kept in the pack
    assert _items(res)["moodboards"]["done"]
    with pytest.raises(UsageError):
        onboard.look("fresh", moodboards=[str(a)])  # a direction is required


def test_work_code_is_read_from_stdin_and_never_written(fresh, tmp_path):
    from brand_helpers import CSS

    res = onboard.look("fresh", from_app=["work:~/code/corp-app"])
    assert jsonio.read(fresh / onboard.LOOK_INPUTS)["pending"]
    res = onboard.look("fresh", stdin_text=CSS, stdin_as="work:~/code/corp-app/src/tokens.css",
                       source_pin="c0ffee" * 6 + "c0ff")
    inputs = jsonio.read(fresh / onboard.LOOK_INPUTS)
    src = inputs["sources"]["stdin-tokens-css"]
    assert src["where"] == "WORK:~/code/corp-app/src/tokens.css" and src["read"] == "in_place"
    assert src["pin"].startswith("c0ffee") and inputs["pending"] == []
    assert src["found"]["eases"] and "cubic-bezier" not in json.dumps(
        {k: v for k, v in src.items() if k != "found"})
    for p in fresh.rglob("*"):
        assert p.suffix != ".css"  # no code file landed in the pack
    assert _items(res)["product_motion"]["done"]


def test_guidelines_pdf_is_read_in_place(fresh, tmp_path):
    from slate import procs

    if not (procs.which("osascript") or procs.which("pdftotext")):
        pytest.skip("no PDF text reader on this machine")
    pdf = minimal_pdf(tmp_path / "guidelines.pdf", ["Brand colours", "Harbour Blue #1F4E79", "Sand RGB 230, 214, 180",
                                                    "Headings: Fraunces Bold"])
    res = onboard.look("fresh", from_pdf=str(pdf))
    src = next(s for s in jsonio.read(fresh / onboard.LOOK_INPUTS)["sources"].values() if s["kind"] == "guidelines")
    values = {c["value"] for c in src["found"]["colours"]}
    assert {"#1F4E79", "#E6D6B4"} <= values and src["read"] == "in_place"
    assert any(f["family"] == "Fraunces" for f in src["found"]["fonts"])
    assert not list(fresh.rglob("*.pdf"))  # never copied
    assert _items(res)["components"]["done"]


def test_website_in_replay_mode_needs_a_saved_page_or_a_recorded_fixture(fresh, tmp_path, monkeypatch):
    from slate.errors import MissingPrerequisite

    with pytest.raises(MissingPrerequisite) as exc:
        onboard.look("fresh", from_website="https://example.invalid/")
    assert "--live" in exc.value.hint
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text('<html><head><link rel="stylesheet" href="css/main.css">'
                                     '<link href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;700" '
                                     'rel="stylesheet"></head><body></body></html>')
    (site / "css").mkdir()
    (site / "css" / "main.css").write_text(":root { --brand-ink: #101820; --ease-enter: cubic-bezier(0.2,0.8,0.2,1); }")
    res = onboard.look("fresh", from_website=str(site))
    src = next(s for s in jsonio.read(fresh / onboard.LOOK_INPUTS)["sources"].values() if s["kind"] == "website")
    assert {c["value"] for c in src["found"]["colours"]} == {"#101820"}
    assert any(f["family"] == "Public Sans" and f["source"] == "google" for f in src["found"]["fonts"])
    assert res["step"] == "look"
    # a recorded fixture answers in replay mode
    fixtures = tmp_path / "fixtures"
    monkeypatch.setenv("SLATE_FIXTURES", str(fixtures))
    from slate import replay

    kit_url = "https://fonts-kit.example.invalid/abc123.css"
    replay.record("web", replay.request_key("web", "fetch", {"url": "https://example.invalid/"}),
                  {"html": f'<link rel="stylesheet" href="{kit_url}"><style>:root{{--accent:#AA3300}}</style>',
                   "stylesheets": {kit_url: "/* kit */ @font-face { font-family: 'Brand Serif'; "
                                            "src: url(https://fonts-kit.example.invalid/f.woff2) format('woff2'); }"}})
    onboard.look("fresh", from_website="https://example.invalid/")
    srcs = jsonio.read(fresh / onboard.LOOK_INPUTS)["sources"]
    assert any(c["value"] == "#AA3300" for s in srcs.values() for c in s["found"]["colours"])
    kit_fonts = [f for s in srcs.values() for f in s["found"]["fonts"] if f.get("family") == "Brand Serif"]
    assert kit_fonts and kit_fonts[0]["source"] == {"kind": "kit", "url": kit_url}  # named by the kit's URL


def test_review_entry_registers_the_brands_google_fonts():
    src = onboard.review_entry_source([("Public Sans", "@remotion/google-fonts/PublicSans")], Path("/kit"))
    assert "import {loadFont as loadFont0} from '@remotion/google-fonts/PublicSans';" in src
    assert 'registerGoogleFont("Public Sans", loadFont0);' in src
    assert src.index("registerGoogleFont(") < src.index("registerRoot(KitReviewRoot)")
    assert onboard.google_families(pack.load("demo")) == ["Public Sans"]


def test_review_sheet_refuses_cleanly_without_an_engine(studio, monkeypatch, tmp_path):
    monkeypatch.setenv("SLATE_ENGINE_DIR", str(tmp_path / "no-engine"))
    res = onboard.look("studio", sheet=True)
    assert "sheet_error" in res and any("review sheet didn't render" in n for n in res["notes"])
    assert not _items(res)["kit_sheet"]["done"]


# ----------------------------------------------------------------------------------------------------------- voice
def test_voice_step_records_the_voice_and_its_take(studio, tmp_path):
    take = write_wav(tmp_path / "take.wav", speechy(3.0))
    res = onboard.voice("studio", voice_id="library-voice-1", voice_name="Narrator", model="tts-model-x", seed=42,
                        settings='{"stability": 0.4, "similarity_boost": 0.8}', source="library",
                        lexicon=["Studio"], take=str(take))
    it = _items(res)
    assert it["voice"]["done"] and it["policy"]["done"] and it["pinned"]["done"] and it["take"]["done"]
    v = jsonio.read(studio / "brand.json")["voice"]
    assert v["voice_id"] == "library-voice-1" and v["model"] == "tts-model-x" and v["seed"] == 42
    assert v["settings"] == {"stability": 0.4, "similarity_boost": 0.8} and "Studio" in v["lexicon"]
    rec = jsonio.read(studio / onboard.VOICE_TAKE)
    assert rec["sha256"] == jsonio.sha256(take) and rec["voice"]["seed"] == 42
    with pytest.raises(UsageError):
        onboard.voice("studio", settings="not json")


def test_a_clone_of_someone_else_fails_the_voice_policy(studio):
    res = onboard.voice("studio", voice_id="someone", source="clone")
    assert not _items(res)["policy"]["done"]


# ----------------------------------------------------------------------------------------------------------- sound
def test_sound_step_measures_the_proof_against_the_voice(studio, tmp_path):
    mix, stems = write_proof(tmp_path, seconds=12.0)
    res = onboard.sound("studio", proof=str(mix), stems=str(stems), key="G major", tempo="80-92",
                        materials=["wood block"], banned_timbres=["gong"], motif_instrument="felt piano")
    it = _items(res)
    assert it["palette"]["done"] and it["proof"]["done"] and it["levels"]["done"] and res["ready_to_approve"]
    s = jsonio.read(studio / "brand.json")["sound"]
    assert s["key"] == "G major" and s["tempo"] == [80.0, 92.0] and "gong" in s["banned_timbres"]
    assert set(s["reference_levels"]) == {"ui", "fx"} and s["reference_levels"]["ui"] < s["reference_levels"]["fx"] < 0
    assert s["reference_measured_from"]["sha256"] == jsonio.sha256(mix)
    lv = jsonio.read(studio / onboard.SOUND_LEVELS)
    assert lv["duration_s"] == pytest.approx(12.0, abs=0.01) and lv["method"] == "stems"
    # the sound module reads the brand's own palette over the starting one
    from slate.sound.effects import palette

    pal = palette.from_brand(pack.load("studio"))
    assert "gong" in pal["banned_timbres"] and "wood block" in pal["materials"] and pal["from_brand"]


def test_sound_levels_against_a_voice_file(studio, tmp_path):
    from brand_helpers import tone

    vo = speechy(12.0, 0.3)
    bed = tone(12.0, 300, 0.03)
    voice = write_wav(tmp_path / "vo.wav", vo)
    mix = write_wav(tmp_path / "mix.wav", vo + bed)
    res = onboard.sound("studio", proof=str(mix), voice=str(voice))
    lvl = jsonio.read(studio / "brand.json")["sound"]["reference_levels"]
    assert set(lvl) == {"bed"} and -40 < lvl["bed"] < -5
    assert _items(res)["levels"]["done"]


def test_a_proof_outside_10_to_20_seconds_stays_open(studio, tmp_path):
    mix, stems = write_proof(tmp_path, seconds=6.0)
    res = onboard.sound("studio", proof=str(mix), stems=str(stems))
    assert not _items(res)["proof"]["done"] and "needs 10–20 s" in _items(res)["proof"]["evidence"]


def test_step_flags_on_the_wrong_step_are_usage_errors(studio):
    from slate.cli import run_cli

    code, out, err = run_cli(["brand", "onboard", "studio", "--step", "voice", "--claim", "x", "--json"])
    assert code == 2 and "--claim belongs to --step truth" in json.loads(out)["message"]


def test_overview_lists_all_four_steps(studio):
    ov = onboard.overview("studio")
    assert list(ov["steps"]) == ["truth", "look", "voice", "sound"] and ov["status"] == "needs onboarding"
    assert ov["steps"]["truth"]["next"].startswith("slate brand onboard studio --step truth")


# --------------------------------------------------------------------------------------------- analyze, tokens
def _analysis(film: str, moves: list[dict]) -> dict:
    """The shape slate analyze writes (analysis.json): video metadata and eases.moves as a list."""
    return {"generated": "do not edit by hand: slate analyze", "video": {"name": film, "sha256": "ab" * 32},
            "eases": {"moves": moves, "skipped": []}}


def test_measured_accepts_slate_analyze_output(fresh, tmp_path):
    for film in ("ref-a.mp4", "ref-b.mp4"):
        f = tmp_path / f"{film}.analysis.json"
        f.write_text(json.dumps(_analysis(film, [
            {"name": "move-01", "bezier": [0.4, 0.1, 0.25, 0.95], "rms": 0.004, "duration_s": 0.4,
             "lineage": {"tool": "slate analyze --eases", "video": film, "window_s": [1.0, 1.4]}}])))
        res = onboard.look("fresh", measured=[str(f)])
    measured = jsonio.read(fresh / onboard.LOOK_INPUTS)["measured"]
    assert set(measured) == {"ref-a-move-01", "ref-b-move-01"}
    assert "measured from ref-a.mp4 1.00–1.40 s" in measured["ref-a-move-01"]["lineage"]
    assert _items(res)["reference_films"]["done"]  # two moves from two different films


def test_reference_films_run_through_slate_analyze(fresh, tmp_path, monkeypatch):
    import slate.analyze as analyze

    calls = []

    def fake_run(video, **kw):
        calls.append((Path(video).name, kw.get("steps")))
        out = tmp_path / "analysis" / Path(video).stem
        out.mkdir(parents=True, exist_ok=True)
        (out / "analysis.json").write_text(json.dumps(_analysis(Path(video).name, [
            {"name": "move-01", "bezier": [0.2, 0.8, 0.3, 1], "rms": 0.006, "duration_s": 0.5,
             "lineage": {"video": Path(video).name, "window_s": [2.0, 2.5]}}])))
        return {"out": str(out)}

    monkeypatch.setattr(analyze, "run", fake_run)
    film = tmp_path / "ref.mov"
    film.write_bytes(b"not really a film")
    res = onboard.look("fresh", reference_films=[str(film)], analyze=True)
    assert calls == [("ref.mov", ["cuts", "eases"])]
    ref = jsonio.read(fresh / "brand.json")["look"]["references"][0]
    assert ref["file"] == str(film.resolve()) and ref["analysis"]["moves"] == ["ref-move-01"]
    assert "ref-move-01" in jsonio.read(fresh / onboard.LOOK_INPUTS)["measured"]
    assert "1 move(s) measured" in " ".join(res["changes"])


def test_roles_come_from_token_names_the_kit_knows_and_pairs_are_proposed(fresh, tmp_path):
    app = tmp_path / "site"
    app.mkdir()
    (app / "tokens.css").write_text(":root { --background: #FAF7F2; --text: #1A1A1A; --primary: #1F5FAA; "
                                    "--muted: #6B6B6B; --card: #FFFFFF; }")
    res = onboard.look("fresh", from_app=[str(app)])
    raw = jsonio.read(fresh / "brand.json")
    roles = raw["palette"]["roles"]
    assert roles == {"page": "background", "surface": "card", "ink": "text", "ink3": "muted", "accent": "primary"}
    pairs = {(p["fg"], p["bg"], p["role"]) for p in raw["contrast_pairs"]}
    assert ("ink", "page", "body") in pairs and ("accent", "page", "large") in pairs
    assert all("proposed" in p["where"] for p in raw["contrast_pairs"])
    assert any("contrast pairs" in c for c in res["changes"])


def test_a_postscript_name_joins_its_family(fresh, tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "a.css").write_text('body { font-family: "Kettle Grotesk", sans-serif; }')
    (app / "B.swift").write_text('Text("x").font(.custom("KettleGrotesk-Bold", size: 17))')
    onboard.look("fresh", from_app=[str(app)])
    fams = jsonio.read(fresh / "brand.json")["type"]["families"]
    assert [f["family"] for f in fams] == ["Kettle Grotesk"] and fams[0]["postscript"] == "KettleGrotesk-Bold"


def test_app_screens_are_the_brands_own_components_kept_in_place(fresh, tmp_path):
    shot = make_png(tmp_path / "screens" / "home.png")
    res = onboard.look("fresh", app_screens=[str(shot)])
    refs = jsonio.read(fresh / "brand.json")["look"]["references"]
    assert refs == [{"file": str(shot.resolve()), "what": "app screen", "sha256": jsonio.sha256(shot)}]
    assert _items(res)["components"]["done"] and "1 app screen" in _items(res)["components"]["evidence"]
    assert not list(fresh.rglob("home.png"))  # recorded where it is, not copied


# ----------------------------------------------------------------------------------------------------------- spend
def test_onboarding_spend_bills_against_the_onboarding_budget(studio, monkeypatch):
    """The paid parts of onboarding (Voice Design, look tests) bill against ledger.format_budget('onboarding'), once
    per brand; the ledger refuses a call past it, and an autonomous run can't onboard a brand at all."""
    from slate import formats, ledger
    from slate.errors import SpendRefused

    ctx = onboard.billing("studio")
    assert ctx["brand"] == "studio" and ctx["format"] == "onboarding" and ctx["id"] == "onboarding-studio"
    assert onboard.billing(pack.load("studio")) == ctx  # a loaded pack says the same
    want = ledger.format_budget("onboarding")
    limits = ledger.film_limits(ctx)["providers"]
    assert limits["elevenlabs"]["budget"] == want["elevenlabs"] == formats.onboarding_budget()["elevenlabs"][
        "voice_design"][1]
    assert limits["higgsfield"]["budget"] == want["higgsfield"] == formats.onboarding_budget()["higgsfield"][1]

    monkeypatch.setenv("SLATE_FAKE_VENDORS", "0")  # the ledger only judges spend outside replay; nothing is called
    design = ledger.Estimate("elevenlabs", "voice_design", 3000.0, "credits", "test estimate")
    row = ledger.begin(design, film=ctx, mode="supervised")
    ledger.finish(row, actual=3000.0)
    assert onboard.spend("studio")["elevenlabs"]["spent"] == 3000.0
    with pytest.raises(SpendRefused, match="budget is 5000"):
        ledger.check_budget(ledger.Estimate("elevenlabs", "voice_design", 2500.0, "credits", "again"), film=ctx,
                            mode="supervised")
    other = onboard.billing(create.init("second", from_pack="demo")["dir"])
    ledger.check_budget(ledger.Estimate("elevenlabs", "voice_design", 2500.0, "credits", "another brand"),
                        film=other, mode="supervised")  # each brand has its own onboarding budget
    with pytest.raises(SpendRefused, match="four references approved"):
        ledger.check_budget(ledger.Estimate("higgsfield", "look_test", 5.0, "usd", "a look test"), film=ctx,
                            mode="autonomous")
    assert "onboarding spend: elevenlabs 3,000 of 5,000 credits" in onboard.spend_line(onboard.spend("studio"))


def test_the_overview_says_where_the_onboarding_budget_stands(studio):
    from slate.cli import run_cli

    code, out, err = run_cli(["brand", "onboard", "studio"])
    assert code == 0 and "onboarding spend: elevenlabs 0 of 5,000 credits; higgsfield $0 of $20" in out
    code, out, err = run_cli(["brand", "onboard", "studio", "--json"])
    data = json.loads(out)
    assert data["billing"]["id"] == "onboarding-studio" and data["spend"]["higgsfield"]["budget"] == 20
