"""The CLI contract for gen, plates and capture: help everywhere, one JSON object with --json, the shared exit codes."""

from __future__ import annotations

import json

import pytest

from gencap_helpers import run_json
from slate.cli import run_cli

COMMANDS = ["gen", "gen estimate", "gen run", "gen takes", "gen judge", "gen pick", "gen cancel",
            "gen request-approval", "gen approve", "gen probe", "plates", "plates plan", "plates conform",
            "plates matte", "plates track", "capture", "capture ios", "capture web", "capture conform", "capture beatmap"]


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_has_help(command, slate_home):
    code, out, err = run_cli([*command.split(), "--help"])
    assert code == 0 and "usage: slate" in (out + err)


def test_registered_through_the_dispatcher(slate_home):
    from slate import cli

    cli.build_parser()
    assert "slate.gen.cli" not in cli.BROKEN and "slate.capture.cli" not in cli.BROKEN


def test_json_mode_prints_exactly_one_object(film):
    for argv in (["plates", "plan"], ["gen", "estimate", "--shot", "S02"], ["gen", "probe"]):
        code, out, err = run_cli(["--json", *argv, "--film", str(film)] if argv[1] != "probe" else ["--json", *argv])
        assert code == 0, err
        lines = [line for line in out.splitlines() if line.strip()]
        assert len(lines) == 1 and isinstance(json.loads(lines[0]), dict)


def test_exit_codes(make_film):
    film = make_film()
    code, _, _ = run_cli(["gen", "run", "--film", str(film)])  # --shot missing
    assert code == 2
    code, out, _ = run_json(["gen", "run", "--film", str(film), "--shot", "S03"])  # policy says no
    assert code == 1 and out["error"] == "refused"
    code, out, _ = run_json(["gen", "run", "--film", str(film), "--shot", "S99"])  # no such shot
    assert code == 2 and out["error"] == "usage"
    code, out, _ = run_json(["capture", "beatmap", "--film", str(film), "--name", "nothing"])
    assert code == 2
    auto = make_film("auto", mode="autonomous")
    code, out, _ = run_json(["gen", "run", "--film", str(auto), "--shot", "S02"])  # waits for a yes
    assert code == 4 and out["error"] == "spend_refused"


def test_films_resolve_by_id(film, monkeypatch):
    monkeypatch.chdir(film.parent)
    code, out, _ = run_json(["plates", "plan", "--film", film.name])
    assert code == 0 and out["film"] == film.name


def test_detach_without_the_queue_says_so(film, monkeypatch):
    from slate.gen import deps

    monkeypatch.setattr(deps, "jobs_client", lambda: None)
    code, out, _ = run_json(["gen", "run", "--film", str(film), "--shot", "S02", "--detach"])
    assert code == 3 and "queue" in out["message"]


def test_detach_hands_the_run_to_the_queue(film, monkeypatch):
    from slate.gen import deps

    submitted = {}

    class FakeJobs:
        @staticmethod
        def submit(kind, argv, **kw):
            submitted.update(kind=kind, argv=argv, **kw)
            return "job_123"

    monkeypatch.setattr(deps, "jobs_client", lambda: FakeJobs)
    code, out, _ = run_json(["gen", "run", "--film", str(film), "--shot", "S02", "--detach"])
    assert code == 0 and out["job"] == "job_123"
    assert submitted["kind"] == "generation" and submitted["resources"] == ("higgsfield",)
    argv = submitted["argv"]
    assert argv[argv.index("--shot") + 1] == "S02" and argv[argv.index("--film") + 1] == str(film)
    assert "--detach" not in submitted["argv"]


def test_the_cross_module_interfaces_exist_with_their_signatures():
    """docs/ARCHITECTURE.md §12: gen / capture, and the Higgsfield client."""
    import inspect

    from slate.capture import conform, ios, web
    from slate.gen import policy
    from slate.vendors import higgsfield

    def params(fn):
        return [n for n, p in inspect.signature(fn).parameters.items() if p.kind in (p.POSITIONAL_ONLY,
                                                                                    p.POSITIONAL_OR_KEYWORD)]

    assert params(policy.allowed) == ["film_json", "shot"]
    assert params(ios.capture) == ["film_dir", "spec"]
    assert params(web.capture) == ["film_dir", "spec"]
    assert params(conform.to_sequence) == ["src", "film_dir", "name"]
    client = higgsfield.Client
    assert params(client.probe) == ["self"]
    assert params(client.estimate) == ["self", "path", "body"]
    assert params(client.submit) == ["self", "path", "body"]
    assert params(client.wait) == ["self", "request_id"]
    assert params(client.cancel) == ["self", "request_id"]
    assert params(client.upload) == ["self", "path"]
