"""Every function docs/ARCHITECTURE.md §12 assigns to slate.state exists with that signature (more is fine)."""

from __future__ import annotations

import inspect

import slate.state as state
from slate.state import approvals, derive, film, lock, modes, park, pins, production, status

EMPTY = inspect.Parameter.empty


def _check(fn, positional: list[str], keyword: dict[str, object]) -> None:
    params = inspect.signature(fn).parameters
    names = list(params)
    assert names[: len(positional)] == positional, (fn.__qualname__, names)
    for name in positional:
        assert params[name].kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    for name, default in keyword.items():
        assert name in params, (fn.__qualname__, name)
        assert params[name].kind == inspect.Parameter.KEYWORD_ONLY, (fn.__qualname__, name)
        if default is not EMPTY:
            assert params[name].default == default, (fn.__qualname__, name, params[name].default)


def test_section_12_signatures():
    _check(film.create, ["film_id"], {"brand": EMPTY, "format": EMPTY, "mode": "supervised", "title": None,
                                      "parent": None, "derivation": None, "generation": "rarely", "films_dir": None})
    _check(film.load, ["film_dir"], {})
    _check(film.save, ["film_dir", "data"], {})
    _check(lock.record, ["film_dir", "rel_path"], {"producer": EMPTY, "inputs": EMPTY, "tool_version": None})
    _check(lock.is_stale, ["film_dir", "rel_path"], {})
    _check(lock.lock_asset, ["film_dir", "rel_path"], {"reason": EMPTY})
    _check(lock.is_locked, ["film_dir", "rel_path"], {})
    _check(pins.pin, ["film_dir", "key", "value"], {"repin": False})
    _check(pins.get, ["film_dir", "key"], {})
    _check(approvals.record, ["film_dir"], {"what": EMPTY, "path": EMPTY, "approved_by": EMPTY, "note": None})
    _check(approvals.is_approved, ["film_dir", "what", "path"], {})
    _check(production.add_direction, ["film_dir", "text"], {"source": EMPTY})
    _check(status.compute, ["film_dir"], {})
    _check(derive.create, ["parent_dir", "child_id"], {"format": None, "aspect": None, "language": None, "cut": None})
    _check(modes.settings, ["mode"], {})
    _check(modes.check, ["film_dir", "action"], {})
    _check(state.frozen_shot_ids, ["film_dir"], {})
    _check(lock.guard_regenerate, ["film_dir", "rel_path"], {"force": False, "approval_id": None})
    _check(park.park, ["film_dir"], {"reason": EMPTY, "evidence": (), "source": EMPTY})
