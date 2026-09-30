"""The names and signatures docs/ARCHITECTURE.md §12 promises other modules from slate.sound."""

import inspect

import pytest

from slate.errors import Refused
from slate.sound import effects, measure, mix, revise_hooks, verify


def params(fn):
    return list(inspect.signature(fn).parameters)


def test_section_12_interfaces_exist_with_their_signatures():
    assert params(measure.loudness)[:1] == ["source"]
    assert params(mix.run)[:1] == ["film_dir"]
    assert params(verify.run)[:2] == ["film_dir", "names"] and "write" in params(verify.run)
    assert callable(effects.require_proof3) and params(effects.require_proof3) == []
    assert set(verify.GATES) == set(verify.GATE_NAMES) and all(n.startswith("sound.") for n in verify.GATES)
    assert params(revise_hooks.compare)[:4] == ["film_dir", "approved", "current", "revision"]


def test_the_effects_engine_refuses_until_proof_3_is_approved(slate_home):
    with pytest.raises(Refused) as exc:
        effects.require_proof3()
    assert exc.value.exit_code == 1 and "proof 3" in exc.value.message.lower()
