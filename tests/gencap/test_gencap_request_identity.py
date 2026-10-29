"""A resumed generation keeps the model, request and uploaded bytes that made its existing takes."""

import pytest
from PIL import Image

from slate import jsonio, ledger
from slate.errors import Refused
from slate.gen import run, store


def _edit_block(film, **changes):
    path = film / "timing" / "shots.json"
    data = jsonio.read(path)
    shot = next(s for s in data["shots"] if s["id"] == "S04")
    shot["generate"].update(changes)
    jsonio.write_atomic(path, data)


@pytest.mark.parametrize("change", [
    {"prompt": "a different street at night"},
    {"model": "another/still-model"},
    {"resolution": "2K"},
])
@pytest.mark.parametrize("force", [False, True])
def test_changed_request_refuses_without_spend_or_record_changes(film, change, force):
    run.run(film, "S04")
    before = store.record_path(film, "S04").read_bytes()
    rows = ledger.rows()
    _edit_block(film, **change)

    with pytest.raises(Refused, match="generation request changed"):
        run.run(film, "S04", force=force)

    assert store.record_path(film, "S04").read_bytes() == before
    assert ledger.rows() == rows


def test_changed_upload_bytes_refuse_even_when_the_path_is_unchanged(film):
    _edit_block(film, image="refs/start.png")
    run.run(film, "S04")
    before = store.record_path(film, "S04").read_bytes()
    rows = ledger.rows()
    Image.new("RGB", (320, 180), (40, 50, 60)).save(film / "refs" / "start.png")

    with pytest.raises(Refused, match="generation request changed"):
        run.run(film, "S04")

    assert store.record_path(film, "S04").read_bytes() == before
    assert ledger.rows() == rows


def test_more_takes_of_the_same_request_keep_existing_takes(film):
    run.run(film, "S04")
    before = store.load_record(film, "S04")["takes"]
    out = run.run(film, "S04", takes=3)

    assert out["new_takes"] == 1
    assert out["completed"] == 3
    assert store.load_record(film, "S04")["takes"][:2] == before


def test_changed_request_does_not_resend_an_unanswered_take(film, monkeypatch):
    run.run(film, "S04")
    rec = store.load_record(film, "S04")
    rec["takes"][0].update(status="submitted", request_id=None, resend=True, files=[])
    store.save_record(film, "S04", rec)
    _edit_block(film, prompt="a different shot")
    monkeypatch.setattr(run, "_resubmit", lambda *a, **k: pytest.fail("old request was resent"))

    with pytest.raises(Refused, match="generation request changed"):
        run.run(film, "S04")
