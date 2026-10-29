"""slate review: the packet's contents (a short synthetic clip made with ffmpeg), the burned-in timecode and shot id,
the 0.5 s contact sheet, what changed between versions, the open questions, and where it goes."""

from __future__ import annotations

import numpy as np
import pytest

import filmkit
from slate import gates, jsonio
from slate.finish import burnin, media, review
from slate.finish.film import Film


def _bar(film: Film, phone_path, frame: int) -> np.ndarray:
    from slate.qa import media as qa_media

    info = qa_media.video_info(phone_path)
    img = qa_media.read_frames(phone_path, [frame], pix_fmt="gray")[frame]
    pw, ph = media.fit_short_side(*film.size, 360)
    return img[ph:info["height"], :].astype(np.float64)


def _expected(film: Film, frame: int, width: int, bar: int) -> np.ndarray:
    shot = film.shot_at(frame)
    img = burnin.draw_strip(burnin.label(film, frame, shot["id"]), width, bar, right="v1")
    return np.asarray(img, dtype=np.float64)


def test_packet_contents(make_film):
    d = make_film("pk", mode="draft", brand="open-brand")
    code, out = filmkit.cli_json(d, "review")
    assert code == 0, out
    assert out["version"] == 1 and out["built"] is True
    rec = jsonio.read(d / "review/v1/packet.json")
    assert jsonio.validate(rec, "review") == []
    assert rec["publishing"] == "not approved for publishing"
    assert rec["source"]["kind"] == "renders"  # no master yet: straight from the act renders and the mix
    phone = d / "review/v1" / rec["files"]["phone_cut"]["file"]
    assert jsonio.sha256(phone) == rec["files"]["phone_cut"]["sha256"]
    info = media.video_info(phone)
    film = Film(d)
    assert info["frames"] == film.total_frames
    assert (info["fps_num"], info["fps_den"]) == (film.clock.fps_num, film.clock.fps_den)
    assert media.has_audio(phone)
    assert rec["files"]["phone_cut"]["burnin"]["method"] == "pillow"  # this ffmpeg has no drawtext
    assert "25/1" in rec["files"]["phone_cut"]["burnin"]["timecode"]
    # the contact sheet: a frame every 0.5 s on the film's clock, each tile labelled with its time and shot
    idx = jsonio.read(d / "review/v1" / rec["files"]["sheet_index"])
    assert [t["frame"] for t in idx["tiles"]] == [0, 12, 25, 38]
    assert [t["shot"] for t in idx["tiles"]] == ["S01", "S02", "S03", "S03"]
    assert [t["timecode"] for t in idx["tiles"]] == ["00:00:00:00", "00:00:00:12", "00:00:01:00", "00:00:01:13"]
    sheet = d / "review/v1" / rec["files"]["contact_sheets"][0]["file"]
    assert sheet.is_file() and jsonio.sha256(sheet) == rec["files"]["contact_sheets"][0]["sha256"]
    md = (d / "review/v1/review.md").read_text()
    assert md.startswith(f"# {film.title}, v1: review") and "What changed" in md and "slate notes add" in md


def test_burned_in_label_is_the_films_timecode_and_shot(make_film):
    d = make_film("burn", mode="draft", brand="open-brand")
    filmkit.cli(d, "review", "--no-send")
    film = Film(d)
    phone = next((d / "review/v1").glob("*_phone.mp4"))
    pw, ph = media.fit_short_side(*film.size, 360)
    bar = burnin.bar_height(ph, pw)
    for frame in (5, 18, 33):
        got = _bar(film, phone, frame)
        right = np.abs(got - _expected(film, frame, pw, bar)).mean()
        wrong = np.abs(got - _expected(film, (frame + 9) % film.total_frames, pw, bar)).mean()
        assert right < wrong, (frame, right, wrong)
        assert got.max() > 128  # there is white text in the bar
    assert burnin.label(film, 30, "S03") == "00:00:01:05  S03"  # 25 fps: frame 30 is 1 s and 5 frames


def test_drawtext_chain_uses_the_films_rate_and_each_shot(make_film):
    d = make_film("dt", mode="draft", brand="open-brand", renders=False, mix=False)
    chain = burnin.drawtext_chain(Film(d), width=160, y0=90, bar=16, right="v2")
    assert "timecode='00\\:00\\:00\\:00':rate=25/1" in chain
    assert chain.count("enable='between(n") == 3
    assert "between(n\\,0\\,11)" in chain and "between(n\\,25\\,39)" in chain


def test_burnin_method_follows_ffmpeg(monkeypatch):
    monkeypatch.setattr(media, "has_filter", lambda name: True)
    assert burnin.method("auto") == "drawtext"
    assert burnin.method("pillow") == "pillow"
    monkeypatch.setattr(media, "has_filter", lambda name: False)
    assert burnin.method("auto") == "pillow"
    from slate.errors import MissingPrerequisite

    with pytest.raises(MissingPrerequisite):
        burnin.method("drawtext")


def test_packet_from_the_master_when_current(make_film):
    d = make_film("frommaster", mode="draft", brand="open-brand")
    filmkit.cli(d, "master")
    filmkit.cli(d, "review", "--no-send")
    rec = jsonio.read(d / "review/v1/packet.json")
    assert rec["source"]["kind"] == "master" and rec["source"]["files"][0].startswith("masters/")


def test_what_changed_between_versions(make_film):
    from slate.state import approvals

    d = make_film("chg", mode="draft", brand="open-brand")
    gates.write_report(d, [gates.GateResult.fail("loudness", "fixture: too loud")])
    code, out = filmkit.cli_json(d, "review", "--no-send")
    assert out["version"] == 1
    # nothing changed: the packet stands and no new version starts
    code, out = filmkit.cli_json(d, "review", "--no-send")
    assert out["version"] == 1 and out["built"] is False
    # a note, an approval, a gate that moved, and a re-rendered act
    filmkit.cli(d, "notes", "add", "the card lands late", "--at", "00:00:00:18")
    approvals.record(d, what="hero", path="renders/act1.mov", approved_by="Test Person")
    gates.write_report(d, [gates.GateResult.pass_("loudness", "fixture: fixed")])
    film = Film(d)
    filmkit.render_act(d / "renders/act2.mov", 15, film.size, film.rate, seed=5)
    reg = jsonio.read(d / "renders/registry.json")
    reg["entries"][1]["sha256"] = jsonio.sha256(d / "renders/act2.mov")
    jsonio.write_atomic(d / "renders/registry.json", reg)
    code, out = filmkit.cli_json(d, "review", "--no-send")
    assert code == 0 and out["version"] == 2 and out["started_version"] is True
    ch = jsonio.read(d / "review/v2/packet.json")["changes"]
    assert ch["since"] == 1 and ch["first"] is False
    assert "renders/act2.mov" in ch["content"]["changed"]
    assert [a["what"] for a in ch["approvals_new"]] == ["hero"]
    assert {"gate": "loudness", "from": "FAIL", "to": "PASS"} in ch["gates_moved"]
    assert ch["directions"][0]["text"] == "the card lands late"
    text = "\n".join(ch["summary"])
    assert "renders/act2.mov" in text and "Approved: hero" in text and "loudness: FAIL → PASS" in text
    assert jsonio.read(d / "film.json")["versions"][-1]["n"] == 2
    assert (d / "review/v1/packet.json").is_file()  # the earlier packet stays


def test_open_questions_are_in_the_packet(make_film):
    from slate.state import approvals

    d = make_film("qs", mode="draft", brand="open-brand")
    approvals.ask(d, "Is the logo the right one?", source="test")
    filmkit.cli(d, "review", "--no-send")
    rec = jsonio.read(d / "review/v1/packet.json")
    assert [q["text"] for q in rec["questions"]] == ["Is the logo the right one?"]
    assert "Is the logo the right one?" in (d / "review/v1/review.md").read_text()


def test_downloads_for_the_owners_films_once(make_film, fin):
    d = make_film("dl", mode="supervised", brand="open-brand")
    code, out = filmkit.cli_json(d, "review")
    assert out["sent"]["destination"] == "downloads" and out["sent"]["sent"] is True
    folder = fin["downloads"] / "dl v1 review"
    assert folder.is_dir() and {p.suffix for p in folder.iterdir()} == {".mp4", ".jpg", ".md"}
    # running it again doesn't copy the same packet twice
    code, out = filmkit.cli_json(d, "review")
    assert out["sent"] is None
    assert [p.name for p in fin["downloads"].iterdir()] == ["dl v1 review"]
    # asking explicitly copies again, into a new folder (nothing in Downloads is replaced)
    code, out = filmkit.cli_json(d, "review", "--to", "downloads")
    assert sorted(p.name for p in fin["downloads"].iterdir()) == ["dl v1 review", "dl v1 review (2)"]


def test_dry_run_builds_and_sends_nothing(make_film, fin, sends):
    d = make_film("drv", mode="supervised", brand="open-brand")
    code, p = filmkit.cli_json(d, "review", "--dry-run")
    assert code == 0 and p["would"] == "build the packet" and p["destination"] == "downloads"
    assert not (d / "review/v1").exists() and not list(fin["downloads"].iterdir()) and sends == []


def test_packet_interface_returns_the_packet(make_film):
    d = make_film("iface", mode="draft", brand="open-brand")
    path = review.packet(d)
    assert path == d / "review/v1/packet.json"


def test_qa_access_reads_the_packets_sheets(make_film):
    """qa-motion reads review sheets to check safe zones before a film has a render: its own code must find them,
    locate every cell, and recognise each cell's label as review material."""
    from slate.qa import access
    from slate.qa.film import FilmContext

    d = make_film("acc", mode="draft", brand="open-brand", format="finish-reel")
    filmkit.cli(d, "review", "--no-send")
    film = Film(d)
    ctx = FilmContext(d)
    found = access.find_sheets(ctx)
    assert [p.name for p in found] == ["acc_v1_00.json"]
    cells = access.sheet_cells(found[0], film_size=film.size, fps=float(film.clock.fps))
    idx = jsonio.read(found[0])
    assert len(cells) == len(idx["cells"]) == 4
    from slate.finish import sheet

    for c, row in zip(cells, idx["cells"]):
        assert c.image.size == tuple(idx["geometry"]["cell"]) and c.frame == row["film_frame"]
        label = sheet.cell_label(row["frame"], row["shot"], row["timecode"])
        box = [0.02, 0.9, 0.9, 0.99]  # where the label sits: the cell's bottom-left corner
        assert access.is_stamp(label, box, c.stamp), (label, c.stamp)



def _notification_hold_fixture(make_film, monkeypatch):
    """Use real parking and send logs, with fixed grade/delivery boundaries and no media process."""
    d = make_film("notification-hold", mode="autonomous", renders=False, mix=False, captions=False, voice=False)
    film = Film(d)
    folder = film.review_dir(1)
    folder.mkdir(parents=True)
    (folder / "phone.mp4").write_bytes(b"neutral phone-cut fixture; not production media")
    rec = {"version": 1, "film": {"seconds": film.seconds, "width": film.size[0], "height": film.size[1]},
           "changes": {"summary": []}, "questions": [], "sheet": {"step_s": 0.5},
           "files": {"phone_cut": {"file": "phone.mp4"}, "contact_sheets": []}}
    jsonio.write_atomic(folder / "packet.json", rec)
    monkeypatch.setattr(review.grade, "require_pass",
                        lambda *_: {"verdict": "PASS", "by": "Separate Fixture Reviewer", "at": "fixture"})
    monkeypatch.setattr(review.versions, "delivered", lambda *_: True)
    monkeypatch.setattr(review, "build", lambda *args, **kwargs:
                        {"packet": folder / "packet.json", "version": 1, "built": False,
                         "started_version": False, "record": rec})
    return d, film, rec


@pytest.mark.parametrize("initial_failure", ["transport", "missing"])
def test_notification_hold_without_delivery_remains_retryable(make_film, settings_toml, monkeypatch, initial_failure):
    from slate import notify
    from slate.state import approvals

    d, film, _rec = _notification_hold_fixture(make_film, monkeypatch)
    configured = '[notify]\nimessage_handle = "+15550100200"\nimessage_route = "osascript"\n' \
                 'push_command = ["fixture-push", "{title}", "{body}"]\n'
    if initial_failure == "transport":
        settings_toml(configured)
    monkeypatch.setenv("SLATE_LIVE", "1")
    seen = []
    failing = True

    def transport(argv, *, timeout):
        seen.append("imessage" if argv[0] == "/usr/bin/osascript" else "push")
        return {"returncode": 1 if failing else 0, "stdout": "", "stderr": "fixture route failed" if failing else ""}

    monkeypatch.setattr(notify, "transport", transport)
    code, first = filmkit.cli_json(d, "review")
    assert code == 0 and first["sent"]["held"] is True and first["sent"]["sent"] is False
    sends = first["sent"]["sends"]
    assert [s["channel"] for s in sends] == ["imessage", "push"] and not any(s["sent"] for s in sends)
    if initial_failure == "missing":
        assert all(s["missing"] for s in sends) and seen == []
    else:
        assert all(s["error"] == "error" for s in sends) and seen == ["imessage", "push"]
    assert not review.already_sent(film, 1, "imessage")
    original_question = approvals.open_questions(d, parking=True)[0]
    packet_sha = jsonio.sha256(film.review_dir(1) / "packet.json")
    before_log = review.sent_log(film, 1)
    assert len(before_log) == 1 and before_log[0]["held"] is True and before_log[0]["sent"] is False
    assert before_log[0]["packet_sha256"] == packet_sha and before_log[0]["question"] == original_question["id"]

    settings_toml(configured)
    failing = False
    seen.clear()
    code, second = filmkit.cli_json(d, "review")
    assert code == 0 and second["sent"]["sent"] is True and second["sent"]["held"] is True
    assert seen == ["imessage", "push"] and review.already_sent(film, 1, "imessage")
    questions = approvals.open_questions(d, parking=True)
    assert len(questions) == 1
    assert {k: questions[0][k] for k in ("id", "text", "source", "evidence", "parks")} == \
           {k: original_question[k] for k in ("id", "text", "source", "evidence", "parks")}
    after_log = review.sent_log(film, 1)
    assert len(after_log) == 2 and after_log[0] == before_log[0]
    assert all(e["question"] == original_question["id"] and e["packet_sha256"] == packet_sha for e in after_log)
    seen.clear()
    code, third = filmkit.cli_json(d, "review")
    assert code == 0 and third["sent"] is None and seen == []
    code, explicit = filmkit.cli_json(d, "review", "--to", "imessage")
    assert code == 0 and explicit["sent"]["sent"] is True and seen == ["imessage", "push"]


@pytest.mark.parametrize("failed_channel", ["imessage", "push"])
def test_notification_hold_partial_success_keeps_existing_default_suppression(make_film, settings_toml,
                                                                              monkeypatch, failed_channel):
    from slate import notify

    d, film, _rec = _notification_hold_fixture(make_film, monkeypatch)
    settings_toml('[notify]\nimessage_handle = "+15550100200"\nimessage_route = "osascript"\n'
                  'push_command = ["fixture-push"]\n')
    monkeypatch.setenv("SLATE_LIVE", "1")
    seen = []

    def transport(argv, *, timeout):
        channel = "imessage" if argv[0] == "/usr/bin/osascript" else "push"
        seen.append(channel)
        return {"returncode": 1 if channel == failed_channel else 0, "stdout": "", "stderr": "fixture failure"}

    monkeypatch.setattr(notify, "transport", transport)
    code, first = filmkit.cli_json(d, "review")
    assert code == 0 and first["sent"]["held"] is True and first["sent"]["sent"] is True
    assert seen == ["imessage", "push"] and review.already_sent(film, 1, "imessage")
    sends = {s["channel"]: s for s in first["sent"]["sends"]}
    assert not sends[failed_channel]["sent"] and sends[failed_channel]["error"] == "error"
    assert len(review.sent_log(film, 1)) == 1
    seen.clear()
    code, second = filmkit.cli_json(d, "review")
    assert code == 0 and second["sent"] is None and seen == []
    code, explicit = filmkit.cli_json(d, "review", "--to", "imessage")
    assert code == 0 and explicit["sent"]["sent"] is True and seen == ["imessage", "push"]
