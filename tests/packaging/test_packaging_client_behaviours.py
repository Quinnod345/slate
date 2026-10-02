"""Five client behaviours Slate's packaging could lean on, each recorded with its hands-on check and its result.

tests/packaging/client_behaviours.json is the record. A behaviour counts as confirmed only with the date, the client's
version and what was seen; until then it's unconfirmed, with the exact check that settles it, and Slate must not rely
on it. The second half of this file proves Slate doesn't: the plugin names no path placeholder, the skills carry no
nested metadata, and the plugin's README doesn't send Codex or OpenClaw to the marketplace as if it worked.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from slate import skills_build as sb

HERE = Path(__file__).parent
REPO = HERE.parents[1]
RECORD = HERE / "client_behaviours.json"
FIVE = {"openclaw-claude-bundle-placeholders", "codex-claude-marketplace-sources", "openclaw-nested-metadata",
        "claude-validate-both-manifests", "openclaw-gateway-state-dir"}


def _record() -> dict:
    return json.loads(RECORD.read_text())


def _by_id() -> dict[str, dict]:
    return {b["id"]: b for b in _record()["behaviours"]}


def test_the_five_client_behaviours_are_recorded():
    data = _record()
    rows = data["behaviours"]
    assert {b["id"] for b in rows} == FIVE and len(rows) == len(FIVE)
    for b in rows:
        assert b["status"] in data["statuses"], b["id"]
        assert b["what"].strip() and b["check"].strip(), b["id"]
        assert isinstance(b["slate_relies_on_it"], bool), b["id"]
        if b["status"] == "unconfirmed":
            assert not b["slate_relies_on_it"], f"{b['id']} is unconfirmed, so Slate mustn't rely on it yet"
            assert b.get("how_slate_avoids_it", "").strip(), b["id"]
            assert b.get("what_is_left", "").strip(), f"{b['id']}: say exactly what's left to confirm it"
        else:
            # A result is a date, a client version and what was seen.
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", b["checked_on"]), b["id"]
            assert b["client_version"].strip() and b["evidence"].strip(), b["id"]
        if b["slate_relies_on_it"]:
            assert b["status"] == "confirmed", f"Slate relies on {b['id']}, which isn't confirmed"


def test_checks_never_touch_a_real_client_state_folder():
    """The hands-on checks run on scratch state folders, never the person's own configuration."""
    by = _by_id()
    assert "never the real one" in by["openclaw-claude-bundle-placeholders"]["check"]
    assert "never ~/.codex" in by["codex-claude-marketplace-sources"]["check"]


def test_slate_relies_on_no_unconfirmed_behaviour():
    by = _by_id()
    files = sb.plugin_files()
    # 1. No path placeholder anywhere in the plugin's MCP configs or manifests, so OpenClaw has nothing to expand.
    for rel in (".mcp.json", "mcp.json", "plugin.json", ".claude-plugin/plugin.json"):
        assert "${" not in files[rel][0].decode(), rel
    # 2. The plugin's README doesn't send Codex or OpenClaw to the marketplace as if it were confirmed.
    readme = files["README.md"][0].decode()
    if by["codex-claude-marketplace-sources"]["status"] != "confirmed":
        assert "isn't confirmed yet" in readme and "tests/packaging/client_behaviours.json" in readme
        assert "In Codex, add the same repository as a plugin marketplace" not in readme
    # 3. Slate's skills carry no metadata at all, let alone nested blocks.
    if by["openclaw-nested-metadata"]["status"] != "confirmed":
        for skill in sb.discover():
            assert "metadata" not in skill.meta, skill.name


def test_the_confirmed_behaviours_are_the_ones_the_plugin_leans_on():
    by = _by_id()
    assert by["claude-validate-both-manifests"]["status"] == "confirmed"
    assert {"plugin.json", ".claude-plugin/plugin.json"} <= set(sb.plugin_files())  # both manifests ship
    assert by["openclaw-gateway-state-dir"]["status"] == "confirmed"
    assert "~/.agents/skills" in sb.openclaw_entries([])["about"]


def test_two_marketplace_behaviours_are_confirmed_and_nested_metadata_waits_on_skills_ref():
    """L1 (2026-10-01) confirmed the bundle placeholders and the marketplace sources hands-on in scratch homes; nested
    metadata needs skills-ref installed and a claude.ai upload by hand."""
    by = _by_id()
    for bid in ("openclaw-claude-bundle-placeholders", "codex-claude-marketplace-sources"):
        assert by[bid]["status"] == "confirmed" and by[bid]["checked_on"] == "2026-10-01", bid
        assert not by[bid]["slate_relies_on_it"], f"{bid}: confirmed, and Slate still doesn't need it"
    assert "local agent turn" in by["openclaw-claude-bundle-placeholders"]["as_checked"]
    left = by["openclaw-nested-metadata"]
    assert left["status"] == "unconfirmed"
    assert "skills-ref" in left["what_is_left"] and "claude.ai" in left["what_is_left"]
