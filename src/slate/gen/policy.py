"""When Slate generates, and what it may send a generator.

Quinn's rule (2026-09-30): generation is rare by default, and the requester decides how much. Code builds anything
designed (UI, type, data, brand graphics). Generation is for real people, real scenes and stock-style shots code
can't make, or where it genuinely adds something. Every take is judged, and ugly or AI-looking takes never ship.

So a shot generates only when:

1. the film's `generation` setting (chosen by the requester) isn't "none";
2. the film isn't a draft (drafts use no plates);
3. the shot carries a `generate` block naming a reason category from `REASONS`, and nothing designed in `contains`;
4. on "rarely" (the default), the reason is a gap code can't fill: real people, a real scene, a stock-style shot;
   "where-it-helps" also allows "adds-something" and "bridge-matte" (a generation used only for its shape, as a
   matte over real frames), each with a written `why`.

Autonomous films may generate under the same rules, but every generation waits for a person's yes (`slate gen
request-approval`, then `--approval <id>`); the ledger refuses the spend without one.

Brand material (anything but this film's own generator outputs) goes to a generator only when the brand pack allows
uploads (`grounding.uploads_allowed: true`). Text-only prompts are always fine.
"""

from __future__ import annotations

from pathlib import Path

SETTINGS = ("none", "rarely", "where-it-helps")
DEFAULT_SETTING = "rarely"
REASONS: dict[str, str] = {
    "real-people": "real-looking people, which code can't draw",
    "real-scene": "a real place or scene, which code can't draw",
    "stock": "a stock-style shot code can't make",
    "adds-something": "generation genuinely adds something code can't",
    "bridge-matte": "a generation used only for its shape, as a matte over real frames",
}
GAP_REASONS = frozenset({"real-people", "real-scene", "stock"})
WHY_REQUIRED = frozenset({"adds-something", "bridge-matte"})
# Things a generated plate never carries: they're designed, so code builds them (and plates stay atmosphere).
DESIGNED = frozenset({"ui", "app-ui", "screen", "text", "type", "typography", "copy", "logo", "wordmark", "chart",
                      "graph", "data", "numbers", "diagram", "icon", "brand-graphics", "product-ui"})
DEFAULT_TAKES = 2
MIN_TAKES = 2


def setting(film_json: dict) -> str:
    return str(film_json.get("generation") or DEFAULT_SETTING)


def mode(film_json: dict, film_dir: Path | str | None = None) -> str:
    """The mode a generation is judged in: autonomous whenever film.json or Slate's own record says the film was made
    autonomous (slate.state.autonomy, found by folder or by the film's id and creation stamp), else film.json's."""
    own = str(film_json.get("mode") or "supervised")
    try:
        from slate.state import autonomy  # noqa: PLC0415 - gen runs without film state installed
    except ImportError:
        return own
    return autonomy.film_mode(film_dir, film_json)


def requires_approval(film_json: dict) -> bool:
    """Autonomous films wait for a person's yes on every generation (Oneiro asks by iMessage)."""
    return mode(film_json) == "autonomous"


def allowed(film_json: dict, shot: dict) -> tuple[bool, str]:
    """May this shot generate? (allowed, why) — the why is written for the person reading the plan."""
    sid = str(shot.get("id") or "this shot")
    gen = setting(film_json)
    if gen not in SETTINGS:
        return False, (f"film.json says generation is {gen!r}; it must be one of {', '.join(SETTINGS)} "
                       "(the requester's choice).")
    block = shot.get("generate")
    if not isinstance(block, dict) or not block:
        return False, f"{sid} has no generate block, so code builds it (anything designed stays in code)."
    if gen == "none":
        return False, "This film's generation setting is none: the requester asked for no generated footage."
    if mode(film_json) == "draft":
        return False, "Draft films use no plates: the shot keeps its placeholder until the film leaves draft."
    reason = block.get("reason")
    if reason not in REASONS:
        return False, (f"{sid}'s generate block needs a reason category: one of {', '.join(REASONS)}. "
                       "Code builds anything designed; generation is for what code can't make.")
    designed = sorted({str(c).lower() for c in block.get("contains") or []} & DESIGNED)
    if designed:
        return False, (f"{sid} would generate {', '.join(designed)}: that's designed content, so code builds it. "
                       "Generated plates never carry UI, text, charts or logos.")
    if gen == "rarely" and reason not in GAP_REASONS:
        return False, (f"'{reason}' needs the film's generation setting to be where-it-helps. On 'rarely' (the "
                       "default) Slate generates only what code can't make: real people, real scenes, stock-style "
                       "shots.")
    why = str(block.get("why") or "").strip()
    if reason in WHY_REQUIRED and len(why) < 12:
        return False, f"{sid}'s generate block says '{reason}': write what it adds that code can't, in `why`."
    takes = block.get("takes")
    if takes is not None and (not isinstance(takes, int) or takes < MIN_TAKES):
        return False, f"{sid} asks for {takes} take(s); every generated shot is judged from at least {MIN_TAKES}."
    verdict = f"allowed on '{gen}': {REASONS[reason]}" + (f" ({why})" if why else "")
    if requires_approval(film_json):
        verdict += ". Autonomous film: each generation waits for a person's yes (slate gen request-approval)."
    return True, verdict


def uploads_allowed(brand_pack: dict) -> bool:
    """Does the brand owner allow its material to go to generators? Off unless the pack says so."""
    grounding = brand_pack.get("grounding") or {}
    return bool(isinstance(grounding, dict) and grounding.get("uploads_allowed") is True)


def is_generator_output(film_dir: Path, path: Path) -> bool:
    """A file this film already got back from the generator (a take): sending it back isn't brand material."""
    try:
        rel = Path(path).resolve().relative_to((Path(film_dir) / "plates").resolve())
    except ValueError:
        return False
    return len(rel.parts) >= 3 and rel.parts[1] == "takes"


def check_uploads(film_dir: Path, brand_pack: dict, files: list[Path]) -> list[str]:
    """Problems with sending these files to a generator (empty when it's fine)."""
    brand = brand_pack.get("name") or "this brand"
    blocked = [f for f in files if not is_generator_output(film_dir, f)]
    if blocked and not uploads_allowed(brand_pack):
        names = ", ".join(Path(f).name for f in blocked[:4]) + (" …" if len(blocked) > 4 else "")
        return [f"Brand material goes to a generator only when the brand pack allows uploads, and {brand}'s pack "
                f"doesn't ({names}). Use a text-only prompt, or ask the brand owner and set "
                "grounding.uploads_allowed in the pack."]
    return []
