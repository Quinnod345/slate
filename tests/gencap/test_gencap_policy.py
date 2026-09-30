"""When a shot may generate (the requester's setting, reason categories, drafts, autonomous yeses, brand uploads)."""

from __future__ import annotations

from pathlib import Path

from slate.gen import policy

REAL = {"id": "S02", "generate": {"reason": "real-scene", "why": "a real shoreline", "prompt": "a shoreline"}}
ADDS = {"id": "S03", "generate": {"reason": "adds-something", "why": "an ink bleed code can't paint",
                                  "prompt": "ink bleeding"}}
BRIDGE = {"id": "S05", "generate": {"reason": "bridge-matte", "why": "paper bleeding into the real plate",
                                    "prompt": "ink"}}


def film(**kw) -> dict:
    return {"id": "f", "generation": "rarely", "mode": "supervised", **kw}


def test_rarely_is_the_default_and_allows_only_what_code_cant_make():
    ok, why = policy.allowed({"id": "f"}, REAL)
    assert ok and "rarely" in why
    for reason in ("real-people", "real-scene", "stock"):
        assert policy.allowed(film(), {"id": "S", "generate": {"reason": reason, "prompt": "p"}})[0]
    ok, why = policy.allowed(film(), ADDS)
    assert not ok and "where-it-helps" in why
    assert not policy.allowed(film(), BRIDGE)[0]


def test_where_it_helps_allows_more_with_a_written_why():
    assert policy.allowed(film(generation="where-it-helps"), ADDS)[0]
    assert policy.allowed(film(generation="where-it-helps"), BRIDGE)[0]
    vague = {"id": "S03", "generate": {"reason": "adds-something", "why": "nice", "prompt": "p"}}
    ok, why = policy.allowed(film(generation="where-it-helps"), vague)
    assert not ok and "why" in why


def test_none_means_nothing_generates():
    ok, why = policy.allowed(film(generation="none"), REAL)
    assert not ok and "none" in why


def test_a_shot_without_a_generate_block_is_built_in_code():
    ok, why = policy.allowed(film(), {"id": "S01"})
    assert not ok and "code builds it" in why


def test_a_reason_category_is_required():
    ok, why = policy.allowed(film(), {"id": "S", "generate": {"prompt": "a nice shot"}})
    assert not ok and "reason category" in why
    ok, _ = policy.allowed(film(), {"id": "S", "generate": {"reason": "because", "prompt": "p"}})
    assert not ok


def test_designed_content_is_never_generated():
    shot = {"id": "S", "generate": {"reason": "stock", "prompt": "an app screen", "contains": ["UI", "text"]}}
    ok, why = policy.allowed(film(generation="where-it-helps"), shot)
    assert not ok and "designed" in why and "code builds it" in why


def test_drafts_have_no_plates():
    ok, why = policy.allowed(film(mode="draft"), REAL)
    assert not ok and "Draft" in why


def test_autonomous_films_may_generate_but_each_waits_for_a_yes():
    ok, why = policy.allowed(film(mode="autonomous"), REAL)
    assert ok and "yes" in why
    assert policy.requires_approval(film(mode="autonomous"))
    assert not policy.requires_approval(film())


def test_a_bad_setting_or_one_take_is_refused():
    assert not policy.allowed(film(generation="lots"), REAL)[0]
    one = {"id": "S", "generate": {"reason": "stock", "prompt": "p", "takes": 1}}
    ok, why = policy.allowed(film(), one)
    assert not ok and "at least 2" in why


def test_brand_material_goes_to_a_generator_only_when_the_pack_allows(tmp_path):
    film_dir = tmp_path / "film"
    take = film_dir / "plates" / "S04" / "takes" / "t1" / "out.png"
    brand_file = film_dir / "refs" / "logo-frame.png"
    for p in (take, brand_file):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    closed, open_ = {"name": "neutral"}, {"name": "neutral", "grounding": {"uploads_allowed": True}}
    assert policy.is_generator_output(film_dir, take)
    assert not policy.is_generator_output(film_dir, brand_file)
    assert policy.check_uploads(film_dir, closed, [take]) == []  # a take we got back isn't brand material
    problems = policy.check_uploads(film_dir, closed, [brand_file])
    assert problems and "allows uploads" in problems[0]
    assert policy.check_uploads(film_dir, open_, [brand_file, take]) == []
    assert not policy.uploads_allowed({"grounding": {"uploads_allowed": "yes"}})  # only a real true counts


def test_the_interface_signature():
    import inspect

    sig = inspect.signature(policy.allowed)
    assert list(sig.parameters) == ["film_json", "shot"]
    out = policy.allowed(film(), REAL)
    assert isinstance(out, tuple) and isinstance(out[0], bool) and isinstance(out[1], str)
    assert Path(policy.__file__).name == "policy.py"
