"""Direction preserves exact script words and applies each voice's explicit delivery restrictions before spending."""

import pytest

from slate import ledger
from slate.cli import run_cli
from slate.errors import Refused
from slate.voice import tags

LINE = "Find out where you stand. Then let's talk."


@pytest.mark.parametrize("tagged, plain", [
    ("[warmly, with a smile] Hi, I'm here.", "Hi, I'm here."),
    ("Find out where you stand. [short pause] [warmly] Then let's talk.", LINE),
    ("Hello [laughs], friend.", "Hello, friend."),
    ("This is hard. [sighs]", "This is hard."),
    ("  [bright]   Spaces   collapse. ", "Spaces collapse."),
    ("No tags at all.", "No tags at all."),
])
def test_strip_back_to_the_script(tagged, plain):
    assert tags.strip(tagged) == plain
    assert tags.check(tagged, plain) == []


@pytest.mark.parametrize("tag", [
    "[whispers]", "[whispering]", "[Whispered in a British accent]", "[softly]", "[Softening, reflective]",
    "[continues softly]", "[quietly hopeful]", "[quiet]", "[gently, with care]", "[gently]", "[hushed]",
    "[murmuring]", "[under her breath]", "[breathy]", "[intimate]", "[Warm, intimate narration]",
    "[calm, reassuring]", "[low voice]", "[low, gravelly voice]", "[lower voice]",
])
def test_explicit_full_voice_policy_refuses_low_effort_variants(tag):
    tagged = f"{tag} {LINE}"
    problems = tags.check(tagged, LINE, policy={"avoid_low_effort": True})
    assert any("banned tag" in p for p in problems), problems
    with pytest.raises(Refused) as exc:
        tags.refuse_banned(tagged, avoid_low_effort=True)
    assert exc.value.exit_code == 1


@pytest.mark.parametrize("tag", ["[warmly]", "[with a smile in her voice]", "[bright]", "[confident]",
                                 "[reassuring]", "[sincere]", "[matter-of-fact]", "[short pause]", "[laughs]"])
def test_full_voice_tags_are_allowed(tag):
    assert tags.check(f"{tag} {LINE}", LINE) == []


def test_changed_words_are_named():
    problems = tags.check("[warmly] Find out where you are. Then let's talk.", LINE)
    assert any("doesn't strip back" in p and "'stand.' became 'are.'" in p for p in problems)
    problems = tags.check("[warmly] Find out where you stand.", LINE)
    assert any("is missing" in p for p in problems)


def test_other_direction_problems():
    assert any("unbalanced" in p for p in tags.check("[warmly Find out where you stand. Then let's talk.", LINE))
    assert any("empty tag" in p for p in tags.check("[] " + LINE, LINE))
    assert any("SSML" in p for p in tags.check('Find out where you stand. <break time="1s"/> Then let\'s talk.', LINE))
    assert any("script line itself" in p for p in tags.check(LINE, "[warmly] " + LINE))


def test_brand_policy_enforces_its_explicit_bans_and_vocabulary():
    policy = {"banned_extra": ["sarcastic"], "allowed": ["warmly", "bright"], "max_tags_per_line": 1}
    assert any("banned tag" in p for p in tags.check("[sarcastic] " + LINE, LINE, policy=policy))
    assert any("vocabulary" in p for p in tags.check("[confident] " + LINE, LINE, policy=policy))
    assert any("allows 1" in p for p in tags.check("[warmly] Find out where you stand. [bright] Then let's talk.",
                                                   LINE, policy=policy))
    # an allow-list naming a banned tag still can't let it through
    policy = {"allowed": ["whispering"], "avoid_low_effort": True}
    assert any("banned tag" in p for p in tags.check("[whispering] " + LINE, LINE, policy=policy))


def test_scope_word_and_spoken_tags():
    assert tags.scope_word(LINE) is None
    assert tags.scope_word("[warmly] " + LINE) == 0
    assert tags.scope_word("Find out where you stand. [short pause] [warmly] Then let's talk.") == 5
    assert tags.scope_word("Find out where you stand. [short pause] Then let's talk.") is None
    assert tags.spoken_tag_words("[warmly] " + LINE, LINE, ["warmly", "find", "out"]) == ["warmly"]


def test_cli_refuses_a_banned_tag_before_any_request(voice_film, full_voice_policy):
    code, out, err = run_cli(["voice", "tags", "set", "S04", "[whispering] Warm voices, never whispered.",
                              "--film", str(voice_film)])
    assert code == 1 and "banned tag" in err
    code, _, _ = run_cli(["voice", "tags", "set", "S04", "[warmly] Warm voices, never whispered.",
                          "--film", str(voice_film)])
    assert code == 0
    # a hand edit that slips a banned tag into the direction is caught before the first request
    import json
    path = voice_film / "voice" / "direction.json"
    data = json.loads(path.read_text())
    data["lines"]["S01"] = "[gently] Every film starts with one line."
    path.write_text(json.dumps(data))
    code, out, err = run_cli(["voice", "tts", "--film", str(voice_film)])
    assert code == 1 and "banned tag [gently]" in err
    assert ledger.rows() == []  # nothing was sent, nothing recorded


def test_cli_strip_and_check(voice_film):
    code, out, _ = run_cli(["voice", "tags", "strip", "[bright] Hello there."])
    assert code == 0 and out.strip() == "Hello there."
    code, out, _ = run_cli(["voice", "tags", "check", "--text", "[bright] Hello.", "--line", "Hello."])
    assert code == 0
    code, out, _ = run_cli(["voice", "tags", "check", "--text", "[bright] Hello!", "--line", "Hello."])
    assert code == 1
    code, out, _ = run_cli(["voice", "tags", "check", "--film", str(voice_film), "--json"])
    assert code == 0 and '"ok": true' in out
    code, out, _ = run_cli(["voice", "tags", "rules"])
    assert code == 0 and "whisper" in out


@pytest.mark.parametrize("tag", ["[softly]", "[whispering]", "[calm, reassuring]", "[gently]"])
def test_quiet_delivery_is_available_without_a_scoped_restriction(tag):
    assert tags.check(f"{tag} {LINE}", LINE) == []
    assert tags.banned_in(f"{tag} {LINE}") == []
    assert tags.check(f"{tag} Different words.", LINE)


def test_cli_text_check_uses_the_selected_films_policy(voice_film, full_voice_policy):
    code, out, _ = run_cli(["voice", "tags", "check", "--text", f"[softly] {LINE}", "--line", LINE,
                            "--film", str(voice_film), "--json"])
    assert code == 1 and "banned tag" in out
    code, _, _ = run_cli(["voice", "tags", "check", "--text", f"[softly] {LINE}", "--line", LINE])
    assert code == 0
