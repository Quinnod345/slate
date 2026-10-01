"""The OpenClaw gateway's egress proxy: why a vendor can't be reached from inside it, and the two ways round it.

The gateway runs its shell behind an egress proxy that lets only the hosts in `~/.openclaw/openclaw.json`
`secrets.egressProxy.allowedHosts` through. The phase-1 doctor spike found ElevenLabs and Higgsfield weren't among them:
every live vendor call from the gateway's shell failed with "Tunnel connection failed: 403 Forbidden", which doctor
used to report as "check the network". It isn't the network. There are two fixes, and doctor names both:

1. Slate's job queue. Its launchd runner (dev.slate.jobs) works outside the gateway, and inside the gateway a vendor
   stage queues itself for it (slate.vendors.egress, the route the vendor clients take before any live call), so the
   runner only has to be loaded: once, by the owner, in their own Terminal (`slate jobs agent install`).
2. The owner's call, in their own security config (Slate never edits it): add the vendors' hosts to
   `secrets.egressProxy.allowedHosts`.

The names, hosts and config location are slate.vendors.egress's, so doctor and the route always say the same thing;
when a vendor client already refused with that route's EgressBlocked, its hint (the exact queue command) is the fix.
Doctor reads only the proxy's two settings from that file (`enabled` and `allowedHosts`, which are host names); nothing
else in it is read into a report.
"""

from __future__ import annotations

from pathlib import Path

from slate.doctor import rows as R
from slate.doctor.common import Ctx
from slate.doctor.env import Env
from slate.vendors import egress as gateway

G = "keys"
CONFIG = gateway.DEFAULT_CONFIG
SETTING = gateway.ALLOWLIST_KEY
QUEUE_LABEL = "dev.slate.jobs"
# The hosts each vendor's API answers on (their downloads may come from other hosts too, which the fix says).
VENDOR_HOSTS = {gateway.NAMES[k]: (host,) for k, host in gateway.HOSTS.items()}
PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")


def config_path(env: Env) -> Path:
    """Where the gateway's config is read from: SLATE_OPENCLAW_CONFIG when set (as the route reads it), else
    ~/.openclaw/openclaw.json."""
    named = env.getenv(gateway.CONFIG_ENV)
    if named:
        return Path(named).expanduser()
    return env.home / ".openclaw" / "openclaw.json"


def allows(allowed: list[str], host: str) -> bool:
    """Does an allowlist let `host` through? The route's own rule: an exact host, or a `*.domain` / `.domain` entry."""
    host = str(host or "").strip().lower()
    for entry in allowed:
        if entry == host or (entry.startswith("*.") and host.endswith(entry[1:])) or (
                entry.startswith(".") and host.endswith(entry)):
            return True
    return False


def proxy(env: Env, ctx: Ctx) -> dict:
    """{gateway, proxied, enabled, allowed}: is this run inside the OpenClaw gateway or behind a proxy, and what the
    gateway's egress proxy lets through (None when its config can't be read)."""
    data = env.read_json(config_path(env))
    egress = None
    if isinstance(data, dict):
        sec = data.get("secrets")
        egress = sec.get("egressProxy") if isinstance(sec, dict) else None
    enabled = egress.get("enabled") if isinstance(egress, dict) else None
    allowed = egress.get("allowedHosts") if isinstance(egress, dict) else None
    allowed = [str(h).strip().lower() for h in allowed if isinstance(h, str)] if isinstance(allowed, list) else None
    return {"gateway": ctx.agent == "openclaw", "proxied": any(env.getenv(v) for v in PROXY_VARS),
            "enabled": enabled if isinstance(enabled, bool) else None, "allowed": allowed}


def runner_loaded(env: Env) -> bool | None:
    """Is the job queue's launchd runner loaded (the queue route needs it)? None when its state can't be read."""
    try:
        st = env.jobs_agent()
    except Exception:  # noqa: BLE001 - unknown, not a fault of this row
        return None
    return bool(st.get("loaded")) if isinstance(st, dict) else None


def blocked_text(text: str | None) -> bool:
    """Does an error read like the egress proxy refusing a host (its tunnel answering 403)?"""
    low = (text or "").lower()
    return "tunnel connection failed" in low or ("proxy" in low and "403" in low) or "egress proxy" in low


def applies(env: Env, ctx: Ctx, text: str | None = None) -> bool:
    """Should a vendor row that couldn't reach its vendor explain the egress proxy instead of the network?"""
    return blocked_text(text) or ctx.agent == "openclaw"


def hosts_for(vendor: str) -> tuple[str, ...]:
    return VENDOR_HOSTS.get(vendor, ())


def _queue_words(runner: bool | None) -> str:
    """The queue route, said for the runner's state."""
    route = (f"Slate's job queue, whose launchd runner ({QUEUE_LABEL}) works outside the gateway: inside the gateway a "
             "vendor stage queues itself for it and stops with the job's id (`slate job wait <id>` follows it); "
             "by hand, `slate job submit --kind … --resource … -- slate <the command>` or the stage's own --detach "
             "does the same")
    if runner is True:
        return route + ". The runner is loaded on this Mac."
    if runner is False:
        return route + (". The runner isn't loaded yet: load it once, in your own Terminal, with "
                        "`slate jobs agent install`.")
    return route + " (it has to be loaded: `slate jobs agent install`, once, in your own Terminal)."


def explain(vendor: str, *, text: str | None = None, runner: bool | None = None,
            hint: str | None = None) -> tuple[str, str]:
    """(summary, fix) for a vendor the gateway's egress proxy keeps out: both fixes, named exactly. `hint` is the
    route's own next step when the vendor client already refused with it (its exact queue command); it leads the fix."""
    hosts = hosts_for(vendor)
    listed = " and ".join(hosts) or f"{vendor}'s API"
    tunnel = " (its tunnel answered 403 Forbidden)" if "tunnel connection failed" in (text or "").lower() else ""
    summary = (f"{vendor} can't be reached from inside the OpenClaw gateway{tunnel}: the gateway's egress proxy lets "
               f"only the hosts in {CONFIG} {SETTING} through, and {listed} isn't one of them. Nothing was sent or "
               "billed; paid stages can't run in the gateway's own shell.")
    fix = (f"Either use {_queue_words(runner)} Or, your call, add {listed} (and the hosts its downloads come from) to "
           f"{SETTING} in {CONFIG}. Slate never edits that file.")
    if hint and hint.strip():
        fix = hint.strip()
        if runner is True:
            fix += f" The job queue's runner ({QUEUE_LABEL}) is loaded on this Mac."
        elif runner is False and "slate jobs agent install" not in fix:
            fix += (f" The job queue's runner ({QUEUE_LABEL}) isn't loaded yet: load it once, in your own Terminal, "
                    "with `slate jobs agent install`.")
        if SETTING not in fix:
            fix += f" Or, your call, add {listed} to {SETTING} in {CONFIG}. Slate never edits that file."
    return summary, fix


def from_route(exc_or_probe) -> tuple[bool, str | None]:
    """(blocked, hint) when a vendor client refused with the gateway route's EgressBlocked (an exception, or a probe
    result carrying `egress` and `hint`)."""
    if isinstance(exc_or_probe, dict):
        eg = exc_or_probe.get("egress")
        return bool(isinstance(eg, dict) and eg.get("egress_proxy")), exc_or_probe.get("hint")
    details = getattr(exc_or_probe, "details", None) or {}
    blocked = isinstance(exc_or_probe, gateway.EgressBlocked) or bool(details.get("egress_proxy"))
    return blocked, getattr(exc_or_probe, "hint", None) if blocked else None


def check_egress(env: Env, ctx: Ctx) -> list[dict]:
    """Inside the OpenClaw gateway (or behind a proxy): does its egress proxy let the vendors through? Elsewhere the
    proxy doesn't apply (it covers only the gateway's own shell), and the row says so, so every context reports the
    same rows."""
    st = proxy(env, ctx)
    rid = "net.egress-proxy"
    details = {"gateway": st["gateway"], "proxied": st["proxied"], "enabled": st["enabled"],
               "config": CONFIG, "setting": SETTING}
    if not st["gateway"] and not st["proxied"]:
        return [R.green(rid, "Not inside the OpenClaw gateway and not behind a proxy: vendors are reached directly "
                             "(the gateway's egress proxy covers only its own shell).", group=G, details=details)]
    if st["enabled"] is False:
        return [R.green(rid, f"The OpenClaw gateway's egress proxy is off ({CONFIG}), so vendors are reached "
                             "directly.", group=G, details=details)]
    runner = runner_loaded(env)
    details["runner_loaded"] = runner
    if st["allowed"] is None:
        return [R.yellow(rid, f"This run is behind a proxy, and the OpenClaw gateway's egress proxy settings couldn't "
                              f"be read from {CONFIG}, so whether ElevenLabs and Higgsfield get through is unknown "
                              "(the vendor stages count it as closed).",
                         f"Use {_queue_words(runner)} Or add the vendors' hosts to {SETTING} in {CONFIG} yourself. "
                         "Slate never edits that file.", group=G, details=details)]
    missing = {v: [h for h in hosts if not allows(st["allowed"], h)] for v, hosts in VENDOR_HOSTS.items()}
    missing = {v: hs for v, hs in missing.items() if hs}
    details["missing"] = missing
    if not missing:
        return [R.green(rid, f"The OpenClaw gateway's egress proxy lets ElevenLabs and Higgsfield through "
                             f"({SETTING} lists their API hosts).", group=G, details=details)]
    vendors = " and ".join(missing)
    hosts = ", ".join(h for hs in missing.values() for h in hs)
    return [R.yellow(rid, f"The OpenClaw gateway's egress proxy keeps {vendors} out ({hosts} isn't in {SETTING}), so "
                          "paid stages can't run in the gateway's own shell.",
                     f"Either use {_queue_words(runner)} Or, your call, add {hosts} (and the hosts their downloads "
                     f"come from) to {SETTING} in {CONFIG}. Slate never edits that file.", group=G, details=details)]
