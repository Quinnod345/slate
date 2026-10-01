"""Resuming without paying twice: abandoned in-flight rows are polled through provider hooks, never resubmitted."""

from __future__ import annotations

import os
import signal
import subprocess
import sys

import pytest

from jobs_helpers import PY, job_state, wait_for
from slate import ledger
from slate.cli import run_cli
from slate.jobs import client, proc, resume, runner, store


@pytest.fixture(autouse=True)
def _clean_hooks():
    saved_p, saved_b = dict(resume._POLLERS), dict(resume._BALANCES)
    yield
    resume._POLLERS.clear()
    resume._POLLERS.update(saved_p)
    resume._BALANCES.clear()
    resume._BALANCES.update(saved_b)


def _crashed_call(key: str, *, request_id: str | None = "req-42", provider: str = "fakeprov", job: str | None = None,
                  amount: float = 2.0) -> str:
    """A paid call made by a process that died mid-wait: its in-flight row has a request id and a dead pid."""
    code = ("import os\n"
            "from slate import ledger\n"
            f"est = ledger.estimate({provider!r}, 'gen', amount={amount})\n"
            f"rid = ledger.begin(est, key={key!r})\n"
            + (f"ledger.request(rid, {request_id!r})\n" if request_id else "")
            + "print(rid, flush=True)\n"
            "os._exit(9)\n")
    env = dict(os.environ)
    if job:
        env["SLATE_JOB_ID"] = job
    out = subprocess.run([PY, "-c", code], capture_output=True, text=True, env=env)
    return out.stdout.strip()


def test_paid_call_adopts_an_abandoned_request_instead_of_paying_again(slate_home):
    row_id = _crashed_call("k1")
    (row,) = resume.pending(key="k1")
    assert row["id"] == row_id and row["request_id"] == "req-42" and resume.abandoned(row)

    def submit():
        raise AssertionError("submitted a second, paid request")

    waited = []
    result = resume.paid_call(ledger.estimate("fakeprov", "gen", amount=2.0), key="k1", submit=submit,
                              wait=lambda rid: waited.append(rid) or {"rid": rid, "cost": 1.9},
                              cost=lambda r: r["cost"])
    assert result == {"rid": "req-42", "cost": 1.9} and waited == ["req-42"]
    (only,) = ledger.rows()
    assert only["id"] == row_id and only["status"] == "done" and only["actual"] == 1.9
    assert only["adopted_by"]["pid"] == os.getpid()
    assert resume.pending() == []


def test_paid_call_pays_once_when_nothing_was_in_flight(slate_home):
    calls = []
    result = resume.paid_call(ledger.estimate("fakeprov", "gen", amount=2.0), key="k2",
                              submit=lambda: calls.append(1) or "req-new", wait=lambda rid: {"rid": rid},
                              summarize=lambda r: {"rid": r["rid"]})
    assert result == {"rid": "req-new"} and calls == [1]
    (row,) = ledger.rows()
    assert row["status"] == "done" and row["request_id"] == "req-new" and row["key"] == "k2"
    assert row["pid"] == os.getpid() and row["proc_start"] and row["result"] == {"rid": "req-new"}


def test_a_live_callers_row_is_never_adopted(slate_home):
    rid = ledger.begin(ledger.estimate("fakeprov", "gen", amount=1.0), key="k3")
    ledger.request(rid, "req-live")
    assert resume.pending(key="k3") == []  # our own process is still waiting on it


def test_sync_call_without_a_request_id_is_orphaned_then_redone(slate_home):
    row_id = _crashed_call("k4", request_id=None)
    calls = []
    resume.paid_call(ledger.estimate("fakeprov", "tts", amount=3.0), key="k4", submit=lambda: calls.append(1) or b"audio",
                     wait=None, request_id_of=lambda r: "hist-1")
    assert calls == [1]
    rows = {r["id"]: r for r in ledger.rows()}
    assert rows[row_id]["status"] == "orphaned" and "can't tell whether it billed" in rows[row_id]["reason"]
    new = [r for r in rows.values() if r["id"] != row_id][0]
    assert new["status"] == "done" and new["request_id"] == "hist-1"


def test_recover_settles_rows_through_the_provider_hook(slate_home):
    done_row = _crashed_call("a", request_id="r-done")
    failed_row = _crashed_call("b", request_id="r-failed")
    running_row = _crashed_call("c", request_id="r-running")
    blind_row = _crashed_call("d", request_id=None)
    answers = {"r-done": {"status": "done", "actual": 1.5, "result": {"url": "x"}},
               "r-failed": {"status": "failed", "why": "moderated"},
               "r-running": {"status": "running"}}
    polled = []
    resume.register("fakeprov", lambda rid, row: polled.append(rid) or answers.get(rid, {"status": "unknown"}))
    outcomes = {o["row"]: o for o in resume.recover()}
    rows = {r["id"]: r for r in ledger.rows()}
    assert rows[done_row]["status"] == "done" and rows[done_row]["actual"] == 1.5
    assert rows[failed_row]["status"] == "failed" and rows[failed_row]["reason"] == "moderated"
    assert rows[running_row]["status"] == "inflight"  # the resumed job adopts it and waits
    assert rows[blind_row]["status"] == "orphaned"
    assert outcomes[running_row]["action"].startswith("still running")
    assert set(polled) >= {"r-done", "r-failed", "r-running"}


def test_recover_leaves_a_row_for_a_process_with_the_vendor_module(slate_home):
    row_id = _crashed_call("e", request_id="r-x", provider="nohookprov")
    (outcome,) = resume.recover()
    assert outcome["row"] == row_id and outcome["action"].startswith("left in flight")
    assert ledger.rows()[0]["status"] == "inflight"


def test_requeueing_a_dead_job_settles_its_paid_requests(queue_home):
    job_id = client.submit("gen", ["true"], start_runner=False)
    dead = subprocess.run([PY, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    with store.update(job_id) as job:
        job.update(state="running", phase="running", launch="t", pid=int(dead.stdout), attempts=1)
    row_id = _crashed_call("k-job", request_id="r-job", job=job_id)
    resume.register("fakeprov", lambda rid, row: {"status": "done", "actual": 2.0})
    assert runner.reconcile()["requeued"] == [job_id]
    (row,) = ledger.rows()
    assert row["id"] == row_id and row["job"] == job_id and row["status"] == "done"


def test_a_job_killed_mid_wait_resumes_and_polls_instead_of_paying_twice(queue_home, tmp_path):
    """The whole story: a job pays for a generation, its supervisor is killed while it waits, the queue requeues it,
    and the second attempt waits on the request the first one already paid for."""
    calls, waiting = tmp_path / "submits", tmp_path / "waiting"
    script = tmp_path / "gen.py"
    script.write_text(
        "import os, sys, time, json\n"
        "from pathlib import Path\n"
        "from slate import ledger\n"
        "from slate.jobs import resume\n"
        f"calls, waiting = Path({str(calls)!r}), Path({str(waiting)!r})\n"
        "def submit():\n"
        "    calls.write_text((calls.read_text() if calls.exists() else '') + 'submit\\n')\n"
        "    return 'req-7'\n"
        "def wait(rid):\n"
        "    if os.environ['SLATE_JOB_ATTEMPT'] == '1':\n"
        "        waiting.write_text('x')\n"
        "        time.sleep(120)\n"
        "    return {'rid': rid, 'cost': 3.0}\n"
        "est = ledger.estimate('fakeprov', 'gen', amount=3.0)\n"
        "print('result', json.dumps(resume.paid_call(est, key='k-e2e', submit=submit, wait=wait,\n"
        "                                            cost=lambda r: r['cost'])), flush=True)\n"
    )
    job_id = client.submit("gen", [PY, str(script)], start_runner=False)
    assert runner.schedule() == [job_id]
    wait_for(waiting.exists, what="the first attempt to be waiting on its paid request")
    wait_for(lambda: store.load(job_id).get("cmd_pid"), what="the supervisor to note its command")
    supervisor = store.load(job_id)["pid"]
    os.kill(supervisor, signal.SIGKILL)  # the supervisor dies (a crash, a reboot); the command is orphaned
    wait_for(lambda: not proc.alive(supervisor), what="the supervisor to be gone")
    first = store.load(job_id)
    assert runner.reconcile()["requeued"] == [job_id]  # resumable: back in the queue, leftovers stopped
    # the orphaned first attempt is gone, so it can't finish the paid call behind the resumed attempt's back
    assert first.get("cmd_pid") and not proc.alive(first["cmd_pid"], first.get("cmd_start"))
    job = store.load(job_id)
    assert job["state"] == "queued" and job["attempts"] == 1
    assert runner.schedule() == [job_id]
    wait_for(lambda: job_state(job_id) == "done", timeout=60, what="the resumed attempt to finish")
    assert calls.read_text() == "submit\n"  # paid once, however many attempts
    (row,) = ledger.rows()
    assert row["status"] == "done" and row["request_id"] == "req-7" and row["actual"] == 3.0
    assert row["job"] == job_id and row["adopted_by"]["job"] == job_id
    assert store.load(job_id)["attempts"] == 2
    assert '"rid": "req-7"' in client.tail(job_id, 80)


def test_ledger_inflight_cli_shows_and_recovers_abandoned_rows(slate_home):
    row_id = _crashed_call("k-cli", request_id="r-cli")
    code, out, _ = run_cli(["ledger", "inflight"])
    assert code == 0 and row_id in out and "ABANDONED" in out
    resume.register("fakeprov", lambda rid, row: {"status": "done", "actual": 2.0})
    code, out, _ = run_cli(["--json", "ledger", "inflight", "--recover"])
    assert code == 0
    import json

    data = json.loads(out)
    assert data["recovered"][0]["row"] == row_id and data["recovered"][0]["status"] == "done"
    assert ledger.inflight() == []


def test_default_higgsfield_hook_books_the_quoted_price(slate_home, monkeypatch):
    import types

    answers = {"r-ok": "completed", "r-mod": "nsfw", "r-wait": "in_progress"}

    class FakeClient:
        def status(self, request_id):
            return {"status": answers[request_id]}

    fake = types.ModuleType("slate.vendors.higgsfield")
    fake.Client = FakeClient
    fake.UNBILLED = frozenset({"failed", "nsfw", "canceled"})
    monkeypatch.setitem(sys.modules, "slate.vendors.higgsfield", fake)
    import slate.vendors

    monkeypatch.setattr(slate.vendors, "higgsfield", fake, raising=False)
    monkeypatch.delitem(resume._POLLERS, "higgsfield", raising=False)  # restored after: the real module's own hook
    monkeypatch.setattr(resume, "_tried", set(resume._tried) | {"slate.vendors.higgsfield"})
    # The estimate's amount is /estimate's usd: the price after any discount. discount.usd is the saving, and a
    # saving that rides along in the details is never booked as the price.
    row = {"estimate": {"amount": 2.31, "details": {"saving_usd": 0.46, "list_usd": 2.77}}}
    assert resume.poller("higgsfield") is resume._higgsfield_poll  # nothing registered: Slate's default hook
    assert resume._higgsfield_poll("r-ok", row) == {"status": "done", "actual": 2.31,
                                                   "result": {"status": "completed", "recovered": True}}
    assert resume._higgsfield_poll("r-mod", row)["status"] == "failed"
    assert resume._higgsfield_poll("r-wait", row) == {"status": "running"}
    assert resume._higgsfield_poll(None, row)["status"] == "unknown"
