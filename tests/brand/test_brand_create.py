"""`slate brand init`: new packs go to the private store, never into Slate's repo."""

from __future__ import annotations

from pathlib import Path

import pytest

from slate import jsonio, paths
from slate.brand import create, pack
from slate.errors import Refused

from brand_helpers import write_app, write_git


def test_init_writes_to_the_private_store_from_the_template(store):
    res = create.init("acme", display_name="Acme")
    folder = Path(res["dir"])
    assert folder == (store / "acme").resolve() and res["in_store"]
    raw = jsonio.read(folder / "brand.json")
    assert raw["name"] == "acme" and raw["display_name"] == "Acme"
    assert raw["ownership"] == {"kind": "own", "organisation": None, "company_material": False, "notes": None}
    assert raw["references"] == {k: None for k in pack.REFERENCES}
    assert jsonio.validate(raw, "brand") == []
    for sub in ("logos", "references", "onboarding", "skills"):
        assert (folder / sub).is_dir()
    assert (folder / "onboarding" / "CHECKLIST.md").is_file() and (folder / "skills" / "README.md").is_file()
    assert res["status"] == "needs onboarding" and "--step truth" in res["next"]
    assert pack.status(pack.load("acme"))["status"] == "needs onboarding"


def test_init_refuses_to_put_a_real_brand_in_the_repo(store, monkeypatch):
    with pytest.raises(Refused) as exc:
        create.init("acme", store=paths.BRANDS_REPO)
    assert "neutral packs" in exc.value.message and exc.value.exit_code == 1
    assert not (paths.BRANDS_REPO / "acme").exists()
    with pytest.raises(Refused):
        create.init("acme", store=paths.REPO / "docs")
    # a private store that points into the repo is refused the same way
    monkeypatch.setenv("SLATE_BRANDS", str(paths.BRANDS_REPO))
    with pytest.raises(Refused):
        create.init("acme")
    assert not (paths.BRANDS_REPO / "acme").exists()


def test_init_refuses_neutral_names_existing_packs_and_bad_names(store):
    for name in ("demo", "_template"):
        with pytest.raises(Refused):
            create.init(name)
    create.init("acme")
    with pytest.raises(Refused) as exc:
        create.init("acme")
    assert "never writes over" in exc.value.message
    from slate.errors import UsageError

    with pytest.raises(UsageError):
        create.init("Acme Corp!")


def test_from_pack_takes_the_design_but_never_the_references_or_the_truth(store):
    res = create.init("studio", from_pack="demo")
    folder = Path(res["dir"])
    raw = jsonio.read(folder / "brand.json")
    demo = jsonio.read(paths.BRANDS_REPO / "demo" / "brand.json")
    assert raw["palette"] == demo["palette"] and raw["motion"] == demo["motion"] and raw["logos"] == demo["logos"]
    assert raw["references"] == {k: None for k in pack.REFERENCES}
    assert raw["truth"]["approved_claims"] == [] and raw["truth"]["claim_authority"] is None
    assert raw["display_name"] is None and raw["ownership"]["kind"] == "own"
    assert (folder / "logos" / "mark.svg").read_bytes() == (paths.BRANDS_REPO / "demo" / "logos" / "mark.svg").read_bytes()
    assert (folder / "motion.json").is_file() and (folder / "MOTION.md").is_file()
    from slate.brand import validate

    assert not [r for r in validate.validate("studio") if r.status == "FAIL"]


def test_client_and_company_material_ownership(store):
    res = create.init("partner", organisation="Partner Co", company_material=True)
    own = jsonio.read(Path(res["dir"]) / "brand.json")["ownership"]
    assert own == {"kind": "client", "organisation": "Partner Co", "company_material": True, "notes": None}


def test_init_from_app_reads_in_place_and_copies_no_code(store, tmp_path):
    app = write_app(tmp_path / "kettle-app")
    write_git(app)
    before = sorted(p.relative_to(app) for p in app.rglob("*"))
    res = create.init("kettle", from_app=[str(app)])
    folder = Path(res["dir"])
    assert sorted(p.relative_to(app) for p in app.rglob("*")) == before  # the source is untouched
    for p in folder.rglob("*"):  # and nothing from it was copied: only values and citations
        assert p.suffix not in (".css", ".ts", ".swift") and p.name != "Contents.json"
    raw = jsonio.read(folder / "brand.json")
    assert raw["palette"]["ink"] == "#1B1A18" and raw["palette"]["roles"]["accent"] == "kettle-500"
    assert raw["motion"]["eases"]  # a first law from the product's own motion
    inputs = jsonio.read(folder / "onboarding" / "look" / "inputs.json")
    src = next(iter(inputs["sources"].values()))
    assert src["read"] == "in_place" and src["pin"] == "0123456789abcdef0123456789abcdef01234567"
    assert res["look"]["step"] == "look"


def test_init_from_work_code_prints_the_read_only_procedure(store):
    res = create.init("corp", from_app=["work:~/code/corp-app"], company_material=True, organisation="Corp")
    procs = res["look"]["procedures"]
    assert procs and procs[0]["where"] == "WORK:~/code/corp-app"
    steps = " ".join(procs[0]["steps"])
    assert "peer ls" in steps and "peer cat" in steps and "--stdin-as" in steps
    assert "peer fetch" not in steps and "peer fetch of company code" in procs[0]["never"]
    inputs = jsonio.read(Path(res["dir"]) / "onboarding" / "look" / "inputs.json")
    assert inputs["sources"] == {} and inputs["pending"][0]["where"] == "WORK:~/code/corp-app"


def test_dry_run_writes_nothing(store):
    res = create.init("ghost", dry_run=True)
    assert res["dry_run"] and not (store / "ghost").exists()
