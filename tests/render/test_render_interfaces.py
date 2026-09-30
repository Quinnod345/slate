"""The section 12 interfaces render owes other modules, with exactly the contract's names and signatures."""

import inspect

import slate.render
from slate.render import registry, splice


def _params(fn):
    return [(p.name, p.kind, p.default) for p in inspect.signature(fn).parameters.values()]


def test_run_signature():
    params = {name: (kind, default) for name, kind, default in _params(slate.render.run)}
    assert list(params)[:6] == ["film_dir", "act", "frames", "engine", "samples", "out"]
    kw = inspect.Parameter.KEYWORD_ONLY
    assert params["act"] == (kw, None) and params["frames"] == (kw, None)
    assert params["engine"] == (kw, "remotion") and params["samples"] == (kw, None) and params["out"] == (kw, None)


def test_registry_signatures():
    assert [p[0] for p in _params(registry.entries)] == ["film_dir"]
    assert [p[0] for p in _params(registry.register)] == ["film_dir", "entry"]
    assert inspect.signature(registry.register).return_annotation in (None, "None")


def test_splice_signature():
    assert [p[0] for p in _params(splice.splice)] == ["film_dir", "base", "patch", "frame_in", "frame_out"]


def test_submodules_load_on_first_use():
    assert slate.render.registry is registry and slate.render.splice is splice
