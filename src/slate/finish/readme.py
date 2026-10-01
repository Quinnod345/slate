"""deliver/vN/README.md, written from the delivery's own records, never by hand.

The films wrote each delivery's README from its measurements (sound/p3/tools/delivery_readme.py), so the numbers in it
were the checks' own and nobody retyped one. Slate does the same at `slate deliver`, from:

- the manifest being written (every file, its size and sha256; every standing gate's verdict and summary);
- the balance report `slate verify` wrote for the mix (sound/mix/balance_check.json, delivered beside the mix as
  sound/balance_check.json): the voice's loudness, each family against it, and the clarity of every word;
- what changed since the last version, from this version's review packet (slate.finish.changes);
- the snapshot of the film's code and data the delivery was made from (its commit and tag).

Every number here is read from those records; the test holds the README to them.
"""

from __future__ import annotations

from pathlib import Path

from slate.finish import versions
from slate.finish.film import Film

README = "README.md"


def _num(v, unit: str = "", digits: int = 1) -> str:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return "–"
    return f"{v:.{digits}f}{unit}"


def _size(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 100_000 else f"{n / 1e3:.1f} kB"


def balance_lines(bal: dict | None, source: str | None) -> list[str]:
    """The balance against the voice, from the balance report as `slate verify` wrote it."""
    if not isinstance(bal, dict):
        return ["No balance report was written for this mix (`slate verify` writes sound/mix/balance_check.json), so "
                "there are no levels against the voice to show."]
    out = [f"From `{source}` (`slate verify`). Levels in LU against the voice (EBU R128, K-weighted); the voice "
           f"measures {_num(bal.get('voice_lufs'), ' LUFS', 2)} integrated.", ""]
    fams = bal.get("per_family") or {}
    if fams:
        out += ["| Family | Integrated | Median hit | Loudest hit | Hits |", "|---|---|---|---|---|"]
        for name, f in fams.items():
            mom = f.get("hit_momentary_lu") or {}
            out.append(f"| {name} | {_num(f.get('integrated_lu'))} | {_num(mom.get('median'))} | "
                       f"{_num(mom.get('max'))} | {f.get('hits', 0)} |")
        out.append("")
    cl = bal.get("clarity") or {}
    if cl:
        failing = cl.get("failing") or []
        out += ["| Speech check | Measured | Rule |", "|---|---|---|",
                f"| Clarity: the worst {bal.get('unit', 'word')}, voice over everything else in "
                f"{_num((cl.get('band_hz') or [0])[0] / 1000, ' kHz', 0)}–{_num((cl.get('band_hz') or [0, 0])[-1] / 1000, ' kHz', 0)} "
                f"| {_num(cl.get('min_db'), ' dB')} | ≥ {_num(cl.get('rule_db'), ' dB')} |",
                f"| Clarity: the median {bal.get('unit', 'word')} | {_num(cl.get('median_db'), ' dB')} | |",
                f"| {bal.get('unit', 'word').capitalize()}s under the clarity rule | {len(failing)} of "
                f"{cl.get('units', 0)} | 0 |"]
        for key, label in (("texture_air", "Texture air: the textures under the voice, worst word"),
                           ("never_over", "Never over: the closest anything comes to the voice while it speaks"),
                           ("accents_over_score", "Accents over the score")):
            block = bal.get(key)
            if isinstance(block, dict) and isinstance(block.get("min_db"), (int, float)):
                out.append(f"| {label} | {_num(block.get('min_db'), ' dB')} | "
                           f"{'≥ ' + _num(block.get('rule_db'), ' dB') if isinstance(block.get('rule_db'), (int, float)) else ''} |")
        out.append("")
    return out


def changes_lines(film: Film, n: int) -> list[str]:
    """What changed since the last version, from this version's review packet."""
    rec = versions.packet_record(film, n)
    if rec is None:
        return [f"There's no review packet for v{n}, so what changed since the last version wasn't recorded."]
    ch = rec.get("changes") or {}
    head = f"Since v{ch['since']}:" if ch.get("since") else "The first version:"
    return [head, ""] + [f"- {x}" for x in ch.get("summary") or ["Nothing."]]


def render(film: Film, manifest: dict, *, balance: dict | None, balance_source: str | None) -> str:
    """The README's text for a delivery about to be written (`manifest` is the manifest as it will be)."""
    n = manifest["version"]
    f = manifest["film"]
    lines = [f"# {f.get('title') or f.get('id')}, v{n}" + ("" if manifest.get("final") else " (a draft, never final)"),
             "",
             f"Delivered {str(manifest.get('delivered_at'))[:16].replace('T', ' ')} by {manifest.get('delivered_by')}: "
             f"{f.get('width')}×{f.get('height')} at {f.get('fps')} fps, {f.get('frames')} frames "
             f"({_num(f.get('seconds'), ' s', 2)}), {manifest.get('method')} master.",
             "",
             f"Every file here is {manifest['publishing']['status']} until the owner approves that exact file:",
             "",
             f"    {manifest['publishing']['approve']}",
             "",
             "This README is written by `slate deliver` from the set's own records (the manifest, the gates, the "
             "balance report and the review packet). Don't edit it by hand: deliver again instead.",
             "", "## Files", "", "| What | File | Size | sha256 |", "|---|---|---|---|"]
    for e in manifest.get("files") or []:
        lines.append(f"| {e['what']} | `{e['file']}` | {_size(int(e['bytes']))} | `{e['sha256'][:12]}…` |")
    for m in manifest.get("missing") or []:
        lines.append(f"| {m['what']} (missing) | {m.get('expected', '')} | | |")
    g = manifest.get("gates") or {}
    lines += ["", "## Standing checks", "",
              f"{'Every film gate passes' if g.get('passed') else 'Not every film gate passes'} "
              f"({len(g.get('results') or [])} gates; UNCHECKED and STALE never count as passed).", "",
              "| Gate | Verdict | Summary |", "|---|---|---|"]
    for r in g.get("results") or []:
        summary = str(r.get("summary") or "").replace("|", "/")
        lines.append(f"| {r['gate']} | {r['status']} | {summary[:160]} |")
    lines += ["", "## The balance against the voice", ""] + balance_lines(balance, balance_source)
    lines += ["## What changed", ""] + changes_lines(film, n) + [""]
    snap = manifest.get("snapshot") or {}
    lines += ["## The code and data behind it", ""]
    if snap.get("commit"):
        lines.append(f"The film folder's small files (its code and data) as they were for this delivery: commit "
                     f"`{snap['commit']}`" + (f", tagged `{snap['tag']}`" if snap.get("tag") else "") + ". "
                     f"`git diff {snap.get('tag') or snap['commit']}` in the film folder shows what's changed since.")
    else:
        lines.append(f"No snapshot: {snap.get('why') or 'the film folder has no history'}.")
    if manifest.get("why_not_final"):
        lines += ["", "## Why it isn't final", ""] + [f"- {w}" for w in manifest["why_not_final"]]
    return "\n".join(lines) + "\n"


def write(folder: Path, film: Film, manifest: dict, *, balance: dict | None, balance_source: str | None) -> Path:
    path = Path(folder) / README
    path.write_text(render(film, manifest, balance=balance, balance_source=balance_source), encoding="utf-8")
    return path
