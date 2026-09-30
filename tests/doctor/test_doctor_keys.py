"""Keys by presence, the vendors' free probes, TLS, and the promise that no key value is ever printed."""

from __future__ import annotations

import json

from slate import secrets
from slate.cli import run_cli
from slate.doctor import core, keys
from slate.doctor.env import Env
from slate.errors import SlateError
from slate.http import HttpError

from doctor_helpers import by_id


def rows(denv):
    return by_id(keys.check_keys(denv, core.make_ctx(denv)))


def test_all_keys_present_and_accepted(denv):
    got = rows(denv)
    assert got["key.elevenlabs"]["status"] == "green"
    assert got["vendor.elevenlabs"]["status"] == "green" and "replay" in got["vendor.elevenlabs"]["summary"]
    assert got["key.elevenlabs-oneiro"]["status"] == "green" and "20,000" in got["key.elevenlabs-oneiro"]["summary"]
    assert got["key.higgsfield"]["status"] == "green" and "404" in got["key.higgsfield"]["summary"]
    assert got["net.tls"]["status"] == "green"


def test_missing_elevenlabs_key_is_red_and_skips_the_plan_check(denv):
    denv.secrets.pop("elevenlabs")
    got = rows(denv)
    assert got["key.elevenlabs"]["status"] == "red"
    assert "slate connect elevenlabs" in got["key.elevenlabs"]["fix"]
    assert "vendor.elevenlabs" not in got and denv.eleven_calls == 0


def test_elevenlabs_plan_tiers(denv):
    denv.eleven = {"tier": "creator", "replay": False}
    row = rows(denv)["vendor.elevenlabs"]
    assert row["status"] == "yellow" and "Creator" in row["summary"] and "Pro" in row["summary"]
    denv.eleven = {"tier": "starter", "replay": False}
    assert rows(denv)["vendor.elevenlabs"]["status"] == "red"
    denv.eleven = {"tier": "scale", "replay": False}
    assert rows(denv)["vendor.elevenlabs"]["status"] == "green"
    denv.eleven = {"tier": "mystery_plan_2030", "replay": False}
    assert rows(denv)["vendor.elevenlabs"]["status"] == "yellow"


def test_elevenlabs_plan_errors(denv):
    denv.eleven = HttpError(401, "https://api.elevenlabs.io/v1/user/subscription", "invalid key")
    row = rows(denv)["vendor.elevenlabs"]
    assert row["status"] == "red" and "rejected" in row["summary"]
    denv.eleven = SlateError("Couldn't reach api.elevenlabs.io: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] ...>")
    row = rows(denv)["vendor.elevenlabs"]
    assert row["status"] == "red" and "SSL_CERT_FILE" in row["fix"]
    denv.eleven = SlateError("Couldn't reach api.elevenlabs.io: timed out")
    assert rows(denv)["vendor.elevenlabs"]["status"] == "yellow"


def test_oneiro_key_needed_only_where_the_gateway_is(denv):
    denv.secrets.pop("elevenlabs_oneiro")
    row = rows(denv)["key.elevenlabs-oneiro"]
    assert row["status"] == "yellow" and "slate connect elevenlabs --oneiro" in row["fix"]
    assert "credit quota" in row["fix"] and "music and alignment" in row["fix"]
    denv.tools["openclaw"] = None
    assert rows(denv)["key.elevenlabs-oneiro"]["status"] == "green"


def test_higgsfield_probe_verdicts(denv):
    denv.hf_probe = {"ok": False, "status": "rejected", "http": 401, "replay": False}
    assert rows(denv)["key.higgsfield"]["status"] == "red"
    denv.hf_probe = {"ok": False, "status": "unreachable", "summary": "Couldn't reach api.higgsfield.ai: timed out"}
    assert rows(denv)["key.higgsfield"]["status"] == "yellow"
    denv.hf_probe = {"ok": False, "status": "unreachable",
                     "summary": "Couldn't reach api.higgsfield.ai: [SSL: CERTIFICATE_VERIFY_FAILED]"}
    row = rows(denv)["key.higgsfield"]
    assert row["status"] == "red" and "SSL_CERT_FILE" in row["fix"]
    denv.hf_probe = {"ok": False, "status": "unexpected", "http": 500}
    assert rows(denv)["key.higgsfield"]["status"] == "yellow"


def test_no_higgsfield_key_is_yellow_and_never_probed(denv):
    denv.secrets.pop("higgsfield")
    denv.hf_probe = AssertionError("the probe must not run without a key")
    row = rows(denv)["key.higgsfield"]
    assert row["status"] == "yellow" and "slate connect higgsfield --api-key" in row["fix"]


def test_tls_without_a_ca_bundle_is_red(denv):
    denv.tls = {"ca_certs": 0, "cafile": "/Library/Frameworks/Python.framework/Versions/3.13/etc/openssl/cert.pem",
                "cafile_exists": False, "capath_has_certs": False, "source": "Python's default context"}
    row = rows(denv)["net.tls"]
    assert row["status"] == "red" and "CERTIFICATE_VERIFY_FAILED" in row["summary"]
    assert "/etc/ssl/cert.pem" in row["fix"]


def test_real_tls_probe_counts_certificates(monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/etc/ssl/cert.pem")
    info = Env().tls_info()
    assert info["ca_certs"] > 0 and info["env_cert_file"] == "/etc/ssl/cert.pem"


# ---------------------------------------------------------------------------------------------------------------
# No key value is ever printed, logged or saved


SENTINELS = {"ELEVENLABS_API_KEY": "sk_test_SENTINEL_ELEVEN_0123456789",
             "HF_KEY": "hfid-SENTINEL:hfsecret-SENTINEL-abcdef",
             "SLATE_ONEIRO_ELEVENLABS_API_KEY": "sk_test_SENTINEL_ONEIRO_987"}


def test_keys_never_printed_in_doctor_output_or_saved_report(denv, monkeypatch, slate_home):
    for k, v in SENTINELS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(type(denv), "secret_where", Env.secret_where)
    monkeypatch.setattr(type(denv), "eleven_subscription", Env.eleven_subscription)
    monkeypatch.setattr(type(denv), "higgsfield_probe", Env.higgsfield_probe)
    code, out, err = run_cli(["doctor", "--json", "--context", "keys-test"])
    rep = json.loads(out)
    got = by_id(rep["checks"])
    assert got["key.elevenlabs"]["details"]["where"] == "env"
    assert got["key.higgsfield"]["details"]["where"] == "env"
    saved = (slate_home["data"] / "doctor" / "keys-test.json").read_text()
    code_h, out_h, err_h = run_cli(["doctor"])
    for value in SENTINELS.values():
        for part in value.split(":"):
            assert part not in out and part not in err and part not in saved
            assert part not in out_h and part not in err_h


def test_secret_where_never_reads_the_value(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test_SENTINEL_X")
    calls = []
    monkeypatch.setattr(secrets, "_keychain_read", lambda service: calls.append(service))
    assert Env().secret_where("elevenlabs") == "env"
    assert calls == []
