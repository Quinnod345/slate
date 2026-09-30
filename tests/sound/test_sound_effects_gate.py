"""The effects engine waits for a person's approval of proof 3: the refusal, what counts as approval, the palette."""

import json
import re
from pathlib import Path

import pytest

from slate import jsonio, paths
from slate.cli import run_cli
from slate.errors import Refused
from slate.sound import effects, verify
from slate.sound.effects import engine, gate, palette


def _approvals(obj):
    jsonio.write_atomic(paths.data() / "approvals.json", obj)


def _proof(tmp_path, content=b"proof 3 mix bytes"):
    p = tmp_path / "proof3_mix.wav"
    p.write_bytes(content)
    return p, jsonio.sha256(p)


def test_refuses_until_proof3_is_approved(slate_home):
    with pytest.raises(Refused) as exc:
        effects.require_proof3()
    assert "proof 3" in exc.value.message and "slate approve proof3" in exc.value.hint
    assert exc.value.exit_code == 1


@pytest.mark.parametrize("argv", [["sfx"], ["sfx", "spot"], ["sfx", "render"]])
def test_sfx_refuses_with_the_gate_message(slate_home, sound_film, argv):
    code, out, _ = run_cli([*argv, "--film", str(sound_film), "--json"])
    res = json.loads(out)
    assert code == 1 and res["error"] == "refused" and "proof 3" in res["message"]
    assert res["details"]["gate"] == "proof3"


def test_sfx_status_reports_the_gate_without_refusing(slate_home):
    code, out, _ = run_cli(["sfx", "status", "--json"])
    st = json.loads(out)
    assert code == 0 and st["approved"] is False and st["problems"] == ["no approval of proof 3 is recorded"]


def test_an_agent_cannot_sign_the_approval(slate_home, tmp_path):
    p, sha = _proof(tmp_path)
    _approvals({"approvals": [{"what": "proof3", "path": str(p), "sha256": sha, "approved_by": "Claude (agent)"}]})
    with pytest.raises(Refused) as exc:
        effects.require_proof3()
    assert "agent" in exc.value.message


def test_the_approval_is_tied_to_the_exact_file(slate_home, tmp_path):
    p, sha = _proof(tmp_path)
    _approvals({"approvals": [{"what": "proof3", "path": str(p), "sha256": sha, "approved_by": "A. Person"}]})
    assert effects.require_proof3()["approved_by"] == "A. Person"
    p.write_bytes(b"a different mix")
    with pytest.raises(Refused) as exc:
        effects.require_proof3()
    assert "changed since it was approved" in exc.value.message
    _approvals({"approvals": [{"what": "proof3", "path": str(tmp_path / "gone.wav"), "sha256": sha, "approved_by": "A. Person"}]})
    with pytest.raises(Refused):
        effects.require_proof3()
    _approvals({"approvals": [{"what": "proof3", "path": str(p), "approved_by": "A. Person"}]})
    with pytest.raises(Refused) as exc:
        effects.require_proof3()
    assert "SHA-256" in exc.value.message


def test_a_revocation_closes_the_gate_again(slate_home, tmp_path):
    p, sha = _proof(tmp_path)
    rec = {"what": "proof3", "path": str(p), "sha256": sha, "approved_by": "A. Person"}
    _approvals([rec, {**rec, "revoked": True}])
    assert not effects.status()["approved"]


def test_both_record_shapes_are_read(slate_home, tmp_path):
    p, sha = _proof(tmp_path)
    _approvals({"proof3": {"path": str(p), "sha256": sha, "approved_by": "A. Person"}})
    assert effects.status()["approved"]


def test_past_the_gate_the_engine_says_it_is_next_instead_of_pretending(slate_home, sound_film, tmp_path):
    p, sha = _proof(tmp_path)
    _approvals({"approvals": [{"what": "proof3", "path": str(p), "sha256": sha, "approved_by": "A. Person"}]})
    code, out, _ = run_cli(["sfx", "status", "--json"])
    assert code == 0 and json.loads(out)["approved"]
    code, out, _ = run_cli(["sfx", "spot", "--film", str(sound_film), "--json"])
    res = json.loads(out)
    assert code == 1 and "effects engine is next" in res["message"]
    with pytest.raises(Refused):
        engine.render(sound_film)


def test_verify_never_reaches_the_engine_while_it_is_gated(slate_home):
    assert verify._engine_renderer() is None


def test_the_starting_palette_is_proof_2(slate_home):
    pal = palette.STARTING_PALETTE
    assert pal["levels"]["lu_vs_voice"] == {"ui": -17.0, "fx": -11.2}
    kinds = {"tap", "type", "land", "snap", "count", "draw", "data", "page", "stamp", "whoosh", "swell", "character", "impact"}
    assert kinds == set(pal["kinds"])  # every beat kind in the film folder contract has its one sound
    sounds = [k["family"] for k in pal["kinds"].values()]
    assert len(sounds) == len(set(sounds))  # no sound serves two kinds
    for rule in ("no_glides", "no_slide_ups", "no_drone", "one_sound_per_kind", "no_identical_repeats",
                 "sampled_tonal_roles"):
        assert pal["rules"][rule] is True
    assert "chimes" in pal["banned_timbres"] and "woodblock" in pal["materials"]


def test_a_brand_palette_extends_the_starting_point(slate_home):
    brand = {"name": "b", "sound": {"palette": {"levels": {"lu_vs_voice": {"ui": -19.0}}, "rules": {"no_drone": False}},
                                    "materials": ["glass marbles"], "banned_timbres": ["kazoo"]}}
    pal = palette.from_brand(brand)
    assert pal["levels"]["lu_vs_voice"] == {"ui": -19.0, "fx": -11.2}
    assert pal["rules"]["no_drone"] is False and pal["rules"]["no_glides"] is True
    assert "glass marbles" in pal["materials"] and "woodblock" in pal["materials"]
    assert "kazoo" in pal["banned_timbres"] and "chimes" in pal["banned_timbres"]
    assert pal["from_brand"] and palette.from_brand(None)["from_brand"] is False


SOUND_SRC = Path(__file__).resolve().parents[2] / "src" / "slate" / "sound"
SCHEMAS = [Path(__file__).resolve().parents[2] / "src/slate/schemas" / f"{n}.schema.json"
           for n in ("score", "mix", "sound_cues")]


def test_no_company_material_and_no_assumed_clock_in_the_sound_code():
    banned = re.compile(r"truespace|vixa|trudy|\beos\b|inner.?echo", re.I)
    literals = re.compile(r"(?<![\d.])(1920|1080|30000\s*/\s*1001|29\.97)(?![\d])")
    for f in [*SOUND_SRC.rglob("*.py"), *SCHEMAS]:
        text = f.read_text()
        assert not banned.search(text), f"{f} names company material"
        assert not literals.search(text), f"{f} assumes a frame size or rate"
