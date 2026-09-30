"""Every function docs/ARCHITECTURE.md §12 assigns to slate.jobs exists with that signature (more is fine)."""

from __future__ import annotations

import inspect

from slate.jobs import client, disk, locks

EMPTY = inspect.Parameter.empty


def _check(fn, positional: list[str], keyword: dict[str, object]) -> None:
    params = inspect.signature(fn).parameters
    names = list(params)
    assert names[: len(positional)] == positional, (fn.__qualname__, names)
    for name, default in keyword.items():
        assert name in params, (fn.__qualname__, name)
        if default is not EMPTY:
            assert params[name].default == default, (fn.__qualname__, name, params[name].default)


def test_section_12_signatures():
    _check(client.submit, ["kind", "argv"], {"film": None, "resources": (), "priority": "supervised", "disk_gb": 0.0,
                                             "cwd": None})
    _check(client.status, ["job_id"], {})
    _check(client.wait, ["job_id", "timeout"], {})
    assert inspect.signature(client.wait).parameters["timeout"].default is None
    _check(client.cancel, ["job_id"], {})
    _check(locks.hold, ["resource"], {"timeout": None, "owner": None})
    _check(disk.reserve, ["path", "gb"], {})
    # the --detach convention other modules call
    _check(client.detach_current, ["kind", "resources"], {"film": None, "disk_gb": 0.0})


def test_the_resources_are_the_ones_section_12_names():
    from slate.jobs import model

    assert set(model.RESOURCES) == {"render", "ae", "elevenlabs_tts", "elevenlabs_music", "higgsfield", "whisper"}
