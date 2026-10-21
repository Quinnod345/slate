"""A whole-act render retires the splice record it makes moot (render.splice), and only that one."""

from __future__ import annotations

from slate import gates
from slate.render import runner


def _splice_record(film, rel):
    gates.write_report(film, [gates.GateResult("render.splice", "PASS", "frames 380–560 replaced", evidence=[rel],
                                               inputs=gates.inputs_for(film, [film / rel]))])


def test_a_whole_act_render_supersedes_its_own_acts_splice(tmp_path):
    film = tmp_path / "film"
    (film / "renders").mkdir(parents=True)
    (film / "qa").mkdir()
    act1, act2 = film / "renders/act1.mov", film / "renders/act2.mov"
    act1.write_bytes(b"spliced")
    act2.write_bytes(b"other")
    _splice_record(film, "renders/act1.mov")
    act1.write_bytes(b"rendered whole")  # the act is rendered again, whole: the splice record goes stale
    assert gates.load_report(film)["render.splice"].status == "STALE"
    assert runner.supersede_splice(film, act2, 2) == []  # another act's render leaves it be
    new = runner.supersede_splice(film, act1, 1)
    assert len(new) == 1 and "superseded" in new[0].summary and "render.act1.frames" in new[0].summary
    assert new[0].engine["name"] and new[0].engine["version"]  # the gates schema requires both
    gates.write_report(film, new)
    rec = gates.load_report(film)["render.splice"]
    assert rec.status == "PASS" and rec.details["superseded"] == "frames 380–560 replaced"
