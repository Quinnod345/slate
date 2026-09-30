"""The names and signatures docs/ARCHITECTURE.md §12 promises other modules, and one regression from the films."""

import inspect

import numpy as np

from slate.vendors import elevenlabs
from slate.voice import audio, breath, captions, gates, profile, tags


def params(fn):
    return list(inspect.signature(fn).parameters)


def test_section_12_interfaces_exist_with_their_signatures():
    assert params(tags.strip)[:1] == ["text"]
    assert params(tags.check)[:2] == ["tagged", "line"]
    assert params(profile.load)[:1] == ["brand_pack"]
    assert params(captions.build)[:1] == ["film_dir"]
    assert params(gates.run)[:2] == ["film_dir", "names"]
    client = elevenlabs.Client()
    for name in ("subscription", "tts", "design", "remix", "isolate", "align", "compose_music"):
        assert callable(getattr(client, name)), name
    assert {"composition_plan", "prompt", "model_id", "force_instrumental", "seed",
            "respect_sections_durations"} <= set(params(elevenlabs.Client.compose_music))
    assert {"voice_id", "text", "model_id", "voice_settings", "seed", "previous_text", "next_text",
            "output_format"} <= set(params(elevenlabs.Client.tts))
    assert not hasattr(client, "sync_agents")


def test_creak_is_measured_where_a_mid_line_tag_pushes_the_voice_down(tmp_path):
    """Film regression: a mid-line softening tag put the narrator into vocal fry; creak is counted per take."""
    sr = 48000

    def tone(f0, seconds):
        t = np.arange(int(seconds * sr)) / sr
        return 0.3 * (np.sin(2 * np.pi * f0 * t) + 0.3 * np.sin(4 * np.pi * f0 * t))

    clean = np.concatenate([tone(220, 1.2), np.zeros(int(0.2 * sr)), tone(210, 1.2)])
    fry = np.concatenate([tone(220, 1.2), np.zeros(int(0.2 * sr)), tone(80, 1.2)])
    audio.write_wav(tmp_path / "clean.wav", clean, sr)
    audio.write_wav(tmp_path / "fry.wav", fry, sr)
    c = breath.span(breath.Frames(tmp_path / "clean.wav"), None)
    f = breath.span(breath.Frames(tmp_path / "fry.wav"), None)
    assert c["creak_frac"] < 0.05 < f["creak_frac"]
