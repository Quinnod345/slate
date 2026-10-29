"""Selected creative history stays exact while planning and detail pages remain explicit."""

import hashlib
import json

import pytest

from slate import history, jsonio
from slate.cli import run_cli
from slate.errors import UsageError


def selected_history(slate_home, patterns):
    source = slate_home["tmp"] / "preferences.json"
    jsonio.write_atomic(source, {"patterns": patterns, "unprojected_metadata": "Retain this original field."})
    history.use(source)
    return source


def preference(index, *, topic="motion", evidence_text="Source wording."):
    return {"id": f"P{index}", "title": f"Preference {index}", "topics": [topic],
            "guidance": f"Keep the deliberate movement in example {index}.",
            "scope": "This example's landing.", "interpretation": "Contextual preference.",
            "evidence": [{"full_text": evidence_text, "source": {"path": "transcript.jsonl", "line": index + 1}}],
            "counterexamples": [{"quote": "Avoid the accidental shake.", "source": "transcript.jsonl:40"}],
            "approval_inferred": False}


def test_planning_preserves_exact_guidance_scope_counterexamples_and_source_references(slate_home):
    patterns = [preference(0), preference(1, topic="voice"), preference(2)]
    source = selected_history(slate_home, patterns)
    before = source.read_bytes()
    page = history.context_page(topic="motion", limit=1)
    assert page["total_patterns"] == 3 and page["matching_patterns"] == 2
    assert page["sha256"] == hashlib.sha256(before).hexdigest()
    row = page["patterns"][0]
    assert row["pattern_index"] == 0
    for field in ("guidance", "scope", "counterexamples", "interpretation", "approval_inferred"):
        assert row["data"][field] == patterns[0][field]
    assert row["evidence_refs"][0]["source"] == patterns[0]["evidence"][0]["source"]
    assert row["evidence_refs"][0]["pointer"] == "/patterns/0/evidence/0"
    assert row["source_ref"]["pointer"] == "/patterns/0"
    assert page["paging"]["next_offset"] == 1
    next_page = history.context_page(topic="motion", offset=1, limit=1, expect_sha256=page["sha256"])
    assert [p["pattern_index"] for p in next_page["patterns"]] == [2]
    assert next_page["paging"]["next_offset"] is None
    assert history.context()["patterns"] == patterns
    assert source.read_bytes() == before


def test_detail_chunks_round_trip_the_original_pattern_without_an_ai_summary(slate_home):
    pattern = preference(0, evidence_text=('Line\n"quoted" \\ Ω and a snowman ☃. ' * 4000))
    source = selected_history(slate_home, [pattern])
    before = source.read_bytes()
    chunks, offset, digest = [], 0, hashlib.sha256(before).hexdigest()
    while True:
        page = history.context_page(view="detail", pattern_index=0, text_offset=offset,
                                    text_chars=127, expect_sha256=digest)
        detail = page["detail"]
        assert detail["encoding"] == "json" and detail["offset"] == offset
        chunks.append(detail["text"])
        if detail["next_offset"] is None:
            assert detail["complete"] is True
            break
        assert detail["complete"] is False
        offset = detail["next_offset"]
    assert json.loads("".join(chunks)) == pattern
    assert source.read_bytes() == before


def test_page_binding_refuses_mixed_versions_without_reselecting_history(slate_home):
    source = selected_history(slate_home, [preference(0), preference(1)])
    pointer_before = history.pointer_path().read_bytes()
    page = history.context_page(limit=1)
    jsonio.write_atomic(source, {"patterns": [preference(0), preference(2)]})
    with pytest.raises(UsageError, match="changed"):
        history.context_page(offset=1, expect_sha256=page["sha256"])
    assert history.pointer_path().read_bytes() == pointer_before
    assert history.context()["changed_since_selected"] is True


def test_context_hash_and_patterns_are_bound_to_one_read(slate_home, monkeypatch):
    original = preference(0)
    source = selected_history(slate_home, [original])
    before = source.read_bytes()
    real_read, real_json_read = history._read, jsonio.read

    def mutate_after_bound_read(path):
        result = real_read(path)
        if path == source:
            jsonio.write_atomic(source, {"patterns": [preference(1)]})
        return result

    def mutate_after_legacy_json_read(path, **kwargs):
        result = real_json_read(path, **kwargs)
        if path == source:
            jsonio.write_atomic(source, {"patterns": [preference(1)]})
        return result

    monkeypatch.setattr(history, "_read", mutate_after_bound_read)
    monkeypatch.setattr(jsonio, "read", mutate_after_legacy_json_read)
    result = history.context()
    assert result["patterns"] == [original]
    assert result["sha256"] == hashlib.sha256(before).hexdigest()


def test_full_cli_retrieval_remains_available_and_page_options_are_explicit(slate_home):
    pattern = preference(0, evidence_text="An unshortened source turn.")
    selected_history(slate_home, [pattern])
    code, out, err = run_cli(["history", "context", "--json"])
    assert code == 0, err
    assert json.loads(out)["patterns"] == [pattern]
    code, out, err = run_cli(["history", "context", "--view=planning", "--limit=1", "--json"])
    assert code == 0, err
    assert json.loads(out)["view"] == "planning"
    code, out, _ = run_cli(["history", "context", "--view=detail", "--json"])
    assert code == 2
    assert "pattern-index" in json.loads(out)["message"]


@pytest.mark.parametrize("kwargs", [{"offset": -1}, {"limit": 0}, {"text_chars": 0},
                                   {"view": "detail", "pattern_index": 4},
                                   {"view": "detail", "pattern_index": 0, "field": "unknown"}])
def test_invalid_page_requests_refuse_clearly(slate_home, kwargs):
    selected_history(slate_home, [preference(0)])
    with pytest.raises(UsageError):
        history.context_page(**kwargs)


def test_known_secret_is_redacted_before_detail_fragment_boundaries(slate_home, monkeypatch):
    pattern = preference(0)
    pattern["guidance"] = "prefix neutral-test-key suffix"
    source = selected_history(slate_home, [pattern])
    before = source.read_bytes()
    monkeypatch.setattr(history.secrets, "_seen_values", {"neutral-test-key"})
    pieces, offset = [], 0
    while True:
        detail = history.context_page(view="detail", pattern_index=0, field="guidance",
                                      text_offset=offset, text_chars=2)["detail"]
        assert detail["redacted"] is True
        pieces.append(detail["text"])
        offset = detail["next_offset"]
        if offset is None:
            break
    assert json.loads("".join(pieces)) == "prefix ••• suffix"
    assert source.read_bytes() == before
