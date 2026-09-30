"""slate approved <file>: exit 0 only when that exact file's sha256 matches a person's final-cut approval and it is part
of a final delivery. Oneiro posts only a file that passes this."""

from __future__ import annotations

import shutil

import pytest

import filmkit
from slate.errors import Refused, UsageError
from slate.finish import approved


@pytest.fixture
def delivered(make_film):
    d = make_film("ok", mode="autonomous", format="finish-reel")
    assert filmkit.cli(d, "master")[0] == 0
    filmkit.pass_gates(d)
    assert filmkit.cli(d, "deliver")[0] == 0
    return d


def test_not_approved_until_the_owner_says_yes(delivered):
    f = delivered / "deliver/v1/masters/ok_H264.mp4"
    code, out, err = filmkit.cli(delivered, "approved", str(f))
    assert code == 1 and "nobody has approved this exact file" in err
    from slate.state import approvals

    approvals.record(delivered, what="final_cut", path="deliver/v1/masters/ok_H264.mp4", approved_by="Test Person")
    code, out = filmkit.cli_json(delivered, "approved", str(f))
    assert code == 0 and out["approved"] is True and out["version"] == 1
    assert out["approval"]["approved_by"] == "Test Person" and out["delivered_as"] == "deliver/v1/masters/ok_H264.mp4"
    # the other master wasn't approved: it isn't publishable
    other = delivered / "deliver/v1/masters/ok_ProRes422HQ.mov"
    assert filmkit.cli(delivered, "approved", str(other))[0] == 1


def test_a_copy_anywhere_passes_by_its_bytes(delivered, tmp_path, monkeypatch):
    from slate.state import approvals

    approvals.record(delivered, what="final_cut", path="deliver/v1/masters/ok_H264.mp4", approved_by="Test Person")
    copy = tmp_path / "elsewhere" / "post-me.mp4"
    copy.parent.mkdir()
    shutil.copy2(delivered / "deliver/v1/masters/ok_H264.mp4", copy)
    monkeypatch.delenv("SLATE_FILM", raising=False)
    out = approved.check(copy)  # no --film: every film is searched for these bytes
    assert out["approved"] and out["film"] == "ok"


def test_changed_bytes_are_not_approved(delivered, tmp_path):
    from slate.state import approvals

    approvals.record(delivered, what="final_cut", path="deliver/v1/masters/ok_H264.mp4", approved_by="Test Person")
    copy = tmp_path / "trimmed.mp4"
    data = (delivered / "deliver/v1/masters/ok_H264.mp4").read_bytes()
    copy.write_bytes(data[:-10])
    with pytest.raises(Refused, match="No approval matches"):
        approved.check(copy, film=str(delivered))


def test_approved_but_not_delivered(make_film):
    from slate.state import approvals

    d = make_film("early", mode="supervised")
    filmkit.cli(d, "master", "--ffmpeg")
    approvals.record(d, what="final_cut", path="masters/early_H264.mp4", approved_by="Test Person")
    with pytest.raises(Refused, match="isn't part of a final delivery"):
        approved.check(d / "masters/early_H264.mp4")


def test_a_draft_delivery_is_never_publishable(make_film):
    from slate.state import approvals

    d = make_film("dr", mode="supervised", replay_voice=True)
    filmkit.cli(d, "master", "--ffmpeg")
    filmkit.cli(d, "deliver", "--draft")
    rel = "deliver/drafts/v1.1/masters/dr_H264.mp4"
    approvals.record(d, what="final_cut", path=rel, approved_by="Test Person")
    with pytest.raises(Refused, match="draft delivery"):
        approved.check(d / rel)


def test_missing_file_is_usage(fin):
    with pytest.raises(UsageError):
        approved.check(fin["tmp"] / "nope.mp4")
