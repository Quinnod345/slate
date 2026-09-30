"""Voicing: one take per line with neighbour context, pinned at first use, fitted to its shot, approved takes locked."""

import json

import pytest

from slate import jsonio, ledger
from slate.cli import run_cli
from slate.errors import Refused, SpendRefused
from slate.voice import tts
from slate.voice.film import VoiceFilm, get_pin


def _run(*args):
    """(exit code, JSON result, every message: stderr plus a JSON error's message and hint)."""
    code, out, err = run_cli(["voice", *args, "--json"])
    data = json.loads(out) if out.strip() else None
    if code and isinstance(data, dict):
        err += f"\n{data.get('message', '')}\n{data.get('hint', '')}"
    return code, data, err


def test_one_take_per_line_with_plain_neighbour_context(voice_film):
    VoiceFilm(voice_film).set_direction("S04", "[warmly, with a smile] Warm voices, never whispered.")
    code, res, err = _run("tts", "--film", str(voice_film))
    assert code == 0, err
    assert [t["shot"] for t in res["takes"]] == ["S01", "S02", "S04"]  # S03 has no line
    take = jsonio.read(voice_film / "voice" / "takes" / "S04" / "t01.json")
    assert jsonio.validate(take, "voice_take") == []
    assert take["sent"] == "[warmly, with a smile] Warm voices, never whispered."
    assert take["text"] == "Warm voices, never whispered."
    assert take["previous_text"] == "Slate hears each word, then fits it to the cut."  # plain words, no tags
    assert take["next_text"] is None
    s02 = jsonio.read(voice_film / "voice" / "takes" / "S02" / "t01.json")
    assert s02["next_text"] == "Warm voices, never whispered."  # the neighbour's plain words, not its tags
    for key in ("model", "voice_id", "voice_settings", "seed", "request_id", "format", "sha256"):
        assert take[key] is not None
    assert take["model"] == "eleven_v4" and take["voice_id"] == "demo-narrator-01" and take["seed"] == 7
    assert take["format"] == "pcm_48000" and take["lossless"] and take["replay"]
    assert (voice_film / "voice" / "takes" / "S04" / "t01.wav").is_file()
    manifest = jsonio.read(voice_film / "voice" / "takes.json")
    assert manifest["shots"]["S04"]["selected"] == "t01"
    rows = ledger.rows(op="tts")
    assert len(rows) == 3 and all(r["request_id"].startswith("replay_") for r in rows)
    assert {r["film"] for r in rows} == {"demo-reel"}


def test_pins_at_first_use_and_keeps_them(voice_film, brand):
    code, res, _ = _run("tts", "--film", str(voice_film), "--only", "S01")
    assert code == 0
    assert get_pin(voice_film, "tts") == "eleven_v4"
    assert get_pin(voice_film, "voice_id") == "demo-narrator-01"
    assert get_pin(voice_film, "voice_settings") == {"stability": 0.5, "similarity_boost": 0.75}
    assert get_pin(voice_film, "seed.S01") == 7
    # the brand moves on; the pinned film doesn't
    brand["voice"]["voice_id"] = "a-newer-voice"
    brand["voice"]["settings"] = {"stability": 0.3, "similarity_boost": 0.9}
    code, res, err = _run("tts", "--film", str(voice_film), "--only", "S02")
    assert code == 0 and res["voice_id"] == "demo-narrator-01" and "pinned" in err
    take = jsonio.read(voice_film / "voice" / "takes" / "S02" / "t01.json")
    assert take["voice_id"] == "demo-narrator-01" and take["voice_settings"]["stability"] == 0.5
    # re-pinning is explicit: it needs who approved it and why
    code, _, err = _run("tts", "--film", str(voice_film), "--only", "S04", "--repin", "--approved-by", "A Person")
    assert code == 1 and "reason" in err
    code, res, err = _run("tts", "--film", str(voice_film), "--only", "S04", "--repin", "--approved-by", "A Person",
                          "--reason", "won the A/B")
    assert code == 0 and res["voice_id"] == "a-newer-voice"
    assert get_pin(voice_film, "voice_id") == "a-newer-voice"
    log = jsonio.read(voice_film / "film.json")["pin_log"]
    assert any(e["key"] == "voice_id" and e["event"] == "repin" and e["approved_by"] == "A Person" for e in log)


def test_existing_takes_are_kept_and_force_adds_a_new_take(voice_film):
    _run("tts", "--film", str(voice_film))
    code, res, _ = _run("tts", "--film", str(voice_film))
    assert code == 0 and res["takes"] == [] and len(res["skipped"]) == 3
    code, res, _ = _run("tts", "--film", str(voice_film), "--only", "S01", "--force", "--seed", "99")
    assert code == 0 and res["takes"][0]["take"] == "t02"
    vf = VoiceFilm(voice_film)
    assert vf.take_ids("S01") == ["t01", "t02"] and vf.selected("S01")[0] == "t02"
    assert vf.take_json("S01", "t02")["seed"] == 99
    assert get_pin(voice_film, "seed.S01") == 7  # the first seed stays pinned
    assert (voice_film / "voice" / "takes" / "S01" / "t01.wav").is_file()  # never overwritten


def test_approved_takes_are_locked_and_need_a_spend_approval(voice_film):
    _run("tts", "--film", str(voice_film))
    code, res, err = _run("approve", "S01", "--by", "A Person", "--film", str(voice_film))
    assert code == 0, err
    vf = VoiceFilm(voice_film)
    assert vf.locked("S01")
    code, res, _ = _run("tts", "--film", str(voice_film), "--only", "S01")
    assert code == 0 and res["skipped"][0]["why"] == "approved and locked"
    code, _, err = _run("tts", "--film", str(voice_film), "--only", "S01", "--force")
    assert code == 4 and "spend approval" in err
    code, _, err = _run("tts", "--film", str(voice_film), "--only", "S01", "--force", "--approval", "ap_madeup")
    assert code == 4
    assert vf.take_ids("S01") == ["t01"]  # nothing was voiced
    try:
        from slate.state import approvals
    except ModuleNotFoundError:
        pytest.skip("slate.state isn't installed")
    spend = approvals.record(voice_film, what="spend", path="voice/takes/S01", approved_by="A Person")
    code, res, err = _run("tts", "--film", str(voice_film), "--only", "S01", "--force", "--approval", spend["id"])
    assert code == 0, err
    assert VoiceFilm(voice_film).selected("S01")[0] == "t02" and not VoiceFilm(voice_film).approved("S01")


def test_each_read_is_fitted_to_its_shot(voice_film):
    vf = VoiceFilm(voice_film)
    fit = tts.fit_of(vf, "S01", 2.0)
    assert fit["shot_s"] == 4.0 and fit["lead_s"] == 0.4 and fit["fits"] is True
    fit = tts.fit_of(vf, "S01", 3.5)  # 0.4 + 3.5 = 3.9: only 0.1 s before the cut
    assert fit["fits"] is False and fit["slack_s"] == pytest.approx(0.1)
    # the cue sheet's lead wins when the timing stage has written one
    jsonio.write_atomic(voice_film / "timing" / "vo_cues.json", {"cues": [{"shot": "S01", "at": 0.1}]})
    assert tts.fit_of(VoiceFilm(voice_film), "S01", 3.5)["fits"] is True


def test_estimate_voices_nothing(voice_film):
    code, res, _ = _run("tts", "--film", str(voice_film), "--estimate")
    assert code == 0 and res["dry_run"] and res["characters"] > 0 and res["estimate"]["unit"] == "credits"
    assert ledger.rows() == [] and not (voice_film / "voice" / "takes").exists()


def test_recording_profiles_are_not_synthesized(voice_film, brand):
    brand["voice"] = {"source": "recording", "speaker": "a teammate"}
    code, _, err = _run("tts", "--film", str(voice_film))
    assert code == 1 and "real recordings" in err


def test_live_vendor_category_is_checked(voice_film, monkeypatch):
    from slate.vendors import elevenlabs

    monkeypatch.setattr(elevenlabs.Client, "replay", property(lambda self: False))
    monkeypatch.setattr(elevenlabs.Client, "voice_info", lambda self, vid: {"voice_id": vid, "category": "cloned"})
    monkeypatch.setattr(elevenlabs.Client, "choose_format", lambda self, *a, **k: {"format": "pcm_48000"})
    with pytest.raises(Refused) as exc:
        tts.voice(voice_film)
    assert "cloned voice" in exc.value.message
    assert ledger.rows() == []


def test_spend_refusal_is_exit_four(voice_film, monkeypatch):
    def refuse(*a, **k):
        raise SpendRefused("over this film's budget")

    monkeypatch.setattr(ledger, "check_budget", refuse)
    code, _, err = _run("tts", "--film", str(voice_film))
    assert code == 4 and "budget" in err
