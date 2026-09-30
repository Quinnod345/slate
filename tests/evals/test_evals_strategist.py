"""The pending Oneiro case: its precondition, and the strategist dry run it runs once that holds."""

import datetime as dt
import json
from pathlib import Path

from slate import paths
from slate.evals import commands, preconditions, strategist, suite

EVALS = paths.REPO / "evals"

AGENTS_JS = """
const text = (v, max = 300) => String(v ?? '').replace(/\\s+/g, ' ').trim().slice(0, max);
export function createAgents({ pool, llm }) {
  async function strategize(row, max, now) {
    const s = row.state || {}, want = s.want || {};
    const r = await llm.messages.create({ provider: p.provider, model: p.model, max_tokens: 1400, temperature: 0.4,
      system: `You are the strategist. Agents can browse (Aside), CAPABILITIES, and run code. Use \\`JSON\\` only: {"thought":"…","moves":[{"task":"…","why":"…","stream":"Content"}]}`,
      messages: [{ role: 'user', content: `Now: ${new Date(now).toISOString()}` }] });
  }
}
"""

SNAPSHOT = {
    "chain_id": 27, "seed": "Grow the app", "status": "awaiting_evidence",
    "want": {"description": "Make the app pay for itself", "doneWhen": "a month in the black"},
    "evidence": [
        {"id": "person-1", "source": "stated by Quinn", "observation": "Keep ads honest."},
        {"id": "e2", "source": "observed in the store", "observation": "Two reviews this week."},
    ],
    "continuity": {"board": {"streams": [{"name": "Content"}, {"name": "Distribution"}, {"name": "Content"}]},
                   "lastThought": "Content is the lever.",
                   "lastRefused": [{"task": "Post the old carousel again", "why": "retired"}],
                   "schedule": [{"at": "10-08", "task": "founder post"}]},
}


def _oneiro(home: Path, *, capability: str) -> None:
    agents = home / "oneiro" / "oca-cognitive" / "reasoning" / "agents.js"
    agents.parent.mkdir(parents=True)
    agents.write_text(AGENTS_JS.replace("CAPABILITIES", capability))
    snap = home / "oneiro" / "runtime" / "workspace" / "pursuit-work" / "27" / "pursuit.json"
    snap.parent.mkdir(parents=True)
    snap.write_text(json.dumps(SNAPSHOT))


def test_system_prompt_extraction():
    prompt, interpolates = strategist.extract_system_prompt(AGENTS_JS)
    assert prompt.startswith("You are the strategist.") and "`JSON`" in prompt and not interpolates
    templated = AGENTS_JS.replace("CAPABILITIES", "${extra}")
    assert strategist.extract_system_prompt(templated)[1] is True
    assert strategist.extract_system_prompt("function other() {}") == (None, False)


def test_user_message_mirrors_strategize():
    compose = suite.load_configs(EVALS)["harnesses"]["oneiro-strategist"]["compose"]
    now = dt.datetime(2026, 10, 1, 9, 0, tzinfo=dt.timezone.utc)
    msg = strategist.compose_user_message(SNAPSHOT, now=now, compose=compose)
    lines = msg.splitlines()
    assert lines[0] == "Now: 2026-10-01T09:00:00.000Z" and lines[1] == "N = 3"
    assert lines[2] == "Pursuit #27: Make the app pay for itself" and lines[3] == "Done when: a month in the black"
    assert "- Keep ads honest." in msg and "- Two reviews this week." in msg
    assert "Moves refused last cycle" in msg and "- Post the old carousel again → retired" in msg
    assert "Workstreams so far: Content, Distribution" in msg and "Last thought: Content is the lever." in msg
    assert "Recent agent work (newest first):\n- (none)" in msg
    quiet = strategist.compose_user_message({"chain_id": 1, "seed": "s"}, now=now)
    assert "Moves refused" not in quiet and "- (nothing recorded)" in quiet


def test_the_precondition_waits_for_the_self_want(tmp_path):
    case = next(c for c in suite.load_cases(EVALS) if c.id == "oneiro-strategist-slate-move")
    values = {"home": str(tmp_path)}
    assert preconditions.evaluate(case.precondition, values)["met"] is False  # nothing there yet
    _oneiro(tmp_path, capability="drive Mac apps, write files")
    pending = preconditions.evaluate(case.precondition, values)
    assert pending["met"] is False and "doesn't match" in pending["summary"]
    agents = tmp_path / "oneiro" / "oca-cognitive" / "reasoning" / "agents.js"
    agents.write_text(AGENTS_JS.replace("CAPABILITIES", "make short videos with Slate (the slate CLI)"))
    met = preconditions.evaluate(case.precondition, values)
    assert met["met"] is True and met["summary"] == "precondition met"
    assert met["info"][0]["ok"] is None and "not asked" in met["info"][0]["detail"], "services only when asked"


def test_http_preconditions_are_local_and_for_the_record_only():
    problems = preconditions.validate({"all": [{"check": "http_json", "url": "https://example.com/x"}]})
    assert any("info" in p for p in problems) and any("this Mac" in p for p in problems)
    assert preconditions.validate({"all": [{"check": "file_exists", "path": "x"}],
                                   "info": [{"check": "http_json", "url": "http://127.0.0.1:3333/oca/trace/64"}]}) == []


def test_once_met_the_strategist_prompt_is_built_from_oneiros_own_files(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _oneiro(tmp_path, capability="make short videos with Slate (the slate CLI)")
    configs = suite.load_configs(EVALS)
    case = next(c for c in suite.load_cases(EVALS, configs=configs) if c.id == "oneiro-strategist-slate-move")
    inv = commands.plan(case, "oneiro-strategist", configs, run_id="R", run_dir=tmp_path / "runs")
    assert inv.skip is None
    assert inv.prompt.startswith("You are the strategist. Agents can browse (Aside), make short videos with Slate")
    assert "\n\nUser: Now: " in inv.prompt and "Pursuit #27: Make the app pay for itself" in inv.prompt
    assert any("database" in n for n in inv.notes)


def test_until_then_the_case_is_skipped_as_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    configs = suite.load_configs(EVALS)
    case = next(c for c in suite.load_cases(EVALS, configs=configs) if c.id == "oneiro-strategist-slate-move")
    inv = commands.plan(case, "oneiro-strategist", configs, run_id="R", run_dir=tmp_path / "runs")
    assert inv.skip.startswith("pending")
