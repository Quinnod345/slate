"""QA and finishing can read an unchanged referenced timeline without redirecting child outputs."""

from __future__ import annotations

from slate import jsonio
from slate.qa.film import FilmContext
from slate.qa.checks import text
from slate.state import lock


def _reference(tmp_path):
    parent, child = tmp_path / "parent", tmp_path / "child"
    for folder in (parent, child):
        folder.mkdir()
        jsonio.write_atomic(folder / "film.json", {"id": folder.name, "fps_num": 30000, "fps_den": 1001,
                                                 "width": 1080, "height": 1920})
    path = parent / "timing/shots.json"
    path.parent.mkdir()
    jsonio.write_atomic(path, {"total_frames": 862, "shots": [{"id": "S01", "frame_in": 0, "frame_out": 862}]})
    lock.add_refs(child, [{"path": "timing/shots.json", "parent_path": "timing/shots.json",
                           "sha256": jsonio.sha256(path), "kind": "file"}],
                  parent_id="parent", parent_dir=parent)
    return parent, child, path


def test_missing_child_source_is_read_and_hashed_through_its_reference(tmp_path):
    _parent, child, source = _reference(tmp_path)
    ctx = FilmContext(child)
    assert ctx.total_frames == 862
    assert ctx.exists("timing/shots.json")
    assert ctx.sha("timing/shots.json") == jsonio.sha256(source)
    assert ctx.inputs(["timing/shots.json"])["timing/shots.json"] == jsonio.sha256(source)
    assert ctx.path("timing/shots.json") == child / "timing/shots.json"
    assert not ctx.path("timing/shots.json").exists()


def test_child_owned_input_wins_and_output_paths_stay_in_child(tmp_path):
    parent, child, source = _reference(tmp_path)
    own = child / "timing/shots.json"
    own.parent.mkdir()
    jsonio.write_atomic(own, {"total_frames": 400})
    ctx = FilmContext(child)
    assert ctx.total_frames == 400 and ctx.sha("timing/shots.json") == jsonio.sha256(own)
    assert ctx.path("masters/child.mp4") == child / "masters/child.mp4"
    assert not ctx.exists("timing/unrelated.json") and ctx.read_json("timing/unrelated.json") is None
    assert ctx.sha("timing/unrelated.json") is None
    assert source.is_file() and not (parent / "masters/child.mp4").exists()


def test_broken_reference_stays_missing(tmp_path):
    _parent, child, source = _reference(tmp_path)
    source.unlink()
    ctx = FilmContext(child)
    assert ctx.total_frames is None
    assert not ctx.exists("timing/shots.json") and ctx.sha("timing/shots.json") is None


def test_actual_caption_gate_reads_referenced_cues_and_records_their_real_input(tmp_path):
    parent, child, _source = _reference(tmp_path)
    docs = {
        "timing/captions.json": {"cues": [{"index": 1, "text": "A neutral line.",
                                            "lines": ["A neutral line."], "start": 0.3, "end": 2.0}]},
        "timing/vo_words.json": {"shots": {"S01": {"line": "A neutral line.", "words": []}}},
    }
    refs = []
    for rel, doc in docs.items():
        jsonio.write_atomic(parent / rel, doc)
        refs.append({"path": rel, "parent_path": rel, "sha256": jsonio.sha256(parent / rel), "kind": "file"})
    lock.add_refs(child, refs, parent_id="parent", parent_dir=parent)
    cap = parent / "timing/captions.json"
    result = text.captions(child)
    assert result.status == "PASS", result.to_dict()
    assert FilmContext(child).captions_path() == cap
    assert result.inputs[str(cap)] == jsonio.sha256(cap)


def test_visual_event_reader_uses_its_referenced_source_without_owning_parent_output(tmp_path):
    parent, child, _source = _reference(tmp_path)
    rel = "timing/visual_events/act1.json"
    path = parent / rel
    path.parent.mkdir()
    jsonio.write_atomic(path, {"silent": True, "reason": "A neutral silent source fixture."})
    lock.add_refs(child, [{"path": rel, "parent_path": rel, "sha256": jsonio.sha256(path), "kind": "file"}],
                  parent_id="parent", parent_dir=parent)
    ctx = FilmContext(child)
    events = ctx.act_events(1)
    assert events.path == path and events.exists and events.silent and not events.problems
    assert ctx.path(rel) == child / rel and not ctx.path(rel).exists()


def test_explicit_mix_reference_is_only_a_source_and_child_audio_wins(tmp_path):
    parent, child, _source = _reference(tmp_path)
    rel = "sound/mix/final_mix.wav"
    source = parent / rel
    source.parent.mkdir(parents=True)
    source.write_bytes(b"neutral source bytes; no audio decoder is used")
    lock.add_refs(child, [{"path": rel, "parent_path": rel, "sha256": jsonio.sha256(source), "kind": "file"}],
                  parent_id="parent", parent_dir=parent)
    assert FilmContext(child).mix_path() == source
    own = child / rel
    own.parent.mkdir(parents=True)
    own.write_bytes(b"child-owned neutral bytes")
    ctx = FilmContext(child)
    assert ctx.mix_path() == own and ctx.path(rel) == own
    assert source.read_bytes() == b"neutral source bytes; no audio decoder is used"


def test_caption_fallback_discovers_file_references_and_prefers_owned_sidecars(tmp_path):
    parent, child, _source = _reference(tmp_path)
    rel = "voice/captions/captions.srt"
    source = parent / rel
    source.parent.mkdir(parents=True)
    source.write_text("1\n00:00:00,300 --> 00:00:02,000\nA neutral line.\n")
    lock.add_refs(child, [{"path": rel, "parent_path": rel, "sha256": jsonio.sha256(source), "kind": "file"}],
                  parent_id="parent", parent_dir=parent)
    assert FilmContext(child).captions_path() == source
    own = child / rel
    own.parent.mkdir(parents=True)
    own.write_text("1\n00:00:00,300 --> 00:00:02,000\nA child line.\n")
    assert FilmContext(child).captions_path() == own


def test_referenced_mix_does_not_import_unreferenced_parent_stems(tmp_path):
    parent, child, _source = _reference(tmp_path)
    mix_rel = "sound/mix/final_mix.wav"
    stem_rel = "sound/mix/stems/dialogue.wav"
    for rel in (mix_rel, stem_rel):
        path = parent / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"neutral parent input")
    lock.add_refs(child, [{"path": mix_rel, "parent_path": mix_rel,
                           "sha256": jsonio.sha256(parent / mix_rel), "kind": "file"}],
                  parent_id="parent", parent_dir=parent)
    assert FilmContext(child).stem_paths() == {}
    own = child / stem_rel
    own.parent.mkdir(parents=True)
    own.write_bytes(b"neutral child stem")
    assert FilmContext(child).stem_paths() == {"dialogue": own}
    assert FilmContext(child).path(stem_rel) == own


def test_stem_discovery_reads_only_explicit_file_references(tmp_path):
    parent, child, _source = _reference(tmp_path)
    refs = []
    for rel in ("sound/mix/final_mix.wav", "sound/mix/stems/dialogue.wav"):
        path = parent / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"neutral referenced input")
        refs.append({"path": rel, "parent_path": rel, "sha256": jsonio.sha256(path), "kind": "file"})
    lock.add_refs(child, refs, parent_id="parent", parent_dir=parent)
    assert FilmContext(child).stem_paths() == {"dialogue": parent / "sound/mix/stems/dialogue.wav"}


def test_custom_mix_record_keeps_stem_discovery_in_its_logical_child_folder(tmp_path):
    parent, child, _source = _reference(tmp_path)
    rel = "sound/custom/mix.wav"
    source = parent / rel
    source.parent.mkdir(parents=True)
    source.write_bytes(b"neutral custom source mix")
    lock.add_refs(child, [{"path": rel, "parent_path": rel, "sha256": jsonio.sha256(source), "kind": "file"}],
                  parent_id="parent", parent_dir=parent)
    (child / "sound").mkdir()
    jsonio.write_atomic(child / "sound/mix.json", {"file": rel})
    own = child / "sound/custom/stems/dialogue.wav"
    own.parent.mkdir(parents=True)
    own.write_bytes(b"neutral child dialogue")
    assert FilmContext(child).stem_paths() == {"dialogue": own}
