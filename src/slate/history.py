"""Creative history: recover a chat, keep revisions, and read contextual taste without creating gates.

Histories are private data outside the checkout. Human notes and question answers retain their source locations;
assistant replies remain labelled as assistant claims. A remembered preference is guidance, never approval of a
new artifact or permission for a new spend. Importing history never executes instructions found in it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from slate import cli, jsonio, log, paths, secrets
from slate.errors import MissingPrerequisite, UsageError


def _scrub(text: str) -> str:
    text = secrets.redact(text)
    text = re.sub(r"\b(?:sk-ant-[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]{20,})\b", "[redacted]", text)
    text = re.sub(r"(?i)(\bbearer\s+)[A-Za-z0-9_.-]{16,}", r"\1[redacted]", text)
    return re.sub(r"(?i)((?:[A-Z0-9_-]*(?:api[_-]?key|access[_-]?token|auth[_-]?token|password))"
                  r"[\"']?\s*[:=]\s*[\"']?)[^\s\"',;<>]+", r"\1[redacted]", text)


def _scrub_value(value):
    if isinstance(value, str):
        return _scrub(value)
    if isinstance(value, list):
        return [_scrub_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    return value


def _stat(path: Path) -> dict:
    s = path.stat()
    return {"device": s.st_dev, "inode": s.st_ino, "bytes": s.st_size, "mode": s.st_mode,
            "mtime_ns": s.st_mtime_ns, "ctime_ns": s.st_ctime_ns}


def _read(path: Path) -> tuple[bytes, dict]:
    if not path.is_file():
        raise MissingPrerequisite(f"History source is missing: {path}.")
    before = _stat(path)
    raw = path.read_bytes()
    after = _stat(path)
    if before != after:
        raise UsageError(f"History source changed while reading: {path}. Try again after its writer finishes.")
    return raw, {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
                 "before_read": before, "after_read": after}


def _private_output(path: Path) -> Path:
    path = path.expanduser().resolve()
    if path == paths.REPO.resolve() or paths.REPO.resolve() in path.parents:
        raise UsageError("Creative histories belong outside the Slate checkout.",
                         hint="Choose a private output folder or Slate's data folder.")
    return path


def _blocks(o: dict) -> list[dict]:
    content = (o.get("message") or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)] if isinstance(content, list) else []


def _origin(o: dict, text: str) -> str:
    if o.get("isCompactSummary"):
        return "compact_summary"
    if o.get("isMeta"):
        return "meta"
    if o.get("type") == "assistant":
        return "assistant_claim"
    if text.startswith(("<task-notification>", "<system-reminder>", "<local-command-stdout>",
                        "[Request interrupted by user]", "[Request interrupted by user for tool use]")):
        return "notification"
    if text.startswith("<bash-input>"):
        return "terminal_record"
    if text.startswith("<command-name>"):
        return "human_command"
    return "human_note"


def recover(metadata: list[Path], project: Path, out: Path) -> dict:
    """Read every transcript linked by the selected Desktop records, including rewinds and answered questions.

    Additional metadata records are explicit branches. Copied UUIDs are counted once, with every occurrence kept;
    conflicting text is refused rather than choosing one silently. All records are parsed, not just recent turns.
    """
    out = _private_output(Path(out))
    if out.exists():
        raise UsageError(f"History output already exists: {out}.", hint="Use a new version folder.")
    branches, bindings, wanted = [], [], {}
    for index, source in enumerate(metadata):
        raw, bind = _read(Path(source).expanduser().resolve())
        bindings.append(bind)
        try:
            meta = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise UsageError(f"Invalid Desktop metadata: {source}.") from exc
        if not isinstance(meta, dict) or not isinstance(meta.get("priorCliSessionIds", []), list):
            raise UsageError(f"Invalid Desktop metadata: {source}.")
        ids = [*(meta.get("priorCliSessionIds") or []), meta.get("cliSessionId")]
        if not ids or any(not isinstance(s, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", s) for s in ids):
            raise UsageError(f"Desktop metadata has invalid transcript IDs: {source}.")
        ids = list(dict.fromkeys(ids))
        bid = f"branch{index + 1}"
        branches.append({"id": bid, "title": meta.get("title"), "desktop_session": meta.get("sessionId"),
                         "parent": meta.get("forkedFromSessionId"), "sessions": ids,
                         "rewinds": meta.get("rewindEdges") or [], "source": bind["path"]})
        for sid in ids:
            wanted.setdefault(sid, []).append(bid)
    records, asks, responses, inventories = {}, {}, [], []
    for sid, branch_ids in wanted.items():
        source = Path(project).expanduser().resolve() / f"{sid}.jsonl"
        raw, bind = _read(source)
        bindings.append(bind)
        counts = {}
        for lineno, line in enumerate(raw.splitlines(), 1):
            try:
                o = json.loads(line)
            except (ValueError, UnicodeError) as exc:
                raise UsageError(f"Invalid transcript record: {source}:{lineno}.") from exc
            if not isinstance(o, dict):
                raise UsageError(f"Transcript record must be an object: {source}:{lineno}.")
            kind = str(o.get("type"))
            counts[kind] = counts.get(kind, 0) + 1
            where = {"path": str(source), "line": lineno, "session": sid, "branches": branch_ids}
            blocks = _blocks(o)
            for b in blocks:
                if kind == "assistant" and b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion":
                    ask = {"input": _scrub_value(b.get("input") or {}), "source": where, "at": o.get("timestamp")}
                    old = asks.setdefault(b.get("id"), ask)
                    if old["input"] != ask["input"]:
                        raise UsageError(f"Conflicting question request {b.get('id')} in the linked history.")
                elif kind == "user" and b.get("type") == "tool_result":
                    responses.append({"tool_use_id": b.get("tool_use_id"), "content": b.get("content"),
                                      "source": where, "at": o.get("timestamp"), "uuid": o.get("uuid")})
            texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
            if kind not in ("user", "assistant") or not texts:
                continue
            original = "\n\n".join(texts)
            text = _scrub(original)
            uid = o.get("uuid") or f"{sid}:line{lineno}"
            item = {"uuid": uid, "parent_uuid": o.get("parentUuid"), "at": o.get("timestamp"), "role": kind,
                    "origin": _origin(o, original), "sidechain": bool(o.get("isSidechain")),
                    "text": text, "text_sha256": hashlib.sha256(original.encode()).hexdigest(),
                    "redacted": text != original, "sources": [where]}
            if uid in records:
                prior = records[uid]
                if prior["text_sha256"] != item["text_sha256"] or prior["role"] != item["role"]:
                    raise UsageError(f"Conflicting message UUID {uid} in the linked history.")
                prior["sources"].append(where)
            else:
                records[uid] = item
        inventories.append({"session": sid, "branches": branch_ids, "rows": sum(counts.values()),
                            "record_types": counts, "sha256": bind["sha256"]})
    answers = {}
    for response in responses:
        tid = response["tool_use_id"]
        if tid not in asks:
            continue
        content = response["content"]
        if isinstance(content, list):
            text = "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
        else:
            text = str(content or "")
        if not text.startswith(("Your questions have been answered:", "The user answered:")):
            continue
        item = {"uuid": f"answer:{tid}", "tool_use_id": tid, "at": response["at"], "role": "user",
                "origin": "human_answer", "text": _scrub(text), "question": asks[tid],
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "sources": [response["source"]]}
        if tid in answers:
            if answers[tid]["text_sha256"] != item["text_sha256"]:
                raise UsageError(f"Conflicting answers to question {tid} in the linked history.")
            answers[tid]["sources"].append(response["source"])
        else:
            answers[tid] = item
    messages = sorted(records.values(), key=lambda m: (m["at"] or "", m["uuid"]))
    feedback = [m for m in messages if m["origin"] in ("human_note", "human_command", "terminal_record")
                and not m["sidechain"]] + list(answers.values())
    feedback.sort(key=lambda m: (m["at"] or "", m["uuid"]))
    for n, item in enumerate(feedback, 1):
        item["id"] = f"feedback{n:04d}"
    for bind in bindings:
        if _stat(Path(bind["path"])) != bind["after_read"]:
            raise UsageError(f"History source changed during recovery: {bind['path']}.")
    coverage = {"branches": branches, "files": len(bindings), "transcript_files": len(wanted),
                "all_rows_read": sum(x["rows"] for x in inventories), "messages": len(messages),
                "feedback_records": len(feedback), "answered_rounds": len(answers),
                "question_prompts": sum(len(a["question"]["input"].get("questions") or []) for a in answers.values()),
                "unanswered_question_ids": sorted(str(t) for t in asks if t not in answers),
                "duplicate_occurrences": sum(len(m["sources"]) - 1 for m in messages), "sources_unchanged": True,
                "missing_linked_files": [], "approval_inferred": False,
                "limits": ["Assistant claims are not human preferences or proof of a successful render.",
                           "Branches and rewinds are preserved; copied messages are not extra votes.",
                           "Question answers retain the question and options that framed the choice.",
                           "Thinking blocks, image pixels and unrelated tool results are not exported."]}
    out.mkdir(parents=True, mode=0o700)
    for name, data in (("sources", {"bindings": bindings, "inventories": inventories}), ("coverage", coverage),
                       ("messages", messages), ("feedback", feedback)):
        jsonio.write_atomic(out / f"{name}.json", data)
    return {"out": str(out), **coverage}


def pointer_path() -> Path:
    return paths.data() / "creative-history" / "current.json"


def use(source: Path) -> dict:
    """Make a private, curated history available to planning. This grants no creative or spending approval."""
    source = _private_output(Path(source))
    data = jsonio.read(source)
    if not isinstance(data, dict) or not isinstance(data.get("patterns"), list):
        raise UsageError("A curated history needs a patterns list with guidance and source evidence.")
    for p in data["patterns"]:
        if not isinstance(p, dict) or not p.get("guidance") or not p.get("evidence"):
            raise UsageError("Each history pattern needs guidance and evidence; impressions without sources stay notes.")
    from slate.state import _util  # noqa: PLC0415

    target = pointer_path()
    with _util.state_lock(target.parent):
        previous = jsonio.read(target, default={}) or {}
        value = {"source": str(source), "sha256": jsonio.sha256(source), "at": _util.now(),
                 "previous": previous.get("source") if isinstance(previous, dict) else None, "approval_inferred": False}
        jsonio.write_atomic(target, value)
    return value


def context(*, topic: str | None = None) -> dict:
    """Read remembered guidance without turning it into a gate or hiding counterexamples."""
    try:
        pointer = jsonio.read(pointer_path(), default={}) or {}
    except (OSError, ValueError, UnicodeError, UsageError) as exc:
        return {"source": None, "patterns": [], "note": f"Creative history could not be read: {exc}. Continue with the brief."}
    if not isinstance(pointer, dict):
        return {"source": None, "patterns": [], "note": "Creative history pointer is invalid. Continue with the brief."}
    if not pointer.get("source"):
        return {"source": None, "patterns": [], "note": "No creative history selected. Continue with the brief."}
    if not isinstance(pointer["source"], str):
        return {"source": None, "patterns": [], "note": "Creative history pointer is invalid. Continue with the brief."}
    source = Path(pointer["source"])
    if not source.is_file():
        return {"source": str(source), "patterns": [], "note": "Selected history is unavailable; it is not a gate."}
    try:
        raw, binding = _read(source)
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get("patterns"), list):
            raise ValueError("expected a patterns list")
        if any(not isinstance(p, dict) or not isinstance(p.get("guidance"), str) or not p.get("evidence")
               for p in data["patterns"]):
            raise ValueError("expected sourced guidance for every pattern")
        digest = binding["sha256"]
    except (OSError, ValueError, UnicodeError, UsageError, MissingPrerequisite) as exc:
        return {"source": str(source), "patterns": [], "note": f"Creative history could not be read: {exc}. It is not a gate."}
    patterns = [p for p in data.get("patterns") or [] if topic is None or topic in (p.get("topics") or [])]
    return {"source": str(source), "sha256": digest, "patterns": patterns,
            "changed_since_selected": digest != pointer.get("sha256"),
            "note": "Contextual guidance, not universal rules. The current brief and newer feedback take priority."}


_PLANNING_FIELDS = ("id", "title", "topics", "guidance", "scope", "counterexamples", "interpretation",
                    "approval_inferred")
_SOURCE_FIELDS = ("id", "feedback_id", "alias", "at", "source", "original_path", "snapshot_path", "source_line",
                  "source_uuid", "parent_uuid", "timestamp_utc", "session_id", "entrypoint", "turn_position",
                  "role", "authorship", "text_sha256", "original_human_message_verified",
                  "human_authorship_confirmed_by_current_user", "confirmation_authority")
_DETAIL_FIELDS = ("pattern", "guidance", "scope", "counterexamples", "evidence", "interpretation")


def _evidence_refs(evidence, pointer: str) -> list[dict]:
    """Keep deterministic source locations; full source turns remain available through detail retrieval."""
    values = evidence if isinstance(evidence, list) else [evidence]
    refs = []
    for index, value in enumerate(values):
        ref = {"pointer": f"{pointer}/{index}" if isinstance(evidence, list) else pointer}
        if isinstance(value, dict):
            ref.update({key: value[key] for key in _SOURCE_FIELDS if key in value})
            ref["reference_only_fields"] = [key for key in value if key not in _SOURCE_FIELDS]
        else:
            ref["reference_only"] = True
        refs.append(ref)
    return refs


def context_page(*, topic: str | None = None, view: str = "planning", offset: int = 0, limit: int = 50,
                 pattern_index: int | None = None, field: str = "pattern", text_offset: int = 0,
                 text_chars: int = 16_000, expect_sha256: str | None = None) -> dict:
    """Project exact planning fields or read exact JSON fragments without rewriting the selected history.

    Pattern indexes refer to the original selected list; page offsets refer to the topic-filtered list. Detail
    fragments rejoin as one JSON value. A supplied source hash binds related requests to the same source bytes.
    This checks a requested history read, not a creative approval or a production gate.
    """
    if view not in ("planning", "index", "detail"):
        raise UsageError("History page view must be planning, index or detail.")
    if offset < 0 or not 1 <= limit <= 100 or text_offset < 0 or not 1 <= text_chars <= 100_000:
        raise UsageError("History offsets must be nonnegative, limit 1–100, and text-chars 1–100000.")
    if expect_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", expect_sha256):
        raise UsageError("expect-sha256 must be the selected source's lowercase SHA-256 hash.")
    full = context()
    if expect_sha256 is not None and full.get("sha256") != expect_sha256:
        raise UsageError("Selected history changed or is unavailable; these pages cannot be combined.",
                         hint="Read the first page again and use its sha256 on the following requests.")
    patterns = full["patterns"]
    matching = [(index, pattern) for index, pattern in enumerate(patterns)
                if topic is None or topic in (pattern.get("topics") or [])]
    result = {key: value for key, value in full.items() if key != "patterns"}
    result.update({"view": view, "total_patterns": len(patterns), "matching_patterns": len(matching),
                   "approval_inferred": False,
                   "source_ref": {"path": full.get("source"), "sha256": full.get("sha256")},
                   "retrieval_note": "Planning fields are exact source values. Evidence references point into the "
                                     "unchanged selected source; detail returns its full JSON value in explicit "
                                     "fragments. Use expect-sha256 when combining pages. Existing secret redaction "
                                     "still applies to command output."})
    if view == "detail":
        if pattern_index is None or not 0 <= pattern_index < len(patterns):
            raise UsageError("History detail needs a valid --pattern-index from a planning or index page.")
        if pattern_index not in {index for index, _ in matching}:
            raise UsageError("That history pattern does not match the requested topic.")
        if field not in _DETAIL_FIELDS:
            raise UsageError(f"History detail field must be one of: {', '.join(_DETAIL_FIELDS)}.")
        pattern = patterns[pattern_index]
        if field != "pattern" and field not in pattern:
            raise UsageError(f"History pattern {pattern_index} has no {field} field.")
        value = pattern if field == "pattern" else pattern[field]
        original_json = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        # Redact before fragmenting so a known key cannot escape protection by crossing a page boundary.
        serialized = secrets.redact(original_json)
        if text_offset > len(serialized):
            raise UsageError("History text-offset is past the end of this JSON value.")
        end = min(len(serialized), text_offset + text_chars)
        pointer = f"/patterns/{pattern_index}" + (f"/{field}" if field != "pattern" else "")
        result["detail"] = {"pattern_index": pattern_index, "field": field, "pointer": pointer,
                            "encoding": "json", "redacted": serialized != original_json,
                            "offset": text_offset, "returned_chars": end - text_offset,
                            "total_chars": len(serialized), "text": serialized[text_offset:end],
                            "next_offset": end if end < len(serialized) else None, "complete": end == len(serialized)}
        return result
    if pattern_index is not None or field != "pattern" or text_offset != 0:
        raise UsageError("pattern-index, field and text-offset are only used with --view=detail.")
    if offset > len(matching):
        raise UsageError("History offset is past the end of the matching patterns.")
    selected = matching[offset:offset + limit]
    rows = []
    for index, pattern in selected:
        pointer = f"/patterns/{index}"
        row = {"pattern_index": index, "source_ref": {"pointer": pointer},
               "detail_args": ["--view=detail", f"--pattern-index={index}",
                               f"--expect-sha256={full['sha256']}"]}
        if view == "planning":
            row["data"] = {key: pattern[key] for key in _PLANNING_FIELDS if key in pattern}
            row["evidence_refs"] = _evidence_refs(pattern["evidence"], f"{pointer}/evidence")
            row["reference_only_fields"] = [key for key in pattern if key not in _PLANNING_FIELDS]
        else:
            row["fields_deferred"] = list(pattern)
        rows.append(row)
    end = offset + len(rows)
    result["patterns"] = rows
    result["paging"] = {"offset": offset, "requested_limit": limit, "returned": len(rows),
                        "next_offset": end if end < len(matching) else None}
    if view == "index":
        result["note"] += (" This is an index page: guidance, scope, counterexamples and evidence are deferred, "
                           "not summarized or discarded. Read each pattern with its detail_args.")
    return result


def record(film_dir: Path, *, notes: str, by: str, before: str | None = None, after: str | None = None,
           outcome: str = "requested", revision: str | None = None) -> dict:
    """Append a draft or approved-work revision with its exact notes and available artifact bindings."""
    from slate.state import _util  # noqa: PLC0415

    film_dir = Path(film_dir)
    if not notes.strip() or not by.strip():
        raise UsageError("History notes need the person's wording and their name.")
    if outcome not in ("requested", "draft", "measured", "preferred", "rejected"):
        raise UsageError("History outcome must be requested, draft, measured, preferred or rejected.")
    event = {"at": _util.now(), "notes": _scrub(notes), "by": by, "outcome": outcome,
             "revision": revision, "approval_inferred": False}
    for key, rel in (("before", before), ("after", after)):
        if rel:
            rel = _util.film_rel(film_dir, rel)
            path = film_dir / rel
            with _util.Hasher(film_dir) as hasher:
                event[key] = {"path": rel, "exists": path.exists(),
                              "sha256": hasher.any(path)[0] if path.exists() else None}
    with _util.state_lock(film_dir):
        target = film_dir / "creative-history.jsonl"
        with target.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    return event


def cmd_recover(args):
    if cli.dry_run(args):
        log.result({"would": "recover linked transcripts", "out": str(_private_output(Path(args.out)))},
                   "Would recover linked transcripts into a new private folder.")
        return 0
    result = recover([Path(p) for p in args.metadata], Path(args.project), Path(args.out))
    log.result(result, f"Recovered {result['feedback_records']} feedback records from {result['transcript_files']} transcripts.")
    return 0


def cmd_context(args):
    if args.view == "full":
        if (args.offset != 0 or args.limit != 50 or args.pattern_index is not None or args.field != "pattern"
                or args.text_offset != 0 or args.text_chars != 16_000 or args.expect_sha256 is not None):
            raise UsageError("Paging options require --view=planning, index or detail; full keeps the original output.")
        result = context(topic=args.topic)
        text = "\n\n".join(p["guidance"] for p in result["patterns"]) or result["note"]
    else:
        result = context_page(topic=args.topic, view=args.view, offset=args.offset, limit=args.limit,
                              pattern_index=args.pattern_index, field=args.field, text_offset=args.text_offset,
                              text_chars=args.text_chars, expect_sha256=args.expect_sha256)
        if args.view == "detail":
            text = result["detail"]["text"]
        else:
            text = "\n\n".join(p.get("data", {}).get("guidance", "") for p in result["patterns"]) or result["note"]
    log.result(result, text)
    return 0


def cmd_use(args):
    if cli.dry_run(args):
        log.result({"would": "select planning history", "source": str(_private_output(Path(args.source)))},
                   "Would select this private planning history.")
        return 0
    result = use(Path(args.source))
    log.result(result, f"Planning history: {result['source']}")
    return 0


def cmd_note(args):
    if cli.dry_run(args):
        log.result({"would": "record creative feedback"}, "Would record this creative feedback.")
        return 0
    result = record(cli.film(args), notes=args.notes, by=args.by, before=args.before,
                    after=args.after, outcome=args.outcome)
    log.result(result, "Creative feedback recorded with its artifact references.")
    return 0


def register(sub):
    group = cli.add_group(sub, "history", "recover creative iterations and read contextual taste")
    p = cli.add_command(group, "recover", cmd_recover, "recover every linked transcript and answered question")
    p.add_argument("--metadata", action="append", required=True, metavar="FILE", help="Desktop session record; repeat for explicit branches")
    p.add_argument("--project", required=True, metavar="DIR", help="folder containing its linked transcript files")
    p.add_argument("--out", required=True, metavar="DIR", help="new private output folder")
    p = cli.add_command(group, "context", cmd_context, "read sourced preferences and counterexamples")
    p.add_argument("--topic", help="filter by story, look, motion, voice, sound or process")
    p.add_argument("--view", choices=("full", "planning", "index", "detail"), default="full",
                   help="full preserves the original output; planning pages exact guidance with evidence references")
    p.add_argument("--offset", type=int, default=0, help="offset in the topic-filtered pattern list")
    p.add_argument("--limit", type=int, default=50, help="number of patterns in a planning or index page (1–100)")
    p.add_argument("--pattern-index", type=int, help="original pattern index for a detail request")
    p.add_argument("--field", choices=_DETAIL_FIELDS, default="pattern", help="original JSON value to read in detail")
    p.add_argument("--text-offset", type=int, default=0, help="character offset in the detail value's JSON text")
    p.add_argument("--text-chars", type=int, default=16_000, help="maximum JSON characters in a detail fragment")
    p.add_argument("--expect-sha256", help="refuse combining pages if the selected source bytes changed")
    p = cli.add_command(group, "use", cmd_use, "select a private history for planning; grants no approval")
    p.add_argument("--source", required=True, metavar="FILE")
    p = cli.add_command(group, "note", cmd_note, "record feedback on a draft or a revision")
    p.add_argument("--notes", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--before", help="film-relative version or revision snapshot")
    p.add_argument("--after", help="film-relative revised version")
    p.add_argument("--outcome", default="requested", choices=("requested", "draft", "measured", "preferred", "rejected"))
