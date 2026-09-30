"""The finish commands as agents see them: help, --json, exit codes, the §12 interfaces, and the house rules (no brand
in the code, no frame size or rate typed into logic, every child process through slate.procs)."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

import filmkit
from slate.cli import run_cli

SRC = Path(__file__).resolve().parents[2] / "src" / "slate"
MINE = sorted((SRC / "finish").glob("*.py")) + [SRC / "notify.py"]


@pytest.mark.parametrize("argv", [["master"], ["deliver"], ["review"], ["review", "grade-brief"],
                                  ["review", "grade"], ["notes"], ["notes", "add"], ["notes", "list"],
                                  ["notes", "plan"], ["approved"]])
def test_help(argv):
    code, out, err = run_cli([*argv, "--help"])
    assert code == 0 and "usage: slate" in out


def test_notes_without_a_stage_prints_help(make_film):
    d = make_film("h", mode="draft", brand="open-brand", renders=False, mix=False)
    code, out, err = filmkit.cli(d, "notes")
    assert code == 2 and "add" in err


def test_no_film_is_usage(fin, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out, err = run_cli(["master"])
    assert code == 2 and "No film here" in err


def test_json_errors_carry_the_exit_code(make_film):
    d = make_film("j", mode="autonomous", format="finish-reel", renders=False, mix=False)
    code, out = filmkit.cli_json(d, "master", "--ae", "--go")
    assert code == 1 and out["error"] == "refused" and out["exit_code"] == 1 and "hint" in out
    code, out = filmkit.cli_json(d, "master")
    assert code == 3 and out["error"] == "missing_prerequisite"


def test_bad_timecode_is_usage(make_film):
    d = make_film("tc", mode="draft", brand="open-brand", renders=False, mix=False)
    code, out, err = filmkit.cli(d, "notes", "add", "x", "--at", "99:00:00:00")
    assert code == 2 and "outside the film" in err


def test_section_12_interfaces():
    from slate import notify
    from slate.finish import deliver, master, review

    assert list(inspect.signature(master.run).parameters)[:2] == ["film_dir", "method"]
    assert inspect.signature(master.run).parameters["method"].kind is inspect.Parameter.KEYWORD_ONLY
    assert list(inspect.signature(deliver.run).parameters)[0] == "film_dir"
    assert list(inspect.signature(review.packet).parameters)[0] == "film_dir"
    sig = inspect.signature(notify.imessage)
    assert list(sig.parameters)[:2] == ["text", "attachment"] and sig.parameters["attachment"].default is None
    assert list(inspect.signature(notify.push).parameters)[:2] == ["title", "body"]


def test_master_run_interface(make_film):
    from slate.finish import master

    d = make_film("mi", mode="draft", brand="open-brand")
    out = master.run(d, method="ffmpeg")
    assert out["status"] == "mastered" and out["passed"]


# Company, product, character and font names, the films' voice and kit ids and their brand colours, held only as
# SHA-256 prefixes so the guard itself keeps them out of the repo (the list in tests/brand/test_brand_nobrand.py, plus
# the films' other distinctive colours).
GUARDED = {
    "2ad60e2919a3ae8f", "e252b79c39aa92dd", "fd73d009d4b851d4", "16149626b0365e04", "ff9b5c834ecab632",
    "531074a5255eac9d", "129eaf4b1219beda", "db4ea2e2c2875a50", "61acc4cab1bfb5d3", "bdd6bed80a39febc",
    "f8d73da6e93bc010", "2e7dc20ed2ed1d97", "4715650ab9e0c9ba", "4b916c20ce35d6ba", "55dba21b14c7d258",
    "30c07346a4e77ab1", "a30a9e529370f662", "bad936c77484aada", "7fa259c033887a81",
    "fb2b95447a31b356", "212c6e34978bf31f", "1f2401c1733346fa", "7ba2337ec67b6b43", "4bc7eb0c4824f537",
    "394d55263b21668e", "14cae19d49b9701b", "c52a77f328a6c02c", "934cd28b6e3678a3",
}
MINE_ALL = MINE + [SRC / "schemas" / "manifest.schema.json", SRC / "schemas" / "review.schema.json",
                   *sorted(Path(__file__).parent.rglob("*.py")), *sorted((Path(__file__).parent / "fixtures").rglob("*"))]


def _guarded_hits(text: str) -> list[str]:
    import hashlib

    def h(s: str) -> str:
        return hashlib.sha256(s.encode()).hexdigest()[:16]

    words = re.findall(r"[a-z0-9]+", text.lower())
    return [f"word {i + 1}" for i, w in enumerate(words)
            if h(w) in GUARDED or (i + 1 < len(words) and h(f"{w} {words[i + 1]}") in GUARDED)]


@pytest.mark.parametrize("path", [p for p in MINE_ALL if p.is_file()], ids=lambda p: p.name)
def test_no_brand_in_the_code(path):
    assert not _guarded_hits(path.read_text(encoding="utf-8", errors="replace")), path


def test_the_brand_guard_catches_what_it_guards():
    assert _guarded_hits("made for " + "".join(chr(c) for c in (69, 79, 83)) + " clients")
    assert not _guarded_hits("the card lands a beat late")


def _code_only(text: str) -> str:
    """The source without comments and docstrings (numbers in prose are fine; numbers in logic aren't)."""
    import ast
    import io
    import tokenize

    tree = ast.parse(text)
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None),
                                                                               ast.Constant):
                doc = node.body[0]
                doc_lines.update(range(doc.lineno, doc.end_lineno + 1))
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT or tok.start[0] in doc_lines:
            continue
        out.append(tok.string)
    return " ".join(out)


@pytest.mark.parametrize("path", MINE, ids=lambda p: p.name)
def test_no_frame_size_or_rate_in_logic(path):
    code = _code_only(path.read_text())
    for literal in ("1920", "1080", "30000", "1001", "29.97", "23.976", "59.94"):
        assert literal not in code, (path.name, literal)


@pytest.mark.parametrize("path", MINE, ids=lambda p: p.name)
def test_children_only_through_procs(path):
    text = path.read_text()
    assert "subprocess" not in _code_only(text), path.name
    assert "os.system" not in text and "Popen" not in text
    assert "webbrowser" not in text and "open -a" not in text


def test_detach_queues_the_same_command(make_film, monkeypatch):
    from slate.finish import deps

    queued = []
    monkeypatch.setattr(deps, "detach", lambda kind, resources, *, film_dir, disk_gb=0.0:
                        queued.append((kind, tuple(resources), Path(film_dir).name)) or "job-fixture")
    d = make_film("det", mode="draft", brand="open-brand", renders=False, mix=False)
    code, out = filmkit.cli_json(d, "master", "--detach")
    assert code == 0 and out["job"] == "job-fixture" and queued == [("master", ("render",), "det")]
    code, out = filmkit.cli_json(d, "deliver", "--detach")
    assert queued[-1][:2] == ("deliver", ("render",))
    s = make_film("detae", mode="supervised", renders=False, mix=False)
    code, out, err = filmkit.cli(s, "master", "--ae", "--detach")  # no --go: refused before anything is queued
    assert code == 1 and len(queued) == 2
    code, out = filmkit.cli_json(s, "master", "--ae", "--go", "--detach")
    assert queued[-1] == ("master", ("ae",), "detae")


def test_an_earlier_packet_can_be_sent_again(make_film, fin):
    d = make_film("again", mode="draft", brand="open-brand")
    filmkit.cli(d, "review", "--no-send")
    code, out = filmkit.cli_json(d, "review", "--version", "1", "--to", "downloads")
    assert code == 0 and out["sent"]["folder"].endswith("again v1 review")
    code, out, err = filmkit.cli(d, "review", "--version", "4")
    assert code == 3


def test_finish_registers_only_its_own_commands():
    """variant is state's and formats is its own module's: finish adding either would make argparse refuse the
    duplicate and drop finish's later commands."""
    import argparse

    from slate.finish import cli as finish_cli

    sub = argparse.ArgumentParser(prog="slate").add_subparsers()
    finish_cli.register(sub)
    assert set(sub.choices) == {"master", "deliver", "review", "notes", "approved"}
    from slate import cli

    parser = cli.build_parser()
    choices = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices
    assert {"master", "deliver", "review", "notes", "approved", "variant", "formats"} <= set(choices)
    assert "slate.finish.cli" not in cli.BROKEN
