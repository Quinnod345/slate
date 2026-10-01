"""Every video request says whether it wants sound (L-generate-D002): Kling 3.0 Pro defaults to sound on and Seedance
2.5 to generate_audio true, and audio costs more, so a block that doesn't ask for `audio` sends it off."""

from __future__ import annotations

from slate.gen import bodies

FILM = {"width": 320, "height": 180}
URLS = {"image": "https://example.com/start.png"}


def _body(model: str, role: str = "plate_video", **block):
    return bodies.build(role, model, {"prompt": "a quiet shoreline at dawn", "duration": 4, **block}, FILM, urls=URLS)


def test_a_block_without_audio_sends_it_off():
    assert _body("bytedance/seedance-2.5/image-to-video")["generate_audio"] is False
    assert _body("kling-video/v3.0/pro/image-to-video")["sound"] == "off"
    assert "generate_audio" not in _body("kling-video/v3.0/pro/image-to-video")


def test_audio_true_still_asks_for_plate_ambience():
    assert _body("bytedance/seedance-2.5/image-to-video", audio=True)["generate_audio"] is True
    assert _body("kling-video/v3.0/pro/image-to-video", audio=True)["sound"] == "on"


def test_every_video_role_carries_the_flag_and_a_still_carries_none():
    for role in ("plate_video_reference", "plate_video_extend"):
        body = bodies.build(role, "bytedance/seedance-2.5/reference-to-video",
                            {"prompt": "a slow push in", "duration": 4}, FILM, urls={})
        assert body["generate_audio"] is False, role
    still = bodies.build("plate_still", "higgsfield-ai/soul/cinema", {"prompt": "a street from above"}, FILM, urls={})
    assert "generate_audio" not in still and "sound" not in still


def test_the_blocks_own_params_still_win():
    body = _body("bytedance/seedance-2.5/image-to-video", params={"generate_audio": True})
    assert body["generate_audio"] is True  # a person's explicit params are the last word
