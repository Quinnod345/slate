"""Autonomous films: every generation waits for a person's yes, the request is written and never sent from a test,
and the ledger refuses the spend without an approval id."""

from __future__ import annotations

import json
import types

import pytest

from gencap_helpers import SHOTS, run_json, write_timing
from slate import jsonio, ledger
from slate.errors import SpendRefused
from slate.gen import deps, run as gen_run, store


@pytest.fixture
def auto_film(make_film):
    return make_film(mode="autonomous")


def test_no_approval_no_generation(auto_film):
    code, out, _ = run_json(["gen", "run", "--film", str(auto_film), "--shot", "S02"])
    assert code == 4 and "yes" in out["message"] and "request-approval" in out["hint"]
    assert ledger.rows() == [] and store.load_record(auto_film, "S02") is None


def test_the_request_is_written_and_never_sent_from_a_test(auto_film, monkeypatch):
    sent = []
    fake_notify = types.SimpleNamespace(imessage=lambda text, attachment=None: sent.append(("imessage", text)),
                                        push=lambda title, body: sent.append(("push", title)))
    monkeypatch.setattr(deps, "notify_module", lambda: fake_notify)
    code, req, err = run_json(["gen", "request-approval", "--film", str(auto_film), "--shot", "S02"])
    assert code == 0, err
    assert sent == [] and req["notify"]["sent"] is False  # tests and replay never send
    saved = jsonio.read(auto_film / req["file"])
    assert deps.validate_part({k: v for k, v in saved.items() if k != "generated"}, "generation",
                              "approval_request") == []
    assert saved["takes"] == 2 and saved["estimate_usd_total"] == pytest.approx(1.848)
    assert req["id"] in saved["message"] and "Reply yes" in saved["message"]


def test_when_sending_is_allowed_it_goes_by_imessage_and_push(auto_film, monkeypatch):
    sent = []
    fake_notify = types.SimpleNamespace(imessage=lambda text, attachment=None: sent.append("imessage"),
                                        push=lambda title, body: sent.append("push"))
    monkeypatch.setattr(deps, "notify_module", lambda: fake_notify)
    monkeypatch.setattr(deps, "notify_allowed", lambda: True)  # the fake module above stands in for slate.notify
    req = gen_run.request_approval(auto_film, "S02")
    assert sent == ["imessage", "push"] and req["notify"]["sent"] is True


def test_a_channel_that_isnt_set_up_is_reported_and_never_stops_the_other(auto_film, monkeypatch):
    from slate.errors import MissingPrerequisite

    def no_handle(text, attachment=None):
        raise MissingPrerequisite("No iMessage handle is set for the owner, so Slate can't send this.")

    fake_notify = types.SimpleNamespace(imessage=no_handle, push=lambda title, body: {"sent": True, "why": "sent"})
    monkeypatch.setattr(deps, "notify_module", lambda: fake_notify)
    monkeypatch.setattr(deps, "notify_allowed", lambda: True)
    note = gen_run.request_approval(auto_film, "S02")["notify"]
    assert note["imessage"] is False and "handle" in note["imessage_error"]
    assert note["push"] is True and note["sent"] is True
    blocked = types.SimpleNamespace(imessage=lambda text, attachment=None: {"sent": False, "why": "not sent: dry run"},
                                    push=lambda title, body: {"sent": False, "why": "not sent: dry run"})
    monkeypatch.setattr(deps, "notify_module", lambda: blocked)
    note = gen_run.request_approval(auto_film, "S02")["notify"]
    assert note["sent"] is False and note["imessage"] is False and "yourself" in note["why"]


def test_waiting_approving_then_generating(auto_film):
    _, req, _ = run_json(["gen", "request-approval", "--film", str(auto_film), "--shot", "S02"])
    code, out, _ = run_json(["gen", "run", "--film", str(auto_film), "--shot", "S02", "--approval", req["id"]])
    assert code == 4 and "waiting" in out["message"]
    code, ok, err = run_json(["gen", "approve", "--film", str(auto_film), "--id", req["id"], "--by", "Quinn",
                              "--note", "iMessage yes"])
    assert code == 0, err
    yes = ok["yes"]
    assert yes and yes.startswith("ap_")
    code, out, err = run_json(["gen", "run", "--film", str(auto_film), "--shot", "S02", "--approval", req["id"]])
    assert code == 0, err
    rows = ledger.rows(provider="higgsfield")
    assert len(rows) == 2 and {r["mode"] for r in rows} == {"autonomous"}
    # the ledger lets an approval id pay for one call: each take spends under its own slot of the one yes
    assert sorted(r["approval_id"] for r in rows) == [f"{yes}#1", f"{yes}#2"]
    rec = store.load_record(auto_film, "S02")
    assert rec["approval"]["id"] == req["id"] and rec["approval"]["yes"] == yes


def test_a_recorded_spend_approval_id_works_too(auto_film):
    req = gen_run.request_approval(auto_film, "S02")
    record = deps.approvals().record(auto_film, what="spend", path=req["file"], approved_by="Quinn")
    out = gen_run.run(auto_film, "S02", approval=record["id"])
    assert out["completed"] == 2


def test_a_changed_request_isnt_covered_by_an_old_yes(auto_film):
    req = gen_run.request_approval(auto_film, "S02")
    gen_run.approve(auto_film, req["id"], by="Quinn")
    shots = json.loads(json.dumps(SHOTS))
    shots[1]["generate"]["prompt"] = "a stormy shoreline at night"
    write_timing(auto_film, shots)
    with pytest.raises(SpendRefused) as exc:
        gen_run.run(auto_film, "S02", approval=req["id"])
    assert "changed" in exc.value.message
    assert ledger.rows() == []


def test_the_ledger_refuses_the_spend_without_an_approval_id(auto_film, monkeypatch):
    """Belt and braces: even if a caller skipped Slate's own check, a live autonomous spend has no path through."""
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "0")
    est = ledger.estimate("higgsfield", "generate", amount=0.92)
    with pytest.raises(SpendRefused):
        ledger.check_budget(est, film=auto_film, mode="autonomous")
    with pytest.raises(SpendRefused):  # and the default autonomous Higgsfield cap is $0 until a person raises it
        ledger.check_budget(est, film=auto_film, mode="autonomous", approval_id="ap_1234567890")


def test_supervised_films_dont_wait(film):
    prep = gen_run.prepare(film, "S02")
    assert not prep.needs_approval
    assert gen_run.check_approval(prep, None) == (None, None)


def _still(dest, stem):
    from PIL import Image

    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{stem}.png"
    Image.new("RGB", (32, 18), (90, 90, 90)).save(path)
    return path


def test_live_autonomous_takes_each_spend_their_own_slot_of_the_yes(auto_film, slate_home, monkeypatch):
    """Live, against a fake API: the ledger checks everything replay skips. The person's one yes covered two takes;
    both go through, each under its own slot, and the yes can't pay for a third."""
    from test_gencap_client import FakeTransport
    from slate.vendors import higgsfield

    monkeypatch.setenv("SLATE_FAKE_VENDORS", "0")
    # a person raised both caps (autonomous films start at $0 of Higgsfield a month, and this film at $0)
    (slate_home["data"] / "budget.json").write_text(json.dumps({"monthly": {"autonomous": {"higgsfield": 10}}}))
    fj = jsonio.read(auto_film / "film.json")
    fj.setdefault("budget", {})["higgsfield"] = 10
    jsonio.write_atomic(auto_film / "film.json", fj)
    fake = FakeTransport(status_answers=[{"status": "completed", "images": [{"url": "https://cdn.example/x.png"}]}])
    client = higgsfield.Client(transport=fake, key="k:s", fake=False, sleep=lambda s: None)
    monkeypatch.setattr(client, "download", lambda url, dest, stem: _still(dest, stem))
    req = gen_run.request_approval(auto_film, "S04", client=client)
    yes = gen_run.approve(auto_film, req["id"], by="Quinn")["yes"]
    out = gen_run.run(auto_film, "S04", approval=req["id"], client=client)
    assert out["completed"] == 2
    rows = ledger.rows(provider="higgsfield")
    assert sorted(r["approval_id"] for r in rows) == [f"{yes}#1", f"{yes}#2"]
    assert all(r["status"] == "done" and r["actual"] == 1.0 and not r["replay"] for r in rows)
    with pytest.raises(SpendRefused) as exc:
        gen_run.approval_slot(yes, 2)
    assert "every one of them has been paid for" in exc.value.message
