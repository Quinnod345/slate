"""Re-voicing without starting over: a comp (L-voice-S013), the narration preview (L-voice-S018), the approval swap
(L-voice-S019) and each read stitched to its neighbours' request ids (L-voice-042). Synthetic takes only."""

from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pytest

from slate import jsonio
from slate.cli import run_cli
from slate.errors import Refused, UsageError
from slate.voice import audio as audio_mod
from slate.voice import comp, reel, swap, tts
from slate.voice.film import VoiceFilm

from voice_helpers import real_take, whisper_doc, write_speech

LINE = "Slate hears each word, then fits it to the cut."


# --- the comp ---------------------------------------------------------------------------------------------------------


def _two_takes(voice_film, set_transcript, *, donor_gain=0.5):
    vf = VoiceFilm(voice_film)
    base, wa = real_take(vf, "S02", LINE, seed="base", take="t01")
    donor, wb = real_take(vf, "S02", LINE, seed="donor", take="t02")
    x, sr = audio_mod.load(donor)
    audio_mod.write_wav(donor, x * donor_gain, sr, 24)  # the donor read sits 6 dB quieter
    vf.update_take("S02", "t02", raw=vf.rel(donor))
    vf.save_take_json("S02", "t02", {**vf.take_json("S02", "t02"), "sha256": jsonio.sha256(donor)})
    set_transcript(base, whisper_doc(wa))
    set_transcript(donor, whisper_doc(wb))
    vf.select_take("S02", "t01")
    return vf, base, donor, wa, wb


def test_a_comp_keeps_the_take_to_the_word_and_lands_the_donors_word_where_the_original_was(voice_film, fake_whisper):
    set_transcript, _calls = fake_whisper
    vf, base, donor, wa, wb = _two_takes(voice_film, set_transcript)
    res = comp.comp(voice_film, "S02", word=5, donor="t02")  # from "then" on
    assert res["take"] == "t03" and res["word_text"] == "then" and res["stage"] == "raw"
    assert res["donor_gain_db"] == pytest.approx(6.02, abs=0.3)  # gained to match the base's loud part
    y, sr = audio_mod.load(voice_film / res["file"])
    xa, _ = audio_mod.load(base)
    xb, _ = audio_mod.load(donor)
    cut = int(round(res["base_cut_s"] * sr))
    assert np.allclose(y[: cut - int(0.02 * sr)], xa[: cut - int(0.02 * sr)], atol=1e-6)  # the base, untouched
    g = 10 ** (res["donor_gain_db"] / 20)
    after = int(round(res["donor_cut_s"] * sr))
    k = int(0.02 * sr)
    assert np.allclose(y[cut + k:cut + k + sr // 2], xb[after + k:after + k + sr // 2] * g, atol=1e-5)  # the donor
    # the donor's word lands exactly where the original's did: their onsets, measured the same way, match
    ea = wa[3]["end"]
    assert comp.true_onset(y, sr, ea) == pytest.approx(res["base_onset_s"], abs=0.011)
    t = vf.take_json("S02", "t03")
    assert t["stages"]["comp"]["base"]["sha256"] == jsonio.sha256(base)
    assert t["stages"]["comp"]["donor"]["sha256"] == jsonio.sha256(donor) and t["character_cost"] == 0
    assert jsonio.validate(t, "voice_take") == []
    assert VoiceFilm(voice_film).selected("S02")[0] == "t03" and not VoiceFilm(voice_film).approved("S02")


def test_a_comp_needs_a_word_after_the_first_and_the_persons_go_on_approved_work(voice_film, fake_whisper):
    set_transcript, _calls = fake_whisper
    _two_takes(voice_film, set_transcript)
    with pytest.raises(UsageError, match="2 to 10"):
        comp.comp(voice_film, "S02", word=1, donor="t02")
    assert run_cli(["voice", "approve", "S02", "--by", "A. Person", "--film", str(voice_film)])[0] == 0
    with pytest.raises(Refused, match="approved"):
        comp.comp(voice_film, "S02", word=5, donor="t02")
    code, out, err = run_cli(["voice", "comp", "S02", "--word", "5", "--from", "t02", "--force", "--film",
                              str(voice_film), "--json"])
    assert code == 0, err
    assert json.loads(out)["take"] == "t03"


# --- the narration preview --------------------------------------------------------------------------------------------


def _cued(voice_film):
    vf = VoiceFilm(voice_film)
    cues = []
    for shot in ("S01", "S02", "S04"):
        wav, _w = real_take(vf, shot, vf.line(shot).text, seed=shot)
        cues.append({"shot": shot, "at": 0.3, "file": vf.rel(wav), "gain": -20, "fade_in": 0.01, "fade_out": 0.05})
    jsonio.write_atomic(voice_film / "timing" / "vo_cues.json", {"cues": cues})
    return vf


def test_the_preview_lays_a_re_voiced_line_on_the_timeline_and_moves_what_follows(voice_film):
    vf = _cued(voice_film)
    longer, _w = real_take(vf, "S02", LINE + " Every word of it.", seed="longer", take="t02")  # the new read runs longer
    change = audio_mod.duration(longer) - audio_mod.duration(voice_film / "voice/takes/S02/t01.wav")
    assert change > 0.5
    res = reel.preview(voice_film)
    rows = {r["shot"]: r for r in res["shots"]}
    assert rows["S02"]["take_change_s"] == pytest.approx(change, abs=1e-3)
    assert rows["S02"]["length_proposed_s"] == pytest.approx(rows["S02"]["length_current_s"] + change, abs=1e-3)
    assert rows["S04"]["start_proposed_s"] == pytest.approx(rows["S04"]["start_current_s"] + change, abs=1e-3)
    assert res["change_s"] == pytest.approx(change, abs=1e-3)
    cur, sr = audio_mod.load(voice_film / res["files"]["current"])
    new, _ = audio_mod.load(voice_film / res["files"]["proposed"])
    assert len(new) - len(cur) == pytest.approx(change * sr, abs=2)

    def onset(x, after):
        i = int(after * sr)
        return (i + int(np.argmax(np.abs(x[i:]) > 0.01))) / sr

    s4_cur = onset(cur, rows["S04"]["start_current_s"] + 0.25)
    s4_new = onset(new, rows["S04"]["start_proposed_s"] + 0.25)
    assert s4_new - s4_cur == pytest.approx(change, abs=0.005)  # the next line moved with the longer read
    for shot in ("S01", "S02", "S04"):  # every line at the same loudness, as heard side by side
        assert rows[shot]["take_current"]
    assert not (voice_film / "timing" / "vo_cues.json").read_text().count("t02")  # nothing re-timed
    code, out, _ = run_cli(["voice", "preview", "--takes", "S02=t01", "--name", "same", "--film", str(voice_film),
                            "--json"])
    assert code == 0 and json.loads(out)["change_s"] == 0.0


# --- the approval swap ------------------------------------------------------------------------------------------------


def _set(voice_film, shots=("S01", "S02")):
    vf = VoiceFilm(voice_film)
    for shot in shots:
        real_take(vf, shot, vf.line(shot).text, seed=f"old-{shot}")
    takes, words = {}, {}
    for shot in shots:
        f = voice_film / "voice" / "heard" / f"{shot}.wav"
        f.parent.mkdir(parents=True, exist_ok=True)
        words[shot] = write_speech(f, vf.line(shot).text, seed=f"new-{shot}")
        takes[shot] = {"file": f"voice/heard/{shot}.wav", "sha256": jsonio.sha256(f), "stage": "paced"}
    path = jsonio.write_atomic(voice_film / "voice" / "heard" / "set.json", {"name": "round-2", "takes": takes})
    return vf, path, words


def test_the_swap_prints_its_plan_and_does_it_only_with_go(voice_film, fake_whisper):
    set_transcript, _calls = fake_whisper
    vf, path, words = _set(voice_film)
    code, out, err = run_cli(["voice", "swap", str(path), "--film", str(voice_film), "--json"])
    plan = json.loads(out)
    assert code == 0 and [r["replaces"] for r in plan["takes"]] == ["t01", "t01"]
    assert all(r["heard_as_is"] for r in plan["takes"]) and plan["steps"][0] == "the files the owner heard"
    assert VoiceFilm(voice_film).selected("S01")[0] == "t01"  # the plan wrote nothing
    for shot in ("S01", "S02"):
        set_transcript(voice_film / "voice" / "takes" / shot / "t02.paced.wav", whisper_doc(words[shot]))
    ran = []
    res = swap.go(voice_film, path, by="A. Person", reason="the round the owner liked",
                  runner=lambda argv: (ran.append(argv) or (0, "{}", "")))
    vf = VoiceFilm(voice_film)
    for shot in ("S01", "S02"):
        take, info = vf.selected(shot)
        assert take == "t02" and vf.approved(shot)["by"] == "A. Person"
        assert jsonio.sha256(voice_film / info["paced"]) == jsonio.sha256(voice_film / f"voice/heard/{shot}.wav")
        assert vf.locked(shot)
    backup = voice_film / res["backup"]
    assert (backup / "takes.json").is_file() and (backup / "voice/takes/S01/t01.wav").is_file()
    assert [a[:3] for a in ran] == [["script", "build", "--from-takes"], ["timing", "--film", str(voice_film)],
                                    ["voice", "captions", "--film"]]
    log = [json.loads(line) for line in (voice_film / res["log"]).read_text().splitlines()]
    assert [entry["status"] for entry in log] == ["done"] * 6
    assert (voice_film / "voice" / "swaps" / "round-2.approval.json").is_file()


def test_the_swap_stops_at_the_first_failing_step_and_names_it(voice_film, fake_whisper):
    set_transcript, _calls = fake_whisper
    vf, path, words = _set(voice_film)
    for shot in ("S01", "S02"):
        set_transcript(voice_film / "voice" / "takes" / shot / "t02.paced.wav", whisper_doc(words[shot]))

    def runner(argv):
        return (1, "", "the frames aren't exact") if argv[0] == "timing" else (0, "{}", "")

    with pytest.raises(Refused) as exc:
        swap.go(voice_film, path, by="A. Person", runner=runner)
    assert "step 6, the re-timing" in exc.value.message and "frames aren't exact" in exc.value.message
    log = [json.loads(line) for line in (voice_film / "voice/swaps/round-2.log.jsonl").read_text().splitlines()]
    assert log[-1]["step"] == "the re-timing" and log[-1]["status"] == "FAILED"


def test_a_file_that_changed_since_it_was_heard_stops_the_swap_before_anything_moves(voice_film):
    vf, path, _words = _set(voice_film)
    f = voice_film / "voice" / "heard" / "S02.wav"
    x, sr = audio_mod.load(f)
    audio_mod.write_wav(f, x * 0.9, sr, 24)
    with pytest.raises(Refused, match="step 1, the files the owner heard: S02"):
        swap.go(voice_film, path, by="A. Person", runner=lambda argv: (0, "", ""))
    assert VoiceFilm(voice_film).selected("S02")[0] == "t01" and not (voice_film / "voice" / "backups").exists()
    with pytest.raises(UsageError, match="--by"):
        swap.go(voice_film, path, by="")


# --- stitching to the neighbours' request ids ----------------------------------------------------------------------


def _live_take(vf, shot, request_id, minutes_ago, **extra):
    wav, _w = real_take(vf, shot, vf.line(shot).text, seed=shot)
    made = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    vf.save_take_json(shot, "t01", {**vf.take_json(shot, "t01"), "request_id": request_id, "created_at": made,
                                    **extra})


def test_a_read_is_stitched_to_its_neighbours_recent_live_takes(voice_film):
    vf = VoiceFilm(voice_film)
    _live_take(vf, "S01", "req_before", 30)
    _live_take(vf, "S04", "req_after", 45)
    assert tts.neighbour_request_ids(vf, "S02") == (["req_before"], ["req_after"])  # S03 has no line
    _live_take(vf, "S01", "req_old", 3 * 60)  # over two hours old: the API won't stitch to it
    _live_take(vf, "S04", "req_replayed", 5, replay=True)  # a replayed stand-in never reaches the API
    assert tts.neighbour_request_ids(vf, "S02") == ([], [])
    _live_take(vf, "S01", "req_comp", 5, stages={"comp": {}})  # a comp isn't a read of its own
    assert tts.neighbour_request_ids(vf, "S02")[0] == []


def test_request_ids_go_out_once_the_client_sends_them(voice_film):
    class Sends:
        def tts(self, *, voice_id, text, previous_request_ids=None, next_request_ids=None, **kw):
            return {}

    class Doesnt:
        def tts(self, *, voice_id, text, previous_text=None, next_text=None, **kw):
            return {}

    assert tts._sends_request_ids(Sends()) and not tts._sends_request_ids(Doesnt())  # noqa: SLF001
    code, out, _ = run_cli(["voice", "tts", "--film", str(voice_film), "--json"])
    assert code == 0
    stitch = jsonio.read(voice_film / "voice" / "takes" / "S02" / "t01.json")["stitch"]
    assert stitch == {"previous_request_ids": [], "next_request_ids": [], "sent": False}  # replayed neighbours: none


# --- trimming a leaked tail (NEEDS: "a trim stage for leaked tails") ----------------------------------------------------


def _leaky(voice_film, fake_whisper, gap_s):
    """Every line voiced for real; S01's take runs on into two leaked words after a pause of gap_s."""
    from slate.voice import align

    from voice_helpers import speech

    set_transcript, _calls = fake_whisper
    vf = VoiceFilm(voice_film)
    texts = {"S01": "Every film starts with one line.", "S02": LINE, "S04": "Warm voices, never whispered."}
    leak, _w = speech(["leaked", "words"], seed="other")
    for shot, text in texts.items():
        extra = np.concatenate([np.zeros(int(gap_s * 48000)), leak]) if shot == "S01" else None
        wav, words = real_take(vf, shot, text, seed=shot, extra=extra)
        set_transcript(wav, whisper_doc(words))
    align.align(voice_film)
    return VoiceFilm(voice_film)


def test_a_leaked_tail_is_trimmed_off_the_play_length_and_the_take_is_untouched(voice_film, fake_whisper):
    from slate.voice import gates, trim

    vf = _leaky(voice_film, fake_whisper, gap_s=0.45)
    take_file = voice_film / vf.selected("S01")[1]["raw"]
    before = jsonio.sha256(take_file)
    assert gates.run(voice_film, ["voice_tails"])[0].status == "FAIL"
    planned = {r["shot"]: r for r in trim.plan(voice_film)}
    assert planned["S01"]["ok"] and "nothing to trim" in planned["S02"]["why"]
    assert vf.selected("S01")[1].get("dur") is None  # a plan records nothing
    cut = planned["S01"]["cut_s"]
    assert planned["S01"]["audible_end_s"] < cut < planned["S01"]["tail"]["start"]
    code, out, err = run_cli(["voice", "trim", "--go", "--film", str(voice_film), "--json"])
    assert code == 0, err
    vf = VoiceFilm(voice_film)
    assert vf.selected("S01")[1]["dur"] == cut and jsonio.sha256(take_file) == before  # a play length, not an edit
    assert vf.take_json("S01", "t01")["stages"]["trim"]["tail"]["start"] == planned["S01"]["tail"]["start"]
    assert gates.run(voice_film, ["voice_tails"])[0].status == "PASS"  # the take plays only to its cut
    from slate.timing.takes import find_takes

    assert find_takes(voice_film)["S01"].read_seconds == cut  # timing reads the play length (the cue sheet's dur)


def test_a_tail_with_no_pause_before_it_is_left_for_a_re_voice_and_approved_takes_need_the_persons_go(voice_film,
                                                                                                      fake_whisper):
    from slate.voice import trim

    _leaky(voice_film, fake_whisper, gap_s=0.45)
    assert run_cli(["voice", "approve", "S01", "--by", "A. Person", "--film", str(voice_film)])[0] == 0
    with pytest.raises(Refused, match="approved"):
        trim.apply(voice_film, only="S01")
    rows = trim.apply(voice_film, only="S01", force=True, by="A. Person", reason="the owner heard the cut")
    assert rows[0]["trimmed"] and VoiceFilm(voice_film).take_json("S01", "t01")["stages"]["trim"]["by"] == "A. Person"
    code, _out, err = run_cli(["voice", "trim", "--force", "--film", str(voice_film)])
    assert code == 2 and "--go" in err


def test_a_tail_that_runs_straight_on_from_the_line_isnt_trimmed(voice_film, fake_whisper, monkeypatch):
    from slate.voice import gates, trim

    _leaky(voice_film, fake_whisper, gap_s=0.45)
    # the tails gate places the tail; here it starts just after the line's last word, with no pause to cut in
    monkeypatch.setattr(gates, "trailing_voice", lambda audio, last: [{"start": round(last + 0.06, 3),
                                                                       "end": round(last + 0.6, 3), "voiced": 0.9}])
    row = next(r for r in trim.plan(voice_film, only="S01"))
    assert not row["ok"] and "re-voice" in row["why"]
