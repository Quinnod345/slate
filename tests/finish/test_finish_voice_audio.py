"""Review cuts before the final mix exists play the voice alone (L-finish-D002), and `slate review --audio <mix>`
reviews a proposed mix against the current picture (the films' review_cuts.sh AUDIO=)."""

from __future__ import annotations

import numpy as np
import pytest

import filmkit
from slate import jsonio
from slate.finish import cuts, media, review
from slate.finish.film import Film


def _takes(d) -> None:
    """The two voice takes the fixture's cue sheet places (a short tone each, so the voice is audible)."""
    for sid, hz in (("S01", 330.0), ("S03", 440.0)):
        t = np.arange(int(0.4 * filmkit.SR)) / filmkit.SR
        filmkit.write_wav24(d / f"voice/takes/{sid}/t01.wav", 0.2 * np.sin(2 * np.pi * hz * t))


def _peak(path) -> float:
    from slate.qa import wav

    x, _info = wav.read(path)
    return float(np.abs(x).max())


# ---------------------------------------------------------------------------------------------------------- voice only
def test_before_the_mix_the_packet_plays_the_voice_alone(make_film):
    d = make_film("vo", mode="draft", brand="open-brand", mix=False)
    _takes(d)
    code, out = filmkit.cli_json(d, "review", "--no-send")
    assert code == 0, out
    rec = jsonio.read(d / "review/v1/packet.json")
    assert rec["source"]["sound"] == "voice only" and rec["source"]["audio"] == cuts.VOICE_ONLY
    assert any("voice alone" in n for n in rec["source"]["notes"])
    assert media.has_audio(d / "review/v1" / rec["files"]["phone_cut"]["file"])
    voice = d / cuts.VOICE_ONLY
    film = Film(d)
    assert abs(media.stream_durations(voice)["audio"] - film.seconds) < 0.01  # the film's length
    assert _peak(voice) > 0.01  # the takes are in it
    placed = jsonio.read(voice.with_suffix(".json"))["placed"]
    assert [p["shot"] for p in placed] == ["S01", "S03"]
    assert "the voice alone" in (d / "review/v1/review.md").read_text()


def test_without_takes_the_packet_is_silent_and_says_why(make_film):
    d = make_film("novo", mode="draft", brand="open-brand", mix=False)  # cue sheet, but no take files
    src = cuts.source_for(Film(d))
    assert src.audio is None and src.sound == "silent"
    assert any("isn't on disk" in n for n in src.notes)


def test_the_voice_track_is_made_again_only_when_a_take_changes(make_film):
    d = make_film("vo2", mode="draft", brand="open-brand", mix=False)
    _takes(d)
    film = Film(d)
    first, _ = cuts.voice_only(film)
    stamp = first.stat().st_mtime_ns
    again, _ = cuts.voice_only(film)
    assert again.stat().st_mtime_ns == stamp  # nothing changed: kept
    t = np.arange(int(0.3 * filmkit.SR)) / filmkit.SR
    filmkit.write_wav24(d / "voice/takes/S01/t01.wav", 0.1 * np.sin(2 * np.pi * 550.0 * t))
    third, _ = cuts.voice_only(film)
    assert jsonio.read(third.with_suffix(".json"))["key"]["takes"]["voice/takes/S01/t01.wav"] == \
        jsonio.sha256(d / "voice/takes/S01/t01.wav")


def test_with_a_mix_the_packet_plays_the_mix(make_film):
    d = make_film("mixed", mode="draft", brand="open-brand")
    src = cuts.source_for(Film(d))
    assert src.sound == "final mix" and src.audio == Film(d).mix()


# ---------------------------------------------------------------------------------------------------------- --audio
def _proposed_mix(d, seconds: float, name: str = "proposal_v2.wav"):
    t = np.arange(int(round(seconds * filmkit.SR))) / filmkit.SR
    path = d / "sound" / "proposals" / name
    filmkit.write_wav24(path, 0.08 * np.sin(2 * np.pi * 523.25 * t))
    return path


def test_a_proposed_mix_is_reviewed_against_the_current_picture(make_film, fin):
    d = make_film("prop", mode="draft", brand="open-brand")
    film = Film(d)
    mix = _proposed_mix(d, film.seconds)
    before = jsonio.sha256(film.mix())
    code, out = filmkit.cli_json(d, "review", "--audio", str(mix))
    assert code == 0, out
    assert out["built"] is True and out["mix"]["file"] == "sound/proposals/proposal_v2.wav"
    rec = jsonio.read(d / out["proposal"])
    folder = (d / out["proposal"]).parent
    phone = folder / rec["files"]["phone_cut"]["file"]
    half = folder / rec["files"]["review_cut"]["file"]
    for cut in (phone, half):
        assert media.frames(cut) == film.total_frames and media.has_audio(cut)
    assert rec["picture"]["sound"] == "proposed mix" and rec["mix"]["sha256"] == jsonio.sha256(mix)
    assert rec["files"]["phone_cut"]["burnin"]  # the timecode and shot id under the picture
    assert "a proposed mix" in (folder / "review.md").read_text()
    # the film's own mix, packet and version are untouched
    assert jsonio.sha256(film.mix()) == before and not (d / "review/v1/packet.json").exists()
    assert Film(d).version() == film.version()
    # Downloads got the cuts and review.md (your own film's default)
    sent = out["sent"]
    assert sent and sent["sent"] and sorted(sent["files"]) == sorted([phone.name, half.name, "review.md"])
    # the same mix again is kept, and isn't sent twice
    code, again = filmkit.cli_json(d, "review", "--audio", str(mix))
    assert code == 0 and again["built"] is False and again["sent"] is None


def test_a_proposed_mix_must_run_the_pictures_length(make_film):
    d = make_film("short", mode="draft", brand="open-brand")
    mix = _proposed_mix(d, Film(d).seconds - 0.5, "too_short.wav")
    code, out = filmkit.cli_json(d, "review", "--audio", str(mix))
    assert code != 0 and "sound runs" in out["message"]


def test_audio_option_refusals_and_dry_run(make_film):
    d = make_film("refuse", mode="draft", brand="open-brand")
    code, out = filmkit.cli_json(d, "review", "--audio", str(d / "nope.wav"))
    assert code == 2 and "There's no mix" in out["message"]
    mix = _proposed_mix(d, Film(d).seconds)
    code, out = filmkit.cli_json(d, "review", "--audio", str(mix), "--to", "imessage")
    assert code == 2 and "Downloads" in out["message"]
    code, out = filmkit.cli_json(d, "review", "--audio", str(mix), "--dry-run")
    assert code == 0 and out["dry_run"] is True and out["folder"].startswith("review/proposals/")
    assert not (d / out["folder"]).exists()  # a dry run makes nothing


def test_with_a_master_the_proposed_mix_goes_over_the_masters_picture(make_film):
    d = make_film("pm", mode="autonomous", format="finish-reel")
    code, _out, err = filmkit.cli(d, "master")
    assert code == 0, err
    film = Film(d)
    src = cuts.with_audio(film, _proposed_mix(d, film.seconds))
    assert src.kind == "master" and src.sound == "proposed mix"
    args, amap, n = src.ffmpeg_inputs(d)
    assert amap == "1:a:0" and n == 2 and str(src.audio) in args
