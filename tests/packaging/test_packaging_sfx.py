"""The MCP sound effects surface keeps media work queued and approval decisions outside the allowlist."""

import pytest

from slate import paths
from slate.errors import Refused
from slate.jobs import store
from slate.mcp import policy, tools


@pytest.mark.parametrize("stage", ["spot", "render", "run"])
def test_effects_are_queued_in_the_render_slot_without_paid_spend(studio, stage):
    film = tools.new_film("sound-cues", "plain-brand", studio["format"], git=False)
    assert film["ok"], film
    result = tools.job_submit(f"sfx {stage}", film="sound-cues", args=["--seed", "17"] if stage != "render" else [])
    assert result["ok"], result
    job = store.load(result["result"]["job"])
    assert job["resources"] == ["render"]
    assert job["kind"] == "sfx"
    argv = job["argv"]
    assert "sfx" in argv and stage in argv
    assert str((paths.films() / "sound-cues").resolve()) in argv
    assert "--live" not in argv
    assert f"sfx {stage}" not in policy.RUN


def test_effects_status_is_inline_and_approval_flags_are_refused():
    assert policy.RUN["sfx status"].how == "inline"
    assert policy.RUN["sfx status"].action == ("status",)
    for stage in ("sfx spot", "sfx render", "sfx run"):
        with pytest.raises(Refused):
            policy.check_args(policy.JOBS[stage], ["--by", "someone"])
        with pytest.raises(Refused):
            policy.check_args(policy.JOBS[stage], ["--out", "/tmp/elsewhere"])
