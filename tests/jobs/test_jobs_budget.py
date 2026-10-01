"""Budgets and the ledger's hardening: who may change what, the autonomous caps, format defaults, reconcile."""

from __future__ import annotations

import datetime as dt
import json
import sys
import types

import pytest

from slate import config, jsonio, ledger, procs, secrets
from slate.cli import run_cli
from slate.errors import MissingPrerequisite, Refused, SpendRefused
from slate.jobs import resume


@pytest.fixture
def live(slate_home, monkeypatch):
    """Budgets only bite on real spend, so these tests turn replay off (nothing here calls a vendor)."""
    monkeypatch.setenv("SLATE_FAKE_VENDORS", "0")
    return slate_home


@pytest.fixture
def person(monkeypatch):
    """A person typing in their own Terminal: no agent around, and stdin is a TTY."""
    monkeypatch.setattr(procs, "agent_context", lambda: None)
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))


def _without_formats(monkeypatch):
    """As if the formats module weren't built: hide it from the import system *and* from the slate package, because
    `from slate import formats` finds the package attribute once any earlier test imported the real module."""
    import slate

    monkeypatch.setitem(sys.modules, "slate.formats", None)
    monkeypatch.delattr(slate, "formats", raising=False)


def _film(films, film_id, **fields):
    folder = films / film_id
    folder.mkdir()
    (folder / "film.json").write_text(json.dumps({"id": film_id, **fields}))
    return folder


# ---- slate budget set: only a person, in Terminal -------------------------------------------------------------

def test_budget_set_is_refused_for_agents(slate_home, monkeypatch):
    monkeypatch.setenv("SLATE_CONTEXT", "codex")
    code, out, err = run_cli(["budget", "set", "--mode", "autonomous", "--provider", "elevenlabs", "--cap", "90000"])
    assert code == 1 and "only by a person, in their own Terminal" in err and "codex" in err
    assert not (slate_home["data"] / "budget.json").exists()
    code, out, _ = run_cli(["--json", "budget", "set", "--mode", "autonomous", "--provider", "elevenlabs",
                            "--cap", "90000"])
    assert code == 1 and json.loads(out)["error"] == "refused"


def test_budget_set_is_refused_without_a_terminal(slate_home, monkeypatch):
    monkeypatch.setattr(procs, "agent_context", lambda: None)  # no agent, but stdin isn't a TTY (a pipe, a job)
    code, _, err = run_cli(["budget", "set", "--mode", "supervised", "--provider", "higgsfield", "--cap", "50"])
    assert code == 1 and "isn't an interactive Terminal" in err
    with pytest.raises(Refused):
        ledger.set_cap("supervised", "higgsfield", 50)


def test_budget_set_by_a_person_changes_the_cap(live, person, monkeypatch):
    monkeypatch.setattr(secrets, "present", lambda name: "keychain")
    big = ledger.estimate("elevenlabs", "music", amount=24000)
    with pytest.raises(SpendRefused):
        ledger.check_budget(big, mode="autonomous")
    code, out, err = run_cli(["--json", "budget", "set", "--mode", "autonomous", "--provider", "elevenlabs",
                              "--cap", "25000"])
    assert code == 0, err
    change = json.loads(out)
    assert change["from"] == 20000 and change["to"] == 25000
    saved = jsonio.read(live["data"] / "budget.json")
    assert saved["monthly"]["autonomous"]["elevenlabs"] == 25000 and saved["history"][-1]["to"] == 25000
    assert any(e.get("event") == "cap" for e in ledger._events())  # the change is in the ledger too
    ledger.check_budget(big, mode="autonomous")  # 24,000 fits the new cap
    code, _, _ = run_cli(["budget", "set", "--mode", "draft", "--provider", "higgsfield", "--cap", "none",
                          "--approval-required", "yes"])
    assert code == 0
    cfg = ledger.budget_config()
    assert cfg["monthly"]["draft"]["higgsfield"] is None and "higgsfield" in cfg["approval_required"]["draft"]
    code, _, _ = run_cli(["budget", "set", "--mode", "draft", "--provider", "higgsfield", "--cap", "lots"])
    assert code == 2


# ---- slate budget film: a person's yes, never for autonomous films -----------------------------------------

def test_budget_film_is_refused_for_autonomous_films(slate_home):
    film = _film(slate_home["films"], "reel", mode="autonomous", budget={"elevenlabs": 3000})
    code, out, _ = run_cli(["--json", "--film", str(film), "budget", "film", "--provider", "elevenlabs",
                            "--raise", "1000", "--approved-by", "Quinn"])
    assert code == 4
    err = json.loads(out)
    assert err["error"] == "spend_refused" and "monthly budget file" in err["message"]
    assert ledger.raises("reel") == []


def test_budget_film_records_a_raise_for_a_supervised_film(live):
    film = _film(live["films"], "promo", mode="supervised", budget={"elevenlabs": 1000})
    est = ledger.estimate("elevenlabs", "tts", amount=1400)
    with pytest.raises(SpendRefused) as exc:
        ledger.check_budget(est, film=film)
    assert "slate budget film --film promo" in exc.value.hint
    code, out, err = run_cli(["--json", "--film", str(film), "budget", "film", "--raise", "500",
                              "--approved-by", "Quinn", "--note", "yes in chat, 14:02"])
    assert code == 0, err
    data = json.loads(out)
    assert data["raise"]["approved_by"] == "Quinn" and data["raise"]["note"] == "yes in chat, 14:02"
    assert data["budget"]["budget"] == 1500 and data["budget"]["raised"] == 500
    ledger.check_budget(est, film=film)  # 1,400 now fits under 1,500
    # rework may reach 1.5x the raised budget, and no further
    ledger.check_budget(ledger.estimate("elevenlabs", "tts", amount=2200), film=film, rework=True)
    with pytest.raises(SpendRefused):
        ledger.check_budget(ledger.estimate("elevenlabs", "tts", amount=2300), film=film, rework=True)


def test_a_brands_onboarding_budget_can_be_raised_like_a_films(slate_home):
    """`--film onboarding-<brand>` is the brand's onboarding billing id (slate.brand.onboard.billing), not a folder."""
    code, out, err = run_cli(["--json", "--film", "onboarding-demo", "budget", "film", "--provider", "elevenlabs",
                              "--raise", "2000", "--approved-by", "Quinn"])
    assert code == 0, err
    data = json.loads(out)
    assert data["raise"]["film"] == "onboarding-demo" and data["budget"]["raised"] == 2000
    from slate.brand import onboard

    limits = ledger.film_limits(onboard.billing("demo"))["providers"]["elevenlabs"]
    assert limits["budget"] == data["budget"]["budget"]  # onboarding spend checks against the raised budget
    code, _, err = run_cli(["--film", "onboarding-no-such-brand", "budget", "film", "--provider", "elevenlabs",
                            "--raise", "10", "--approved-by", "Quinn"])
    assert code == 2


def test_an_aliases_budget_names_its_own_format_file():
    assert ledger.format_budget("reel")["source"] == ledger.format_budget("social-reel")["source"] == \
        "formats/social-reel.json"


def test_budget_film_needs_a_provider_when_the_film_has_two(slate_home):
    film = _film(slate_home["films"], "both", mode="supervised", budget={"elevenlabs": 1000, "higgsfield": 40})
    code, _, err = run_cli(["--film", str(film), "budget", "film", "--raise", "10", "--approved-by", "Quinn"])
    assert code == 2 and "--provider" in err
    code, _, _ = run_cli(["--film", str(film), "budget", "film", "--raise", "-5", "--approved-by", "Quinn",
                          "--provider", "higgsfield"])
    assert code == 2
    code, _, _ = run_cli(["--film", str(film), "budget", "film", "--raise", "10", "--approved-by", " ",
                          "--provider", "higgsfield"])
    assert code == 2


# ---- the autonomous caps -----------------------------------------------------------------------------------

def test_an_autonomous_film_is_held_to_the_monthly_cap_whatever_it_says(live, monkeypatch):
    monkeypatch.setattr(secrets, "present", lambda name: "keychain")
    film = _film(live["films"], "reel", mode="autonomous", budget={"elevenlabs": 10 ** 9})
    big = ledger.estimate("elevenlabs", "music", amount=25000)
    with pytest.raises(SpendRefused) as exc:
        ledger.check_budget(big, film=film, mode="supervised")  # the caller can't talk its way out of the cap
    assert "autonomous cap" in exc.value.message and exc.value.exit_code == 4
    ledger.check_budget(ledger.estimate("elevenlabs", "tts", amount=19000), film=film)
    rid = ledger.begin(ledger.estimate("elevenlabs", "tts", amount=19000), film=film, mode="supervised")
    assert ledger.rows()[0]["mode"] == "autonomous"  # recorded in the mode it was judged in
    ledger.finish(rid, actual=19000)
    with pytest.raises(SpendRefused):
        ledger.check_budget(ledger.estimate("elevenlabs", "tts", amount=1500), film=film)


def test_oneiros_key_counts_toward_the_same_cap(live, monkeypatch):
    monkeypatch.setattr(secrets, "present", lambda name: "keychain")
    rid = ledger.begin(ledger.estimate("elevenlabs_oneiro", "tts", amount=15000), mode="autonomous")
    ledger.finish(rid, actual=15000)
    assert ledger.spent("elevenlabs", mode="autonomous") == 15000
    with pytest.raises(SpendRefused):
        ledger.check_budget(ledger.estimate("elevenlabs", "tts", amount=6000), mode="autonomous")


def test_autonomous_elevenlabs_needs_oneiros_capped_key(live, monkeypatch):
    monkeypatch.setattr(secrets, "present", lambda name: None)
    with pytest.raises(MissingPrerequisite) as exc:
        ledger.check_budget(ledger.estimate("elevenlabs", "tts", amount=100), mode="autonomous")
    assert exc.value.exit_code == 3 and "slate connect elevenlabs --oneiro" in exc.value.hint
    assert ledger.secret_name("elevenlabs", "autonomous") == "elevenlabs_oneiro"
    assert ledger.secret_name("elevenlabs", "supervised") == "elevenlabs"
    ledger.check_budget(ledger.estimate("elevenlabs", "tts", amount=100), mode="supervised")


def test_every_autonomous_higgsfield_call_needs_its_own_yes(live):
    jsonio.write_atomic(live["data"] / "budget.json", {"monthly": {"autonomous": {"higgsfield": 20}}})
    plate = ledger.estimate("higgsfield", "plate", amount=2.31)
    with pytest.raises(SpendRefused) as exc:
        ledger.check_budget(plate, mode="autonomous")
    assert "no approval id" in exc.value.message
    ledger.check_budget(plate, mode="autonomous", approval_id="yes-1")
    rid = ledger.begin(plate, mode="autonomous", approval_id="yes-1")
    with pytest.raises(SpendRefused) as exc:
        ledger.check_budget(plate, mode="autonomous", approval_id="yes-1")
    assert "each yes covers one call" in exc.value.message
    ledger.finish(rid, status="failed")  # the vendor never billed it, so the yes is still unused
    ledger.check_budget(plate, mode="autonomous", approval_id="yes-1")


def test_default_autonomous_caps_are_20000_credits_and_no_higgsfield(slate_home):
    cfg = ledger.budget_config()
    assert cfg["monthly"]["autonomous"] == {"elevenlabs": 20000, "higgsfield": 0}
    assert cfg["approval_required"]["autonomous"] == ["higgsfield"]
    assert cfg["rework_factor"] == 1.5


# ---- per-format defaults ----------------------------------------------------------------------------------

def test_plan_format_budgets(slate_home, monkeypatch):
    _without_formats(monkeypatch)
    reel = ledger.format_budget("social-reel")
    assert (reel["elevenlabs"], reel["higgsfield"]) == (5000, 0) and "plan default" in reel["source"]
    assert (ledger.format_budget("ad-15")["elevenlabs"], ledger.format_budget("ad-15")["higgsfield"]) == (5000, 0)
    ex = ledger.format_budget("explainer")
    assert (ex["elevenlabs"], ex["higgsfield"]) == (10000, 60)
    film = ledger.format_budget("brand-film")
    assert (film["elevenlabs"], film["higgsfield"]) == (40000, 85)
    onboard = ledger.format_budget("brand-onboarding")
    assert (onboard["elevenlabs"], onboard["higgsfield"]) == (5000, 20)
    assert ledger.format_budget("something-new") == {}


def test_format_templates_win_over_the_plan_table(slate_home, monkeypatch):
    stub = types.ModuleType("slate.formats")
    stub.load = lambda name: {"name": name, "budget": {"elevenlabs": {"vo": [100, 200], "score": 300},
                                                       "higgsfield_usd": [0, 5]}}
    import slate

    monkeypatch.setitem(sys.modules, "slate.formats", stub)
    monkeypatch.setattr(slate, "formats", stub, raising=False)
    got = ledger.format_budget("reel")
    assert got["elevenlabs"] == 500 and got["higgsfield"] == 5 and got["source"] == "formats/reel.json"


def test_the_real_format_templates_carry_the_plans_budgets():
    """P163-P166 against the formats module's own templates (formats/*.json), not a stub: each row of the plan's cost
    table comes through slate.formats.load as the format's hard stop."""
    formats = pytest.importorskip("slate.formats")
    names = set(formats.names())
    for name, (credits, usd) in {"social-reel": (5000, 0), "ad": (5000, 0), "explainer": (10000, 60),
                                 "brand-film": (40000, 85)}.items():
        if name not in names:
            continue
        got = ledger.format_budget(name)
        assert (got["elevenlabs"], got["higgsfield"], got["source"]) == (credits, usd, f"formats/{name}.json"), got


def test_onboarding_reads_the_formats_shared_table(slate_home, monkeypatch):
    """formats' ask: onboarding isn't a film format, so its budget comes from formats/_shared.json through
    slate.formats.onboarding_budget(), and the plan's own row only when that isn't there."""
    import slate

    stub = types.ModuleType("slate.formats")
    stub.onboarding_budget = lambda: {"elevenlabs": {"voice_design": [4000, 4000]}, "higgsfield": [10, 15],
                                      "rework_factor": 1.5, "source": "plan"}
    stub.load = lambda name: (_ for _ in ()).throw(KeyError(name))
    monkeypatch.setitem(sys.modules, "slate.formats", stub)
    monkeypatch.setattr(slate, "formats", stub, raising=False)
    got = ledger.format_budget("brand-onboarding")
    assert (got["elevenlabs"], got["higgsfield"], got["source"]) == (4000, 15, "formats/_shared.json (onboarding)")
    _without_formats(monkeypatch)
    assert ledger.format_budget("brand-onboarding")["source"].startswith("plan default")


def test_a_film_without_a_budget_gets_its_formats(live, monkeypatch):
    _without_formats(monkeypatch)
    film = _film(live["films"], "short", mode="supervised", format="reel", budget={"higgsfield": 12})
    limits = ledger.film_limits(film)["providers"]
    assert limits["elevenlabs"]["base"] == 5000 and "plan default" in limits["elevenlabs"]["source"]
    assert limits["higgsfield"]["base"] == 12 and limits["higgsfield"]["source"] == "film.json"
    with pytest.raises(SpendRefused):
        ledger.check_budget(ledger.estimate("elevenlabs", "music", amount=6000), film=film)


# ---- ledger details ---------------------------------------------------------------------------------------

def test_months_are_utc(slate_home):
    evening = dt.datetime(2026, 9, 30, 21, 0, tzinfo=dt.timezone(dt.timedelta(hours=-5)))
    assert ledger.month_key(evening) == "2026-10"


def test_orphaned_rows_count_at_their_estimate(live):
    rid = ledger.begin(ledger.estimate("elevenlabs", "tts", amount=700))
    ledger.orphan(rid, "died before a request id")
    assert ledger.rows()[0]["status"] == "orphaned" and ledger.spent("elevenlabs") == 700


def test_out_of_balance_stops_and_asks(slate_home):
    err = ledger.out_of_balance("higgsfield", "HTTP 402")
    assert isinstance(err, SpendRefused) and err.exit_code == 4
    assert "never buys credits" in err.message and "Top up" in err.hint


def test_estimates_flag_stale_prices(slate_home, monkeypatch):
    monkeypatch.setattr(config, "model_info", lambda m: {"price": {"unit": "credits", "per_char": 1},
                                                         "verified_on": "2026-01-01"})
    est = ledger.estimate("elevenlabs", "tts", model="old_model", chars=10)
    assert est.details["price_stale"] is True and est.amount == 10


# ---- budget show, ledger list/snapshot/reconcile ------------------------------------------------------------

def test_budget_show(slate_home, monkeypatch):
    _without_formats(monkeypatch)
    _film(slate_home["films"], "promo", mode="supervised", format="explainer")
    _film(slate_home["films"], "reel", mode="autonomous", budget={"elevenlabs": 3000})
    code, out, _ = run_cli(["--json", "budget", "show"])
    assert code == 0
    report = json.loads(out)
    auto = [r for r in report["monthly"] if r["mode"] == "autonomous" and r["provider"] == "elevenlabs"][0]
    assert auto["cap"] == 20000 and auto["left"] == 20000
    hf = [r for r in report["monthly"] if r["mode"] == "autonomous" and r["provider"] == "higgsfield"][0]
    assert hf["cap"] == 0 and hf["approval_required"] is True
    films = {f["film"]: f for f in report["films"]}
    assert films["promo"]["providers"]["elevenlabs"]["budget"] == 10000
    assert films["reel"]["mode"] == "autonomous" and films["reel"]["providers"]["elevenlabs"]["budget"] == 3000
    assert report["rework_factor"] == 1.5 and report["price_notes"]
    code, out, _ = run_cli(["budget", "show"])
    assert code == 0 and "autonomous" in out and "Replay mode is on" in out
    code, out, _ = run_cli(["--json", "--film", "promo", "budget", "show"])
    assert code == 0 and [f["film"] for f in json.loads(out)["films"]] == ["promo"]


def test_ledger_snapshot_and_reconcile(slate_home, monkeypatch):
    code, out, err = run_cli(["--json", "ledger", "snapshot", "--provider", "higgsfield", "--balance", "120.5",
                              "--session", "s1"])
    assert code == 0, err
    snap = json.loads(out)["snapshots"][0]
    assert snap["source"] == "person" and snap["balance"] == 120.5 and snap["replay"] is True
    code, _, err = run_cli(["ledger", "snapshot", "--provider", "higgsfield"])  # no balance endpoint, no --balance
    assert code == 3 and "--balance" in err
    code, out, _ = run_cli(["--json", "ledger", "reconcile", "--provider", "higgsfield"])
    assert code == 1 and json.loads(out)["results"][0]["status"] == "UNCHECKED"
    # setitem, never register + pop: popping would also delete a reader a vendor module registered (it did once)
    with monkeypatch.context() as m:
        m.setitem(resume._BALANCES, "higgsfield", lambda: 100.5)
        code, out, _ = run_cli(["--json", "ledger", "snapshot", "--provider", "higgsfield", "--session", "s1"])
        assert code == 0 and json.loads(out)["snapshots"][0]["source"] == "replay"
    code, out, _ = run_cli(["--json", "ledger", "reconcile", "--provider", "higgsfield", "--session", "s1"])
    result = json.loads(out)["results"][0]
    assert code == 1 and result["status"] == "REVIEW" and result["unexplained"] == 20
    assert "spent outside Slate" in result["summary"]
    code, out, _ = run_cli(["ledger", "reconcile", "--provider", "higgsfield"])
    assert code == 1 and "REVIEW" in out


def test_reconcile_passes_when_the_ledger_explains_the_drop(live, monkeypatch):
    ledger.snapshot("elevenlabs", 10000, session="work")
    rid = ledger.begin(ledger.estimate("elevenlabs", "tts", amount=420))
    ledger.request(rid, "r1")
    ledger.finish(rid, actual=400)
    ledger.snapshot("elevenlabs", 9600, session="work")
    code, out, _ = run_cli(["--json", "ledger", "reconcile", "--provider", "elevenlabs", "--session", "work"])
    data = json.loads(out)
    assert code == 0 and data["status"] == "PASS" and data["results"][0]["ledger"] == 400
    ledger.snapshot("elevenlabs", 9800)  # a top-up
    rec = ledger.reconcile("elevenlabs")
    assert rec["status"] == "REVIEW" and "went up" in rec["summary"]


def test_snapshot_all_reads_what_it_can(slate_home, monkeypatch):
    with monkeypatch.context() as m:  # restores the reader slate.vendors.elevenlabs registered, if it had
        m.setitem(resume._BALANCES, "elevenlabs", lambda: 12345.0)
        code, out, _ = run_cli(["--json", "ledger", "snapshot", "--all"])
    data = json.loads(out)
    assert code == 0 and [s["provider"] for s in data["snapshots"]] == ["elevenlabs"]
    assert data["skipped"][0]["provider"] == "higgsfield"
    code, _, _ = run_cli(["ledger", "snapshot"])
    assert code == 2


def test_ledger_list(slate_home):
    film = _film(slate_home["films"], "f1", mode="supervised")
    a = ledger.begin(ledger.estimate("elevenlabs", "tts", amount=10), film=film)
    ledger.finish(a, actual=9)
    ledger.begin(ledger.estimate("higgsfield", "plate", amount=2.31))
    code, out, _ = run_cli(["--json", "ledger", "list"])
    assert code == 0 and json.loads(out)["count"] == 2
    code, out, _ = run_cli(["--json", "ledger", "list", "--status", "inflight"])
    assert [r["provider"] for r in json.loads(out)["rows"]] == ["higgsfield"]
    code, out, _ = run_cli(["--json", "--film", "f1", "ledger", "list"])
    assert [r["id"] for r in json.loads(out)["rows"]] == [a]
    code, out, _ = run_cli(["ledger", "list"])
    assert code == 0 and "replay" in out


def test_draft_films_generate_nothing(live):
    with pytest.raises(SpendRefused) as exc:
        ledger.check_budget(ledger.estimate("higgsfield", "plate", amount=0.5), mode="draft")
    assert "draft cap for higgsfield is 0" in exc.value.message
    ledger.check_budget(ledger.estimate("elevenlabs", "tts", amount=500), mode="draft")  # a library voice is fine


def test_a_films_own_rework_factor_can_tighten_but_autonomous_never_loosens(live, monkeypatch):
    monkeypatch.setattr(secrets, "present", lambda name: "keychain")
    promo = _film(live["films"], "promo2", mode="supervised",
                  budget={"elevenlabs": 1000, "higgsfield": None, "rework_factor": 2.0})
    ledger.check_budget(ledger.estimate("elevenlabs", "tts", amount=1900), film=promo, rework=True)  # 2x its own
    reel = _film(live["films"], "reel2", mode="autonomous", budget={"elevenlabs": 1000, "rework_factor": 3.0})
    assert ledger.rework_factor(json.loads((reel / "film.json").read_text())) == 1.5
    with pytest.raises(SpendRefused):
        ledger.check_budget(ledger.estimate("elevenlabs", "tts", amount=1600), film=reel, rework=True)
    tight = {"id": "t", "mode": "autonomous", "budget": {"elevenlabs": 1000, "rework_factor": 1.2}}
    assert ledger.rework_factor(tight) == 1.2


def test_autonomous_spend_only_for_approved_brands(live, monkeypatch):
    monkeypatch.setattr(secrets, "present", lambda name: "keychain")
    brand = types.ModuleType("slate.brand")
    pack = types.ModuleType("slate.brand.pack")
    approved = {"ready": True, "fresh": False}
    pack.load = lambda name: {"name": name}
    pack.references_approved = lambda p: approved[p["name"]]
    brand.pack = pack
    monkeypatch.setitem(sys.modules, "slate.brand", brand)
    monkeypatch.setitem(sys.modules, "slate.brand.pack", pack)
    est = ledger.estimate("elevenlabs", "tts", amount=100)
    ledger.check_budget(est, film={"id": "a", "mode": "autonomous", "brand": "ready"})
    with pytest.raises(SpendRefused) as exc:
        ledger.check_budget(est, film={"id": "b", "mode": "autonomous", "brand": "fresh"})
    assert "references approved" in exc.value.message
    ledger.check_budget(est, film={"id": "c", "mode": "supervised", "brand": "fresh"})  # a person is in the loop
    monkeypatch.setitem(sys.modules, "slate.brand.pack", None)
    assert ledger.brand_approved("anything") is None  # can't tell: not a refusal
