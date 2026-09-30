"""Keys and the vendors' free probes.

Keys are checked by presence only (slate.secrets.present: the environment, then whether the Keychain item exists), so
doctor never reads, prints or stores a value. Then each vendor gets one free question, through Slate's own client for
it (replay-aware, like every vendor call):

- ElevenLabs: GET /v1/user/subscription, for the plan. Creator works; Pro or higher gives lossless voice (PCM 48 kHz).
- Higgsfield: a status request for a request id that can't exist. 404 means the key is accepted, 401 that it isn't.
  It costs nothing. Higgsfield has no balance endpoint, so the Cloud API balance itself isn't checked.

A live call needs a CA bundle Python can find, and the python.org build Slate's environment was made from points at
one that doesn't exist, so every live vendor call would fail TLS verification. That gets its own row.
"""

from __future__ import annotations

from slate.doctor import rows as R
from slate.doctor.common import Ctx
from slate.doctor.env import Env

G = "keys"
WHERE = {"env": "the environment", "keychain": "your login Keychain"}
TLS_FIX = ("Point Python at macOS's own certificates where Slate runs: `export SSL_CERT_FILE=/etc/ssl/cert.pem` (in "
           "~/.zshenv for Terminals and agents). The lasting fix is for Slate's HTTP layer to load that bundle itself.")


def _where(where: str | None) -> str:
    return WHERE.get(where or "", where or "nowhere")


def _is_tls(text: str) -> bool:
    low = text.lower()
    return "certificate_verify_failed" in low or "certificate verify failed" in low or "ssl:" in low


def _vendor_error(rid: str, vendor: str, exc: Exception, connect: str) -> dict:
    from slate.secrets import redact  # noqa: PLC0415

    if isinstance(exc, ImportError):
        return R.yellow(rid, f"Slate's {vendor} client isn't in this checkout (or doesn't load), so the free check "
                             f"couldn't run: {exc}", "Update Slate (git pull); `slate doctor` names any module that "
                                                     "doesn't load.", group=G)
    status = getattr(exc, "status", None) or (getattr(exc, "details", None) or {}).get("status")
    message = redact(getattr(exc, "message", None) or str(exc))
    if status in (401, 403):
        return R.red(rid, f"{vendor} rejected the key (HTTP {status}).",
                     f"Run `{connect}` in your own Terminal to replace it (it asks there, never in chat).", group=G)
    if _is_tls(message):
        return R.red(rid, f"Python couldn't verify {vendor}'s certificate, so the free check never reached it: "
                          f"{message[:160]}", TLS_FIX, group=G)
    return R.yellow(rid, f"Couldn't reach {vendor} for the free check: {message[:200]}",
                    "Check the network, then run `slate doctor` again.", group=G)


def check_keys(env: Env, ctx: Ctx) -> list[dict]:
    return [*_elevenlabs(env, ctx), _oneiro(env, ctx), _higgsfield(env, ctx), _tls(env, ctx)]


def _elevenlabs(env: Env, ctx: Ctx) -> list[dict]:
    where = env.secret_where("elevenlabs")
    if not where:
        return [R.red("key.elevenlabs", "No ElevenLabs key. Every voice line and score goes through ElevenLabs.",
                      "Run `slate connect elevenlabs` in your own Terminal (it asks for the key there, never in chat).",
                      group=G)]
    rows = [R.green("key.elevenlabs", f"ElevenLabs key found in {_where(where)} (checked by presence; its value is "
                                      "never read here)", group=G, details={"where": where})]
    rows.append(_eleven_plan(env, ctx))
    return rows


def _eleven_plan(env: Env, ctx: Ctx) -> dict:
    rid = "vendor.elevenlabs"
    try:
        sub = env.eleven_subscription()
    except Exception as exc:  # noqa: BLE001 - every failure becomes a readable row
        return _vendor_error(rid, "ElevenLabs", exc, "slate connect elevenlabs")
    tier = sub.get("tier")
    replayed = bool(sub.get("replay"))
    rank, tiers = env.eleven_tier_rank(tier)
    note = " (replay: a recorded stand-in, not your real plan)" if replayed else ""
    details = {"tier": tier, "lossless": sub.get("lossless"), "best_voice_format": sub.get("best_voice_format"),
               "character_count": sub.get("character_count"), "character_limit": sub.get("character_limit"),
               "replay": replayed}
    pro, creator = tiers.index("pro"), tiers.index("creator")
    name = str(tier or "unknown").replace("_", " ").title()
    if rank is None:
        return R.yellow(rid, f"ElevenLabs reports a plan Slate doesn't know ({tier!r}){note}. Voice falls back to the "
                             "best format the plan accepts.", "Nothing to do unless voice sounds lossy; "
                        "`slate voice tts` records which format it used and why.", group=G, details=details)
    if rank >= pro:
        return R.green(rid, f"ElevenLabs {name} plan: lossless voice (PCM 48 kHz){note}", group=G, details=details)
    if rank == creator:
        return R.yellow(rid, f"ElevenLabs Creator plan{note}: Slate works, and voice arrives as 192 kbps mp3, because "
                             "lossless voice needs Pro or higher.",
                        "If you want lossless voice, move to Pro in ElevenLabs. Slate never changes your plan.",
                        group=G, details=details)
    return R.red(rid, f"ElevenLabs {name} plan{note}: Slate needs Creator or higher (192 kbps voice and the music "
                      "model need it).", "Choose Creator or higher in ElevenLabs. Slate never changes your plan.",
                 group=G, details=details)


def _oneiro(env: Env, ctx: Ctx) -> dict:
    rid = "key.elevenlabs-oneiro"
    where = env.secret_where("elevenlabs_oneiro")
    cap = env.oneiro_cap()
    cap_text = f"{cap:,.0f} credits a month" if isinstance(cap, (int, float)) and cap else "its monthly cap"
    if where:
        return R.green(rid, f"Oneiro's capped ElevenLabs key is in {_where(where)}. Autonomous films spend only through "
                            f"it, and Slate caps them at {cap_text}.", group=G, details={"where": where, "cap": cap})
    if env.which("openclaw"):
        return R.yellow(rid, "Oneiro's capped ElevenLabs key isn't set up, so autonomous films can't voice anything yet "
                             "(supervised films are unaffected).",
                        "In ElevenLabs, create a separate key for Oneiro with a credit quota and only text-to-speech, "
                        "music and alignment access. Then run `slate connect elevenlabs --oneiro` in your own Terminal.",
                        group=G, details={"cap": cap})
    return R.green(rid, "Oneiro's capped key isn't needed here: there's no OpenClaw gateway on this Mac.", group=G)


def _higgsfield(env: Env, ctx: Ctx) -> dict:
    rid = "key.higgsfield"
    where = env.secret_where("higgsfield")
    connect = "slate connect higgsfield --api-key"
    if not where:
        return R.yellow(rid, "No Higgsfield Cloud API key. Only generated shots need it (plates are optional and paid "
                             "per generation).",
                        f"Run `{connect}` in your own Terminal (it asks for the key there, never in chat).", group=G)
    try:
        probe = env.higgsfield_probe()
    except Exception as exc:  # noqa: BLE001
        return _vendor_error(rid, "Higgsfield", exc, connect)
    status = probe.get("status")
    http = probe.get("http")
    replayed = bool(probe.get("replay"))
    note = " (replay: a recorded stand-in, not a real request)" if replayed else ""
    details = {"where": where, "probe": status, "http": http, "replay": replayed}
    if status == "ok":
        return R.green(rid, f"Higgsfield Cloud API key in {_where(where)}, accepted by the zero-cost probe (HTTP 404 "
                            f"for a request that can't exist){note}. Higgsfield has no balance endpoint, so check the "
                            "balance itself on its site.", group=G, details=details)
    if status == "no_key":
        return R.yellow(rid, "No Higgsfield Cloud API key. Only generated shots need it.",
                        f"Run `{connect}` in your own Terminal.", group=G, details=details)
    if status == "rejected":
        return R.red(rid, f"Higgsfield rejected the key (HTTP {http}){note}.",
                     f"Run `{connect}` in your own Terminal to replace it.", group=G, details=details)
    summary = str(probe.get("summary") or f"HTTP {http}")
    if status == "unreachable" and _is_tls(summary):
        return R.red(rid, f"Python couldn't verify Higgsfield's certificate, so the probe never reached it: {summary}",
                     TLS_FIX, group=G, details=details)
    if status == "unreachable":
        return R.yellow(rid, f"Couldn't reach Higgsfield for the zero-cost probe: {summary}",
                        "Check the network, then run `slate doctor` again.", group=G, details=details)
    return R.yellow(rid, f"Higgsfield's probe answered unexpectedly ({summary}){note}.",
                    "Run `slate doctor` again; if it repeats, check the key with `slate connect higgsfield --check`.",
                    group=G, details=details)


def _tls(env: Env, ctx: Ctx) -> dict:
    rid = "net.tls"
    info = env.tls_info()
    if info.get("ca_certs", 0) > 0 or info.get("capath_has_certs"):
        where = info.get("env_cert_file") or info.get("cafile") or info.get("capath")
        return R.green(rid, f"Python can verify TLS certificates ({info.get('ca_certs', 0)} CA certificates, from "
                            f"{where}; {info.get('source')})", group=G, details=info)
    missing = f" (it looks for {info.get('cafile')}, which doesn't exist)" if info.get("cafile") else ""
    return R.red(rid, "Slate's Python has no CA certificates to check TLS with" + missing + ", so every live vendor "
                      "call would fail with CERTIFICATE_VERIFY_FAILED.", TLS_FIX, group=G, details=info)
