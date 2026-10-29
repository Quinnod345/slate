"""The MCP consumer receives usable history pages without changing film or permission boundaries."""

import asyncio
import json

import pytest

from slate import history, jsonio
from slate.mcp import policy, server, tools


def consume(args):
    from mcp import Client

    async def go():
        async with Client(server.build_server()) as client:
            result = await client.call_tool("run", {"command": "history context", **args})
            return result, json.loads(result.content[0].text)

    return asyncio.run(go())


def select(studio, *, count=28, large_guidance=False):
    source = studio["tmp"] / "selected-preferences.json"
    patterns = [{"id": f"P{i}", "topics": ["motion"], "guidance": "Keep the deliberate landing. " *
                 (10000 if large_guidance else 1), "scope": "This card's landing.",
                 "evidence": [{"source": {"path": "transcript.jsonl", "line": i + 1},
                               "full_text": ('Original source with "\\n" and Ω. ' * 1200)}],
                 "counterexamples": [{"quote": "Avoid accidental shake.", "source": "transcript.jsonl:80"}]}
                for i in range(count)]
    jsonio.write_atomic(source, {"patterns": patterns})
    history.use(source)
    return source, patterns


def test_no_film_history_is_global_but_explicit_bad_films_and_film_stages_still_refuse(studio, monkeypatch):
    source, _ = select(studio, count=1)
    monkeypatch.chdir(studio["tmp"])
    monkeypatch.setenv("SLATE_FILM", str(studio["tmp"] / "missing-film"))
    result, payload = consume({})
    assert result.is_error is False and payload["ok"] is True
    assert payload["result"]["source"] == str(source)
    assert "--film" not in payload["command"]
    bad = tools.run("history context", film=str(studio["tmp"] / "missing-film"))
    assert bad["ok"] is False
    assert tools.run("script lint")["ok"] is False
    assert policy.RUN["history context"].requires_film is False
    assert all(s.requires_film for name, s in policy.RUN.items() if name != "history context")
    assert all(s.requires_film for s in policy.JOBS.values())


def test_consumer_pages_fit_the_complete_envelope_and_cover_every_selected_pattern(studio, monkeypatch):
    source, patterns = select(studio)
    before = source.read_bytes()
    monkeypatch.setattr(tools, "MAX_TEXT", 5000)
    offset, seen, digest = 0, [], None
    while True:
        args = [f"--offset={offset}", "--limit=28"]
        if digest:
            args.append(f"--expect-sha256={digest}")
        result, payload = consume({"args": args})
        assert result.is_error is False and payload["ok"] is True, payload
        assert "truncated" not in payload and "result_keys" not in payload
        assert len(result.content[0].text) <= tools.MAX_TEXT
        page = payload["result"]
        assert page["total_patterns"] == len(patterns) and page["view"] == "planning"
        for row in page["patterns"]:
            assert row["data"]["guidance"] == patterns[row["pattern_index"]]["guidance"]
            assert row["data"]["scope"] == patterns[row["pattern_index"]]["scope"]
            assert row["data"]["counterexamples"] == patterns[row["pattern_index"]]["counterexamples"]
            seen.append(row["pattern_index"])
        digest = page["sha256"]
        offset = page["paging"]["next_offset"]
        if offset is None:
            break
    assert seen == list(range(28))
    assert source.read_bytes() == before


@pytest.mark.parametrize("large_field", ["guidance", "counterexamples", "source_reference"])
def test_oversized_planning_fields_are_deferred_and_exact_detail_survives_escaping(studio, monkeypatch, large_field):
    source, patterns = select(studio, count=1)
    # Each source value is indivisible in a planning page. Escaping grows the envelope beyond the detail count.
    oversized = '"quoted"\n\\ path Ω ☃ and astral 🜁. ' * 2500
    field = "pattern"
    if large_field == "guidance":
        patterns[0]["guidance"] = oversized
    elif large_field == "counterexamples":
        patterns[0]["counterexamples"][0]["quote"] = oversized
    else:
        patterns[0]["evidence"][0]["source"]["path"] = oversized
    jsonio.write_atomic(source, {"patterns": patterns})
    before = source.read_bytes()
    monkeypatch.setattr(tools, "MAX_TEXT", 5000)
    result, payload = consume({})
    page = payload["result"]
    assert result.is_error is False and "truncated" not in payload
    assert page["view"] == "index" and page["patterns"][0]["fields_deferred"]
    assert "detail" in page["note"].lower()
    assert page["paging"]["returned"] == 1 and page["paging"]["next_offset"] is None
    assert len(result.content[0].text) <= tools.MAX_TEXT
    chunks, offset = [], 0
    while True:
        args = ["--view=detail", "--pattern-index=0", f"--field={field}", f"--text-offset={offset}",
                "--text-chars=20000", f"--expect-sha256={page['sha256']}"]
        result, payload = consume({"args": args})
        assert result.is_error is False and "truncated" not in payload
        assert len(result.content[0].text) <= tools.MAX_TEXT
        detail = payload["result"]["detail"]
        assert detail["offset"] == offset
        chunks.append(detail["text"])
        offset = detail["next_offset"]
        if offset is None:
            break
    assert json.loads("".join(chunks)) == patterns[0]
    assert source.read_bytes() == before


@pytest.mark.parametrize("args", [["--view=full"], ["--out=/tmp/changed.json"],
                                 ["--expect-sha256=not-a-digest"], ["--limit=0"],
                                 ["--view=detail"], ["--live"]])
def test_history_paging_allowlist_does_not_expose_mutation_or_unbounded_full_output(studio, args):
    select(studio, count=1)
    result, payload = consume({"args": args})
    assert result.is_error is True and payload["ok"] is False


def test_detail_cursor_crossing_ten_million_characters_remains_valid(studio, monkeypatch):
    # A synthetic read isolates cursor validation without a large private fixture or a source rewrite.
    pattern = {"guidance": "x" * 10_000_010, "evidence": [{"source": "neutral-transcript:1"}]}
    monkeypatch.setattr(history, "context", lambda **kwargs: {
        "source": "neutral-selected-history.json", "sha256": "0" * 64, "patterns": [pattern],
        "changed_since_selected": False, "note": "Contextual guidance."})
    base = ["--view=detail", "--pattern-index=0", "--field=guidance", "--text-chars=10",
            "--expect-sha256=" + "0" * 64]
    first, first_payload = consume({"args": [*base, "--text-offset=9999995"]})
    assert first.is_error is False
    cursor = first_payload["result"]["detail"]["next_offset"]
    assert cursor == 10_000_005
    second, second_payload = consume({"args": [*base, f"--text-offset={cursor}"]})
    assert second.is_error is False, second_payload
    detail = second_payload["result"]["detail"]
    assert detail["offset"] == cursor and detail["next_offset"] is None
    assert detail["complete"] is True
    assert policy.check_args(policy.RUN["history context"], ["--offset=10000000", "--pattern-index=10000000"]) == [
        "--offset=10000000", "--pattern-index=10000000"]


@pytest.mark.parametrize("option", ["--offset", "--pattern-index", "--text-offset"])
def test_history_cursor_values_are_bounded_in_split_argument_form(option):
    from slate.errors import UsageError

    with pytest.raises(UsageError):
        policy.check_args(policy.RUN["history context"], [option, "1" * 401])


@pytest.mark.parametrize("joined", [False, True])
def test_existing_ids_option_cannot_bypass_argument_length_in_split_form(joined):
    from slate.errors import UsageError

    only = ",".join(f"S{i:04d}" for i in range(100))
    assert len(only) > policy.MAX_ARG_LEN
    args = [f"--only={only}"] if joined else ["--only", only]
    with pytest.raises(UsageError):
        policy.check_args(policy.RUN["voice estimate"], args)


def test_existing_ids_split_values_keep_type_and_line_break_checks():
    from slate.errors import UsageError

    stage = policy.RUN["voice estimate"]
    with pytest.raises(UsageError, match="string value"):
        policy.check_args(stage, ["--only", 7])
    with pytest.raises(UsageError, match="line break"):
        policy.check_args(stage, ["--only", "S001\nS002"])
    assert policy.check_args(stage, ["--only", "S001,S002"]) == ["--only=S001,S002"]
