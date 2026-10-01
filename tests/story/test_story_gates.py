"""Every script gate, passing and failing (method stages 2–3; plan points P113, P114, P148)."""

from __future__ import annotations

import copy

import pytest
from storyfix import BRAND, SCRIPT, TRUTH

from slate.story.gates import check_script

FILM = {"targets": {"runtime_s": 15}, "language": "en"}


def gates(script=None, *, brand=None, truth=None, film=None, fmt=None) -> dict:
    results = check_script(copy.deepcopy(SCRIPT if script is None else script),
                           brand=copy.deepcopy(BRAND if brand is None else brand), fmt=fmt or {},
                           truth=copy.deepcopy(TRUTH if truth is None else truth), film=film or FILM,
                           default_enter_token="base")
    return {r.gate: r for r in results}


def shot(script, sid):
    return next(s for a in script["acts"] for s in a["shots"] if s["id"] == sid)


def test_the_neutral_script_passes_every_gate():
    got = gates()
    assert {g: r.status for g, r in got.items()} == {
        "script.structure": "PASS", "script.pace": "PASS", "script.read_time": "PASS", "script.on_screen": "PASS",
        "script.hero_lines": "PASS", "script.banned_words": "PASS", "script.runtime": "PASS", "script.numbers": "PASS",
        "script.claims": "PASS", "script.quotes": "PASS"}
    assert all(r.engine["name"] == "slate script build" for r in got.values())


# --- structure ---------------------------------------------------------------------------------------------------------

def test_structure_fails_on_duplicate_ids_and_a_silent_shot_without_a_length():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S03")["id"] = "S02"
    del shot(s, "S01")["dur"]
    got = gates(s)
    assert got["script.structure"].status == "FAIL"
    text = " ".join(got["script.structure"].evidence)
    assert "S02 is used twice" in text and "S01 has no voice" in text
    assert list(got) == ["script.structure"]  # nothing else is judged on a script that can't be planned


# --- pace (≤ 2.6 words a second) -------------------------------------------------------------------------------------------

def test_pace_fails_over_two_point_six_words_a_second():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S04").update(vo="Come early, bring a friend, and take the warm one home with you.", dur=3.0)
    got = gates(s)
    assert got["script.pace"].status == "FAIL"
    assert got["script.pace"].evidence[0].startswith("S04: 4.33 words a second (13 words in 3.00 s)")


def test_pace_ceiling_comes_from_the_format():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S04").update(vo="Come early and bring a friend.", dur=2.4)  # 6 words / 2.4 s = 2.5 w/s
    assert gates(s)["script.pace"].status == "PASS"
    assert gates(s, fmt={"pace": {"max_words_per_second": 2.2}})["script.pace"].status == "FAIL"


def test_planned_lengths_never_break_the_pace_ceiling():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S04").update(vo="One two three four five six seven eight nine ten eleven twelve.", hold=0)
    assert gates(s)["script.pace"].status == "PASS"


# --- read time (MOTION.md §7) -------------------------------------------------------------------------------------------

def test_read_time_fails_when_the_settled_hold_is_too_short():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S01").update(on_screen="Every loaf is shaped by hand before dawn", dur=2.0, enter_token="broad")
    got = gates(s)
    assert got["script.read_time"].status == "FAIL"
    # 8 words need max(1.5, 0.4 + 0.3 x 8) = 2.8 s; 2.0 − 0.8 − 0.6 leaves 0.6 s
    assert "S01: 8 on-screen words need 2.8 s settled, the shot gives 0.6 s" in got["script.read_time"].evidence[0]


def test_read_time_uses_the_brands_duration_scale_and_says_when_it_guesses():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S01").update(on_screen="Warm bread", dur=2.7, enter_token="quick")  # 2.7 − 0.3 − 0.225 = 2.175 ≥ 1.5
    assert gates(s)["script.read_time"].status == "PASS"
    shot(s, "S01")["enter_token"] = "mystery"
    got = gates(s)["script.read_time"]
    assert got.status == "PASS" and "mystery" in got.evidence[-1]


def test_a_number_on_screen_needs_two_seconds():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S01").update(on_screen="120 loaves", dur=2.9, enter_token="base")  # settled 2.9 − 0.875 = 2.025
    assert gates(s)["script.read_time"].status == "PASS"
    shot(s, "S01")["dur"] = 2.8  # settled 1.925 < 2.0
    assert gates(s)["script.read_time"].status == "FAIL"


def test_on_screen_density_caps_at_twelve_words():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S01").update(on_screen="one two three four five six seven eight nine ten eleven twelve thirteen", dur=9)
    got = gates(s)
    assert got["script.on_screen"].status == "FAIL"
    assert "13 on-screen words" in got["script.on_screen"].evidence[0]


def test_a_voice_matched_definition_may_pass_the_word_budget_and_needs_only_the_floor():
    """L-qa-D053: a definition the voice reads along as it lands, declared with its reason."""
    s = copy.deepcopy(SCRIPT)
    long = "one two three four five six seven eight nine ten eleven twelve thirteen"
    shot(s, "S01").update(on_screen=long, dur=3.5, enter_token="base")  # settled 3.5 − 0.875 = 2.625 < 0.4 + 3.9
    got = gates(s)
    assert got["script.on_screen"].status == "FAIL" and got["script.read_time"].status == "FAIL"
    shot(s, "S01")["reading"] = {"voice_matched": True}  # no reason: counts for nothing, and says so
    got = gates(s)
    assert got["script.on_screen"].status == "FAIL"
    assert any("without a reason" in e for e in got["script.on_screen"].evidence)
    shot(s, "S01")["reading"] = {"voice_matched": True, "reason": "the voice reads the definition as it lands"}
    got = gates(s)
    assert got["script.on_screen"].status == "PASS" and "voice-matched" in got["script.on_screen"].summary
    assert got["script.read_time"].status == "PASS"  # held to the 1.5 s floor
    assert any("voice-matched" in e for e in got["script.read_time"].evidence)


def test_a_surface_not_for_reading_isnt_timed():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S01").update(on_screen="Every loaf is shaped by hand before dawn", dur=2.0, enter_token="broad")
    shot(s, "S01")["reading"] = {"not_for_reading": True, "reason": "the report sits small as a closing object"}
    got = gates(s)["script.read_time"]
    assert got.status == "PASS" and any("not for reading" in e for e in got.evidence)
    shot(s, "S01")["reading"] = {"not_for_reading": ["Every loaf"], "reason": "its small print"}
    assert gates(s)["script.read_time"].status == "FAIL"  # a list is for the picture's QA, statement by statement


def test_the_reading_declaration_reaches_the_storyboard():
    from slate.story import builder

    class Shot:  # the planned shot's shape, as builder._shot_doc reads it
        id, dur, dur_source, vo, source_id, lead, hold, act_end, words, take = (
            "S01", 2.0, "script", "", "S01", 0.0, 0.0, False, 0, None)
        data = {"on_screen": "a definition", "reading": {"voice_matched": True, "reason": "read along"}}

    assert builder._shot_doc(Shot())["reading"] == {"voice_matched": True, "reason": "read along"}
    Shot.data = {**Shot.data, "hero_lines": ["One clock"]}  # and its hero lines, for the kit's hero role
    assert builder._shot_doc(Shot())["hero_lines"] == ["One clock"]


def test_a_format_whose_picture_carries_no_words_fails_any_on_screen_word():
    """formats/website-hero-loop.json says on_screen_max_words 0 ("the picture carries no words"): 0 is a real limit,
    not a missing one (it used to fall back to twelve)."""
    got = gates(fmt={"on_screen_max_words": 0})
    assert got["script.on_screen"].status == "FAIL" and "this format's picture carries none" in got["script.on_screen"].summary
    s = copy.deepcopy(SCRIPT)
    for a in s["acts"]:
        for sh in a["shots"]:
            sh["on_screen"] = ""
    assert gates(s, fmt={"on_screen_max_words": 0})["script.on_screen"].status == "PASS"


def test_the_scripts_own_density_rule_wins_over_the_formats():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S02").update(on_screen="Open from six every single day")  # six words of an eight-word line
    assert gates(s, fmt={"on_screen_max_words": 4})["script.on_screen"].status == "FAIL"
    s["rules"] = {"on_screen_max_words": 8}
    got = gates(s, fmt={"on_screen_max_words": 4})["script.on_screen"]
    assert got.status == "FAIL" and "75%" in got.evidence[0]  # inside the cap now, but 75% of what the voice says
    s["rules"] = {"on_screen_max_words": 8, "on_screen_max_vo_ratio": 0.8}
    assert gates(s, fmt={"on_screen_max_words": 4, "on_screen_max_vo_ratio": 0.4})["script.on_screen"].status == "PASS"


# --- on screen against the voice (at most 40%: keywords, not transcripts) ------------------------------------------------

def test_on_screen_text_stays_at_or_under_forty_percent_of_the_voice():
    """L-story-030: a shot's on-screen words are at most 40% of what its voice says (the films' ratio)."""
    s = copy.deepcopy(SCRIPT)
    shot(s, "S02").update(on_screen="Ovens warm by dawn")  # 4 words against 8 said: 50%, and not what's said
    got = gates(s)["script.on_screen"]
    assert got.status == "FAIL" and "40%" in got.summary
    assert got.evidence[0] == "S02: 4 on-screen words against 8 said (50%), over 40% (keywords, not transcripts)"
    shot(s, "S02").update(on_screen="Ovens at dawn")  # 3 of 8: 38%
    assert gates(s)["script.on_screen"].status == "PASS"


def test_words_the_voice_says_as_they_show_are_read_along_and_a_declaration_counts_too():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S02").update(on_screen="the ovens wake up")  # 4 of 8, but the voice says them, in order
    got = gates(s)["script.on_screen"]
    assert got.status == "PASS" and "read along with the voice" in got.summary
    assert any(e.startswith("S02: 4 on-screen words against 8 said (50%)") for e in got.evidence)
    shot(s, "S02").update(on_screen="wake up the ovens")  # the same words out of order: not read along
    assert gates(s)["script.on_screen"].status == "FAIL"
    shot(s, "S02")["reading"] = {"voice_matched": True, "reason": "the voice reads the line as it lands"}
    got = gates(s)["script.on_screen"]
    assert got.status == "PASS" and "declared voice-matched" in got.summary


# --- hero lines (at most 6 words, at most 2 an act) ------------------------------------------------------------------------

def test_hero_lines_are_short_and_rare():
    """L-story-033: a hero line (on-screen text set in the hero role) has at most 6 words; an act shows at most 2."""
    s = copy.deepcopy(SCRIPT)
    shot(s, "S02")["hero_lines"] = "Warm bread, every single morning, since then"  # seven words
    got = gates(s)["script.hero_lines"]
    assert got.status == "FAIL" and "has 7 words (at most 6)" in got.evidence[0]
    shot(s, "S02")["hero_lines"] = ["Warm bread", "Every morning"]  # two in act 1
    shot(s, "S03")["hero_lines"] = "Since 1987"  # one in act 2
    got = gates(s)["script.hero_lines"]
    assert got.status == "PASS" and got.summary.startswith("3 hero lines, each at most 6 words, at most 2 an act")
    assert gates()["script.hero_lines"].summary == "no hero lines in this script"


def test_a_third_hero_line_in_one_act_fails():
    s = copy.deepcopy(SCRIPT)
    act = next(a for a in s["acts"] if len([x for x in a["shots"] if x.get("vo")]) >= 2)
    ids = [x["id"] for x in act["shots"]]
    shot(s, ids[0])["hero_lines"] = ["One", "Two"]
    shot(s, ids[1])["hero_lines"] = "Three"
    got = gates(s)["script.hero_lines"]
    assert got.status == "FAIL" and got.evidence[0].endswith("at most 2") and "3 hero lines" in got.evidence[0]


# --- banned words ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("field,text", [("vo", "The cheapest loaf in town."), ("on_screen", "GUARANTEED fresh"),
                                        ("picture", "A world  class oven glows.")])
def test_banned_words_fail_in_vo_on_screen_and_picture(field, text):
    s = copy.deepcopy(SCRIPT)
    shot(s, "S04")[field] = text
    got = gates(s)["script.banned_words"]
    assert got.status == "FAIL" and got.evidence[0].startswith(f"S04 {field}:")


def test_banned_words_match_whole_words_only():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S04")["picture"] = "A guaranteedness sign, cheapestly lettered."
    assert gates(s)["script.banned_words"].status == "PASS"


def test_banned_words_are_unchecked_without_a_brand_pack():
    assert gates(brand={})["script.banned_words"].status == "UNCHECKED"


def test_a_brand_that_forbids_self_commentary_bans_those_captions_on_screen_only():
    """L-brand-010: with truth.self_commentary false, a graphic captioned "Illustration" or "Example" fails on screen
    and in the picture; the voice may still say "for example", and a brand that allows it (the default) passes."""
    s = copy.deepcopy(SCRIPT)
    shot(s, "S04")["on_screen"] = "Illustration"
    shot(s, "S03")["picture"] = "A card captioned Examples in its corner."
    shot(s, "S02")["vo"] = shot(s, "S02")["vo"].rstrip(".") + ", for example."
    strict = copy.deepcopy(BRAND)
    strict.setdefault("truth", {})["self_commentary"] = False
    got = gates(s, brand=strict)["script.banned_words"]
    assert got.status == "FAIL"
    assert sorted(e.split(":")[0] for e in got.evidence) == ["S03 picture", "S04 on_screen"]
    assert all("truth.self_commentary is false" in e for e in got.evidence)
    assert gates(s)["script.banned_words"].status == "PASS"  # the default allows a graphic to caption itself


# --- runtime (±10%) --------------------------------------------------------------------------------------------------------

def test_runtime_fails_outside_ten_percent():
    assert gates(film={"targets": {"runtime_s": 14.0}})["script.runtime"].status == "PASS"  # planned 14.5 s: +3.6%
    assert gates(film={"targets": {"runtime_s": 13.0}})["script.runtime"].status == "FAIL"  # +11.5%
    got = gates(film={"targets": {"runtime_s": 30}})["script.runtime"]
    assert got.status == "FAIL" and "from the 30 s target" in got.summary


def test_runtime_uses_the_formats_range_when_the_film_has_no_target():
    assert gates(film={"targets": {}}, fmt={"runtime_s": [10, 20]})["script.runtime"].status == "PASS"
    assert gates(film={"targets": {}}, fmt={"runtime_s": [30, 60]})["script.runtime"].status == "FAIL"


def test_runtime_without_any_target_is_unchecked_never_passed():
    assert gates(film={"targets": {}})["script.runtime"].status == "UNCHECKED"


# --- numbers ---------------------------------------------------------------------------------------------------------------

def test_an_unsourced_number_fails():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S04")["vo"] = "Join 4,000 happy regulars."
    got = gates(s)["script.numbers"]
    assert got.status == "FAIL" and "'4,000'" in got.evidence[0]


def test_numbers_pass_when_approved_sampled_or_sourced():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S04")["on_screen"] = "Since 1987 · 120 loaves · 6 am"  # truth claim, sample data, approved claim
    assert gates(s)["script.numbers"].status == "PASS"


def test_spelled_out_numbers_count_and_are_matched_by_value():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S04")["vo"] = "Nineteen eighty-seven, and still one hundred and twenty a day."
    assert gates(s)["script.numbers"].status == "PASS"
    shot(s, "S04")["vo"] = "Forty-five percent of the town comes here."
    got = gates(s)["script.numbers"]
    assert got.status == "FAIL" and "forty-five percent" in got.evidence[0].lower()


def test_a_lone_small_number_is_ordinary_speech():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S04")["vo"] = "One loaf, two hands, three minutes."
    assert gates(s)["script.numbers"].status == "PASS"


def test_a_number_can_cite_a_pinned_source_on_the_shot():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S04")["vo"] = "Join 4,000 regulars."
    shot(s, "S04")["claims"] = [{"text": "4,000 regulars a week", "source": "shop-site:visitors"}]
    assert gates(s)["script.numbers"].status == "PASS"
    shot(s, "S04")["claims"] = [{"text": "4,000 regulars a week", "source": "old-flyer"}]  # not pinned
    assert gates(s)["script.numbers"].status == "FAIL"


# --- claims -----------------------------------------------------------------------------------------------------------------

def test_claims_pass_when_approved_or_pinned():
    got = gates()["script.claims"]
    assert got.status == "PASS" and "2 claims" in got.summary


@pytest.mark.parametrize("claim,why", [
    ("Best bread in the county", "isn't on the approved list"),
    ({"text": "Best bread in the county", "source": "old-flyer"}, "has no pin"),
    ({"text": "Best bread", "source": "nowhere:1"}, "isn't a source"),
    ({"text": "Open late", "approved": "late-hours"}, "isn't on the brand's approved list"),
])
def test_claims_fail_without_approval_or_a_pinned_source(claim, why):
    s = copy.deepcopy(SCRIPT)
    shot(s, "S04")["claims"] = [claim]
    got = gates(s)["script.claims"]
    assert got.status == "FAIL" and why in got.evidence[0]


def test_never_claim_list_fails_anywhere_it_appears():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S04")["picture"] = "Organic flour sacks by the door."
    got = gates(s)["script.claims"]
    assert got.status == "FAIL" and "never claims" in got.evidence[0]


def test_a_truth_claim_id_resolves_through_its_pinned_source():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S03")["claims"] = ["since"]
    assert gates(s)["script.claims"].status == "PASS"


# --- quotes (P148: no invented testimonials) ----------------------------------------------------------------------------

def test_a_quote_without_attribution_fails():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S04")["on_screen"] = "“Best sourdough I've ever had.”"
    got = gates(s)["script.quotes"]
    assert got.status == "FAIL" and "reads as someone's words" in got.evidence[0]


def test_a_real_sourced_quote_passes():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S04")["on_screen"] = "“Best sourdough I've ever had.”"
    shot(s, "S04")["quotes"] = [{"text": "Best sourdough I've ever had.", "by": "A. Baker", "role": "regular",
                                 "source": "shop-site:reviews"}]
    assert gates(s)["script.quotes"].status == "PASS"


@pytest.mark.parametrize("quote,why", [
    ({"text": "Best sourdough ever.", "by": "A. Baker", "source": "shop-site", "real": False}, "invented"),
    ({"text": "Best sourdough ever.", "source": "shop-site"}, "no attribution"),
    ({"text": "Best sourdough ever.", "by": "A. Baker", "source": "old-flyer"}, "has no pin"),
])
def test_invented_or_unsourced_quotes_fail(quote, why):
    s = copy.deepcopy(SCRIPT)
    shot(s, "S04")["quotes"] = [quote]
    got = gates(s)["script.quotes"]
    assert got.status == "FAIL" and why in got.evidence[0]


def test_short_quoted_labels_are_not_testimonials():
    s = copy.deepcopy(SCRIPT)
    shot(s, "S04")["on_screen"] = "The sign says “Open”"
    assert gates(s)["script.quotes"].status == "PASS"


# --- a film's own rules -----------------------------------------------------------------------------------------------------

def test_film_rules_dashes_first_line_and_sources():
    s = copy.deepcopy(SCRIPT)
    s["rules"] = {"vo_dashes": "fail", "first_line": "Every morning", "every_shot_sourced": True}
    shot(s, "S04")["vo"] = "Come early — really."
    got = gates(s)
    assert got["script.vo_dashes"].status == "FAIL"
    assert got["script.first_line"].status == "PASS"
    assert got["script.sources"].status == "FAIL"
    for sid in ("S01", "S02", "S03", "S04"):
        shot(s, sid)["sources"] = ["shop-site:about"]
    shot(s, "S04")["vo"] = "Come early."
    s["rules"]["vo_dashes"] = "review"
    got = gates(s)
    assert got["script.vo_dashes"].status == "PASS" and got["script.sources"].status == "PASS"


# --- autonomous films say only approved claims (PLAN §7) ------------------------------------------------------------------

def test_an_autonomous_film_refuses_sourced_but_unapproved_claims_and_numbers():
    supervised = gates()
    assert supervised["script.claims"].status == "PASS" and supervised["script.numbers"].status == "PASS"
    auto = gates(film={**FILM, "mode": "autonomous"})
    assert auto["script.claims"].status == "FAIL"
    assert "an autonomous film says only approved claims" in auto["script.claims"].evidence[0]
    assert auto["script.numbers"].status == "FAIL" and "'1987'" in auto["script.numbers"].evidence[0]
    s = copy.deepcopy(SCRIPT)
    shot(s, "S03").update(vo="A hundred and twenty loaves, every morning.", on_screen="Fresh daily", claims=[])
    auto = gates(s, film={**FILM, "mode": "autonomous"})
    assert auto["script.claims"].status == "PASS" and auto["script.numbers"].status == "PASS"  # approved + sample data


# --- quoted product facts, re-read at the pinned commit (L-story-005) -------------------------------------------------------

def _git(repo, *argv) -> str:
    import subprocess

    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=Slate test", "-c", "user.email=test@example.invalid",
                           "-c", "commit.gpgsign=false", *argv], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def product(tmp_path):
    """A throwaway product checkout (in the test's own temp folder) with two commits: the copy moves two lines down."""
    import shutil

    if not shutil.which("git"):
        pytest.skip("git isn't installed")
    repo = tmp_path / "product"
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q")
    page = ["export function Week() {", "  // the week view", "  return <h1>Plans a week in one view</h1>;", "}"]
    (repo / "src" / "week.tsx").write_text("\n".join(page) + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "first")
    first = _git(repo, "rev-parse", "HEAD")
    (repo / "src" / "week.tsx").write_text("\n".join(page[:2] + ["  const range = 7;", "  // a heading",
                                                                  page[2], page[3]]) + "\n")
    _git(repo, "commit", "-q", "-am", "second")
    second = _git(repo, "rev-parse", "HEAD")
    return repo, first, second


def _truth_with(repo, pin: str, where: str, quote: str = "Plans a week in one view") -> dict:
    t = copy.deepcopy(TRUTH)
    t["sources"].append({"id": "app", "title": "The product's source", "pin": f"commit {pin}", "path": str(repo)})
    t["claims"].append({"id": "week", "text": "One view for the whole week", "source": "app", "where": where,
                        "quote": quote})
    return t


def test_a_quoted_product_fact_is_re_read_at_its_pinned_commit(product):
    repo, first, _second = product
    got = gates(truth=_truth_with(repo, first[:9], "src/week.tsx:3"))["script.quotes"]
    assert got.status == "PASS" and "1 product quote re-read at its pinned commit" in got.summary
    assert "is at src/week.tsx:3" in got.evidence[0]


def test_a_quote_that_moved_is_named_with_the_line_it_is_on_now(product):
    repo, _first, second = product
    got = gates(truth=_truth_with(repo, second, "src/week.tsx:3"))["script.quotes"]
    assert got.status == "FAIL"
    assert got.evidence[0] == ("claim week: 'Plans a week in one view' isn't at src/week.tsx:3 any more; it's at "
                               "src/week.tsx:5 (cite that line)")
    gone = gates(truth=_truth_with(repo, second, "src/week.tsx:5", quote="Plans a month in one view"))["script.quotes"]
    assert gone.status == "FAIL" and "isn't in src/week.tsx at the pinned commit" in gone.evidence[0]
    missing = gates(truth=_truth_with(repo, second, "src/month.tsx:2"))["script.quotes"]
    assert missing.status == "FAIL" and "src/month.tsx isn't in source 'app'" in missing.evidence[0]


def test_a_shots_quoted_source_is_checked_too_and_an_unreadable_source_is_unchecked(product, tmp_path):
    repo, first, _second = product
    s = copy.deepcopy(SCRIPT)
    shot(s, "S04")["sources"] = [{"ref": "app:src/week.tsx:3", "quote": "Plans a week in one view"}]
    truth = copy.deepcopy(TRUTH)
    truth["sources"].append({"id": "app", "pin": f"commit {first}", "path": str(repo)})
    assert gates(s, truth=truth)["script.quotes"].status == "PASS"
    truth["sources"][-1]["path"] = str(tmp_path / "not-here")  # the code lives on another machine
    got = gates(s, truth=truth)["script.quotes"]
    assert got.status == "UNCHECKED" and "couldn't be re-read on this machine" in got.summary
    assert "isn't on this machine" in got.evidence[0]
    truth["sources"][-1]["path"] = str(repo)
    truth["sources"][-1]["pin"] = "commit 0123456789abcdef"  # a commit the checkout doesn't have
    got = gates(s, truth=truth)["script.quotes"]
    assert got.status == "UNCHECKED" and "isn't in" in got.evidence[0] and "fetch it" in got.evidence[0]
