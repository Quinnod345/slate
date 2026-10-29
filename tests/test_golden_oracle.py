"""A golden oracle freezes inputs and expected records together, without absorbing later film edits."""

import json
from pathlib import Path

import pytest

import golden_oracle as oracle


def _source(tmp_path):
    source = tmp_path / "source"
    (source / "timing").mkdir(parents=True)
    (source / "timing/words.json").write_text('{"words": ["one", "two"]}\n')
    (source / "reference.json").write_text('{"frames": 48}\n')
    return source, ["timing/words.json", "reference.json"]


def test_snapshot_is_portable_and_independent_of_later_source_edits(tmp_path):
    source, files = _source(tmp_path)
    output = tmp_path / "private/oracle"
    manifest = oracle.capture(source, output, files, label="delivered-v1")
    assert len(manifest["files"]) == 2
    assert (output / files[0]).stat().st_ino != (source / files[0]).stat().st_ino
    (source / files[0]).write_text("changed source\n")
    moved = tmp_path / "moved"
    output.rename(moved)
    assert oracle.validate(moved) == manifest
    assert (moved / files[0]).read_text() == '{"words": ["one", "two"]}\n'


def test_existing_snapshot_is_never_replaced(tmp_path):
    source, files = _source(tmp_path)
    output = tmp_path / "oracle"
    oracle.capture(source, output, files, label="v1")
    before = (output / oracle.MANIFEST).read_bytes()
    with pytest.raises(oracle.OracleError, match="never replaced"):
        oracle.capture(source, output, files, label="v2")
    assert (output / oracle.MANIFEST).read_bytes() == before


@pytest.mark.parametrize("name", ["../secret", "/tmp/secret", "x/../secret", "x//secret", ".git/config", "x\\secret"])
def test_capture_refuses_nonlocal_paths(tmp_path, name):
    source, _ = _source(tmp_path)
    with pytest.raises(oracle.OracleError):
        oracle.capture(source, tmp_path / "oracle", [name], label="v1")
    assert not (tmp_path / "oracle").exists()


def test_capture_keeps_private_material_out_of_source_and_repo(tmp_path):
    source, files = _source(tmp_path)
    for destination in (source / "oracle", oracle.REPO / "must-not-exist-oracle"):
        with pytest.raises(oracle.OracleError, match="outside"):
            oracle.capture(source, destination, files, label="v1")
        assert not destination.exists()


def test_capture_refuses_a_symlink_even_when_it_stays_in_the_source(tmp_path):
    source, files = _source(tmp_path)
    (source / "linked.json").symlink_to(source / files[0])
    with pytest.raises(oracle.OracleError, match="symlinks"):
        oracle.capture(source, tmp_path / "oracle", ["linked.json"], label="v1")


def test_source_change_mid_capture_refuses_the_entire_snapshot(tmp_path, monkeypatch):
    source, files = _source(tmp_path)
    real_copy = oracle.shutil.copy2

    def changing_copy(src, dst):
        real_copy(src, dst)
        if Path(src).name == "reference.json":
            (source / files[0]).write_text("changed while another file copied\n")

    monkeypatch.setattr(oracle.shutil, "copy2", changing_copy)
    with pytest.raises(oracle.OracleError, match="Source changed"):
        oracle.capture(source, tmp_path / "oracle", files, label="v1")
    assert not (tmp_path / "oracle").exists()
    assert not list(tmp_path.glob(".oracle-*"))


@pytest.mark.parametrize("change", ["tamper", "remove", "extra", "symlink"])
def test_validation_refuses_any_change_to_the_frozen_set(tmp_path, change):
    source, files = _source(tmp_path)
    output = tmp_path / "oracle"
    oracle.capture(source, output, files, label="v1")
    target = output / files[0]
    if change == "tamper":
        target.chmod(0o644)
        target.write_text("wrong bytes\n")
    elif change == "remove":
        target.unlink()
    elif change == "extra":
        (output / "unlisted.json").write_text("{}\n")
    else:
        (output / "unlisted.json").symlink_to(source / files[0])
    with pytest.raises(oracle.OracleError):
        oracle.validate(output)


def test_explicit_missing_oracle_is_an_error_not_a_skip(tmp_path):
    with pytest.raises(oracle.OracleError, match="No frozen oracle"):
        with oracle.verified(tmp_path / "missing"):
            pytest.fail("a missing oracle was accepted")


def test_selected_oracle_is_rechecked_when_the_session_ends(tmp_path):
    source, files = _source(tmp_path)
    output = tmp_path / "oracle"
    oracle.capture(source, output, files, label="v1")
    with pytest.raises(oracle.OracleError, match="missing"):
        with oracle.verified(output) as selected:
            assert selected == output
            (output / files[0]).unlink()
