"""Reading a brand's own material where it lives: app code, a website, a guidelines PDF.

Onboarding builds a brand's look and motion from the brand's own design language (docs/PLAN.md §4): its colour and
type tokens, and the motion values its product already uses. This module finds those values and cites each one
(`path:line`). It **never copies** anything: company source code is read in place, and what reaches the pack is values
and citations, never code (docs/PLAN.md §7, Protections: "company source code is read in place, never copied").

- `read_app(path)`: a folder of website or app code (CSS and its preprocessors, JS/TS theme files, SwiftUI views,
  asset catalogs). Skips dependencies and builds. The source is pinned to its git commit, read from `.git` itself.
- `read_file_text(text, name)`: one file's text from anywhere else, for code that lives on another Mac: it arrives on
  stdin (`peer cat … | slate brand onboard … --stdin-as <path>`) and is never written to disk.
- `read_website(url)`: a page's HTML and stylesheets over HTTPS (no browser, no JavaScript), or a saved copy of the page.
  Replay mode (`SLATE_FAKE_VENDORS=1`) answers from recorded fixtures only, so tests stay offline.
- `read_pdf(path)`: a guidelines PDF's text through macOS's PDFKit (or poppler's pdftotext), read in place.
- `peer_procedure(spec, brand)`: for `work:<path>`, the read-only steps a person runs through the `peer` tool.

Everything found is a *candidate*: a person (or the agent onboarding with them) decides which values become the
brand's tokens and eases, and the look-and-motion approval fixes them.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any, Iterator

from slate import jsonio, log, procs, replay
from slate.brand import colour, easing
from slate.errors import MissingPrerequisite, SlateError, UsageError

SKIP_DIRS = frozenset({"node_modules", ".git", "dist", "build", ".next", ".nuxt", ".svelte-kit", ".turbo", ".cache",
                       "coverage", "Pods", "DerivedData", "Carthage", ".build", "vendor", "__pycache__", ".venv",
                       "venv", "out", "public/build", ".output", "storybook-static"})
CSS_EXTS = frozenset({".css", ".scss", ".sass", ".less", ".pcss", ".styl"})
SCRIPT_EXTS = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte", ".json"})
SWIFT_EXTS = frozenset({".swift"})
HTML_EXTS = frozenset({".html", ".htm"})
MAX_FILES = 6000
MAX_BYTES = 2 << 20
FONT_FILE = re.compile(r"\.(woff2?|otf|ttf|ttc|eot|dfont)(\?|#|$)", re.IGNORECASE)
GENERIC_FONTS = frozenset({"serif", "sans-serif", "monospace", "system-ui", "ui-sans-serif", "ui-serif",
                           "ui-monospace", "ui-rounded", "cursive", "fantasy", "inherit", "initial", "unset",
                           "-apple-system", "blinkmacsystemfont", "emoji", "math", "fangsong", "revert"})

# --------------------------------------------------------------------------------------------------- findings
def _empty() -> dict:
    return {"colours": [], "aliases": [], "eases": [], "durations": [], "fonts": [], "usage": [], "problems": []}


def merge(into: dict, more: dict) -> dict:
    for key, value in more.items():
        if isinstance(value, list):
            into.setdefault(key, []).extend(value)
    return into


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _token_name(prop: str) -> str:
    """A token's name without its sigil: `--accent`, `$accent` and `@accent` are all "accent"."""
    return prop.strip().removeprefix("--").lstrip("$@")


_DURATION_NAME = re.compile(r"(dur|duration|time|timing|speed|delay|motion|anim|stagger)", re.IGNORECASE)
_EASE_NAME = re.compile(r"(ease|easing|curve|spring|timing|bezier|motion)", re.IGNORECASE)
_CUSTOM_PROP = re.compile(r"(?P<name>(?:--|\$|@)[A-Za-z][A-Za-z0-9_-]*)\s*:\s*(?P<value>[^;{}]+?)\s*(?=;|}|$)",
                          re.MULTILINE)
_FONT_FAMILY = re.compile(r"font-family\s*:\s*(?P<value>[^;{}]+)", re.IGNORECASE)
_FONT_FACE = re.compile(r"@font-face\s*{(?P<body>[^}]*)}", re.IGNORECASE)
_TIMING = re.compile(r"(?:transition|animation)(?:-timing-function)?\s*:\s*(?P<value>[^;{}]+)", re.IGNORECASE)
_BEZIER = re.compile(r"cubic-bezier\(\s*[-\d.\s,]+\)", re.IGNORECASE)
_KEYWORD_EASE = re.compile(r"(?<![\w-])(ease-in-out|ease-out|ease-in|ease)(?![\w-])", re.IGNORECASE)


def _first_family(value: str) -> str | None:
    for part in value.split(","):
        name = part.strip().strip("'\"").strip()
        if not name or name.lower() in GENERIC_FONTS or name.startswith(("var(", "$", "@")):
            continue
        return name
    return None


def scan_css(text: str, where: str) -> dict:
    """Colour, easing and duration tokens (custom properties), font families, and the curves rules actually use."""
    out = _empty()
    for m in _CUSTOM_PROP.finditer(text):
        name, value = _token_name(m.group("name")), " ".join(m.group("value").split())
        at = f"{where}:{_line(text, m.start())}"
        if m.group("name").startswith("@") and name.lower() in ("media", "import", "font-face", "keyframes", "supports",
                                                                 "layer", "tailwind", "apply", "use", "forward"):
            continue
        var = re.fullmatch(r"(?:var\(\s*(--[A-Za-z0-9_-]+)\s*(?:,[^)]*)?\)|[$@]([A-Za-z][A-Za-z0-9_-]*))", value)
        if var:
            out["aliases"].append({"name": name, "target": _token_name(var.group(1) or var.group(2)), "where": at})
            continue
        hexed = colour.normalise(value)
        if hexed:
            out["colours"].append({"name": name, "value": hexed, "raw": value, "where": at, "via": "css token"})
            continue
        seconds = easing.css_time(value)
        if seconds is not None and _DURATION_NAME.search(name):
            out["durations"].append({"name": name, "seconds": seconds, "raw": value, "where": at})
            continue
        if _BEZIER.fullmatch(value) or value.lower().startswith("linear(") or (
                value.lower() in easing.CSS_KEYWORDS and _EASE_NAME.search(name)):
            try:
                out["eases"].append({"name": name, "via": "css token",
                                     **easing.ease_from_css(value, name=f"--{name}", where=at)})
            except easing.EasingError as exc:
                out["problems"].append(f"{at}: --{name}: {exc}")
            continue
        if re.search(r"font|family|typeface", name, re.IGNORECASE):
            family = _first_family(value)
            if family and not re.match(r"^[\d.]+(px|rem|em|%)?$", family):
                out["fonts"].append({"family": family, "where": at, "via": "font token"})
    for m in _FONT_FACE.finditer(text):
        body = m.group("body")
        fam = re.search(r"font-family\s*:\s*([^;]+)", body, re.IGNORECASE)
        src = re.findall(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", body)
        weight = re.search(r"font-weight\s*:\s*([^;]+)", body, re.IGNORECASE)
        if fam:
            family = fam.group(1).strip().strip("'\"")
            out["fonts"].append({"family": family, "where": f"{where}:{_line(text, m.start())}", "via": "@font-face",
                                 "weights": [weight.group(1).strip()] if weight else [],
                                 "served_as_files": any(FONT_FILE.search(s) for s in src),
                                 "note": "served as font files by this code: the pack names the font by reference "
                                         "(installed, Google Fonts or the type service's kit), never the files"})
    for m in _FONT_FAMILY.finditer(text):
        family = _first_family(m.group("value"))
        if family:
            out["fonts"].append({"family": family, "where": f"{where}:{_line(text, m.start())}", "via": "font-family"})
    for m in _TIMING.finditer(text):
        value = m.group("value")
        at = f"{where}:{_line(text, m.start())}"
        for b in _BEZIER.findall(value):
            try:
                parsed = easing.css_easing(b)
                out["usage"].append({"bezier": parsed["bezier"], "css": b, "where": at})
            except easing.EasingError as exc:
                out["problems"].append(f"{at}: {exc}")
        for k in _KEYWORD_EASE.findall(value):
            out["usage"].append({"bezier": list(easing.CSS_KEYWORDS[k.lower()]), "css": k.lower(), "where": at})
    return out


_JS_BEZIER_KV = re.compile(r"(?P<q>['\"]?)(?P<name>[A-Za-z0-9_-]+)(?P=q)\s*:\s*['\"`](?P<value>cubic-bezier\([^)]*\))['\"`]")
_JS_ARRAY_EASE = re.compile(r"(?P<q>['\"]?)(?P<name>[A-Za-z0-9_]+)(?P=q)\s*[:=]\s*\[\s*(?P<nums>-?[\d.]+\s*,\s*-?[\d.]+\s*,"
                            r"\s*-?[\d.]+\s*,\s*-?[\d.]+)\s*\]")
_JS_COLOUR_KV = re.compile(r"(?P<q>['\"]?)(?P<name>[A-Za-z0-9_-]+)(?P=q)\s*:\s*['\"`](?P<value>#[0-9A-Fa-f]{3,8}|"
                           r"(?:rgba?|hsla?|oklch|oklab)\([^)]*\))['\"`]")
_JS_FONT = re.compile(r"(?:fontFamily|font-family|family)\s*[:=]\s*[\[{]?\s*['\"`](?P<family>[^'\"`,]+)['\"`]")


def scan_script(text: str, where: str) -> dict:
    """JS/TS/JSON theme files: named cubic-bezier strings, easing arrays ([x1, y1, x2, y2]), named colours, fonts."""
    out = _empty()
    for m in _JS_BEZIER_KV.finditer(text):
        at = f"{where}:{_line(text, m.start())}"
        try:
            out["eases"].append({"name": m.group("name"), "via": "theme value",
                                 **easing.ease_from_css(m.group("value"), name=m.group("name"), where=at)})
        except easing.EasingError as exc:
            out["problems"].append(f"{at}: {m.group('name')}: {exc}")
    for m in _JS_ARRAY_EASE.finditer(text):
        name = m.group("name")
        if not _EASE_NAME.search(name) and name.lower() not in ("enter", "exit", "arrive", "leave", "snap", "glide"):
            continue
        at = f"{where}:{_line(text, m.start())}"
        nums = [float(x) for x in re.split(r"\s*,\s*", m.group("nums"))]
        try:
            out["eases"].append({"name": name, "via": "theme value",
                                 **easing.ease_from_css(f"cubic-bezier({', '.join(map(str, nums))})", name=name,
                                                        where=at)})
        except easing.EasingError as exc:
            out["problems"].append(f"{at}: {name}: {exc}")
    for m in _JS_COLOUR_KV.finditer(text):
        hexed = colour.normalise(m.group("value"))
        if hexed:
            out["colours"].append({"name": m.group("name"), "value": hexed, "raw": m.group("value"),
                                   "where": f"{where}:{_line(text, m.start())}", "via": "theme value"})
    for m in _JS_FONT.finditer(text):
        family = _first_family(m.group("family"))
        if family:
            out["fonts"].append({"family": family, "where": f"{where}:{_line(text, m.start())}", "via": "theme"})
    return out


_SWIFT_ANIM = re.compile(
    r"(?P<call>(?:\bSpring\s*\([^()]*\))|(?:(?:\bAnimation)?\.(?:spring|interactiveSpring|interpolatingSpring|smooth|"
    r"snappy|bouncy|timingCurve|easeInOut|easeIn|easeOut|linear)\b(?:\s*\([^()]*\))?))")
_SWIFT_NAME = re.compile(r"(?:let|var)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*[A-Za-z0-9_.<>?]+)?\s*=\s*$")
_SWIFT_RGB = re.compile(r"Color\(\s*(?:\.sRGB\s*,\s*)?red:\s*(?P<r>[\d.]+)\s*,\s*green:\s*(?P<g>[\d.]+)\s*,\s*"
                        r"blue:\s*(?P<b>[\d.]+)(?:\s*,\s*opacity:\s*(?P<a>[\d.]+))?")
_SWIFT_HEX = re.compile(r"Color\(\s*hex:\s*(?:\"(?P<s>#?[0-9A-Fa-f]{6,8})\"|0x(?P<n>[0-9A-Fa-f]{6,8}))")
_SWIFT_FONT = re.compile(r"(?:\.custom|Font\.custom|UIFont\(name:)\s*\(?\s*\"(?P<name>[^\"]+)\"")


def _swift_name(text: str, pos: int) -> str | None:
    start = text.rfind("\n", 0, pos) + 1
    m = _SWIFT_NAME.search(text[start:pos])
    return m.group("name") if m else None


def scan_swift(text: str, where: str) -> dict:
    """SwiftUI: animations and springs (converted to named eases), colours and custom fonts."""
    out = _empty()
    seen: set[int] = set()
    for m in _SWIFT_ANIM.finditer(text):
        call = m.group("call")
        if m.start() in seen:
            continue
        seen.add(m.start())
        line = _line(text, m.start())
        at = f"{where}:{line}"
        named = _swift_name(text, m.start())
        name = named or f"swift-{Path(where).stem}-{line}"
        try:
            out["eases"].append({"name": name, "via": "SwiftUI named animation" if named else "SwiftUI animation",
                                 **easing.swift_animation(call, where=at)})
        except easing.EasingError as exc:
            out["problems"].append(f"{at}: {exc}")
    for m in _SWIFT_RGB.finditer(text):
        rgba = [float(m.group(k)) for k in ("r", "g", "b")] + [float(m.group("a") or 1.0)]
        hexed = colour.to_hex((rgba[0] * 255, rgba[1] * 255, rgba[2] * 255, rgba[3]))
        out["colours"].append({"name": _swift_name(text, m.start()) or f"colour-{_line(text, m.start())}",
                               "value": hexed, "raw": m.group(0), "where": f"{where}:{_line(text, m.start())}",
                               "via": "SwiftUI Color"})
    for m in _SWIFT_HEX.finditer(text):
        raw = m.group("s") or m.group("n")
        hexed = colour.normalise("#" + raw.lstrip("#"))
        if hexed:
            out["colours"].append({"name": _swift_name(text, m.start()) or f"colour-{_line(text, m.start())}",
                                   "value": hexed, "raw": m.group(0), "where": f"{where}:{_line(text, m.start())}",
                                   "via": "SwiftUI Color(hex:)"})
    for m in _SWIFT_FONT.finditer(text):
        out["fonts"].append({"family": m.group("name"), "where": f"{where}:{_line(text, m.start())}",
                             "via": "SwiftUI custom font", "note": "may be a PostScript name; check the family"})
    return out


def _component(v: Any) -> float | None:
    s = str(v).strip()
    try:
        if s.lower().startswith("0x"):
            return int(s, 16) / 255.0
        f = float(s)
    except ValueError:
        return None
    return f / 255.0 if f > 1.0 else f


def scan_colorset(contents: Path, where: str) -> dict:
    """An asset catalog colour (`Name.colorset/Contents.json`), with its dark appearance as `<name>-dark`."""
    out = _empty()
    try:
        data = json.loads(contents.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        out["problems"].append(f"{where}: {exc}")
        return out
    base = contents.parent.name.removesuffix(".colorset")
    for entry in data.get("colors") or []:
        comps = ((entry.get("color") or {}).get("components")) or {}
        vals = [_component(comps.get(k)) for k in ("red", "green", "blue")]
        if any(v is None for v in vals):
            continue
        alpha = _component(comps.get("alpha", 1.0)) or 1.0
        dark = any(a.get("value") == "dark" for a in entry.get("appearances") or [])
        out["colours"].append({"name": base + ("-dark" if dark else ""),
                               "value": colour.to_hex((vals[0] * 255, vals[1] * 255, vals[2] * 255, alpha)),  # type: ignore[operator]
                               "where": where, "via": "asset catalog"})
    return out


def read_file_text(text: str, name: str) -> dict:
    """Scan one file's text by its name's extension (CSS, script, Swift or HTML)."""
    ext = Path(name).suffix.lower()
    if ext in CSS_EXTS:
        return scan_css(text, name)
    if ext in SWIFT_EXTS:
        return scan_swift(text, name)
    if ext in HTML_EXTS:
        found = _empty()
        for block in re.findall(r"<style[^>]*>(.*?)</style>", text, re.DOTALL | re.IGNORECASE):
            merge(found, scan_css(block, name))
        return found
    if ext in SCRIPT_EXTS:
        return merge(scan_script(text, name), scan_css(text, name) if "--" in text else _empty())
    return _empty()


# ------------------------------------------------------------------------------------------------------ git pins
def _gitdir(start: Path) -> Path | None:
    for folder in (start, *start.parents):
        g = folder / ".git"
        if g.is_dir():
            return g
        if g.is_file():
            m = re.match(r"gitdir:\s*(.+)", g.read_text(encoding="utf-8").strip())
            if m:
                return (folder / m.group(1).strip()).resolve()
    return None


def _common(gitdir: Path) -> Path:
    cd = gitdir / "commondir"
    return (gitdir / cd.read_text(encoding="utf-8").strip()).resolve() if cd.is_file() else gitdir


def git_head(path: str | os.PathLike) -> str | None:
    """The commit a folder's code is at, read from `.git` itself (no git process). None outside a repository."""
    gitdir = _gitdir(Path(path).resolve())
    if gitdir is None or not (gitdir / "HEAD").is_file():
        return None
    head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
    if not head.startswith("ref:"):
        return head or None
    ref = head[4:].strip()
    common = _common(gitdir)
    for base in (gitdir, common):
        f = base / ref
        if f.is_file():
            return f.read_text(encoding="utf-8").strip()
    packed = common / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + ref):
                return line.split()[0]
    return None


def git_remotes(path: str | os.PathLike) -> list[str]:
    """The remotes a folder's repository names (read from its config), e.g. ["origin"]. [] when it has none."""
    gitdir = _gitdir(Path(path).resolve())
    if gitdir is None:
        return []
    cfg = _common(gitdir) / "config"
    if not cfg.is_file():
        return []
    return re.findall(r'^\s*\[remote\s+"([^"]+)"\]', cfg.read_text(encoding="utf-8", errors="replace"), re.MULTILINE)


# -------------------------------------------------------------------------------------------------------- app code
def _walk(root: Path) -> Iterator[Path]:
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
                             and f"{rel_dir}/{d}".lstrip("./") not in SKIP_DIRS)
        for name in sorted(filenames):
            count += 1
            if count > MAX_FILES:
                return
            yield Path(dirpath) / name


def read_app(path: str | os.PathLike, *, kind: str = "app") -> dict:
    """Read a folder (or one file) of the brand's own code in place. Returns {source, found, files_read, problems}."""
    if is_work_path(str(path)):
        raise UsageError(f"{path} is on the WORK Mac; Slate never copies company code, so it can't read it from here.",
                         hint="Follow the read-only procedure `slate brand onboard <name> --step look --from-app "
                              f"{path}` prints.")
    root = Path(path).expanduser()
    if not root.exists():
        raise UsageError(f"{root} doesn't exist.")
    root = root.resolve()
    found = _empty()
    files = [root] if root.is_file() else list(_walk(root))
    read = 0
    for f in files:
        rel = f.name if root.is_file() else f.relative_to(root).as_posix()
        try:
            if f.name == "Contents.json" and f.parent.name.endswith(".colorset"):
                merge(found, scan_colorset(f, rel))
                read += 1
                continue
            ext = f.suffix.lower()
            if ext not in CSS_EXTS | SCRIPT_EXTS | SWIFT_EXTS | HTML_EXTS:
                continue
            if f.stat().st_size > MAX_BYTES:
                found["problems"].append(f"{rel}: larger than {MAX_BYTES >> 20} MB, skipped")
                continue
            merge(found, read_file_text(f.read_text(encoding="utf-8", errors="replace"), rel))
            read += 1
        except OSError as exc:
            found["problems"].append(f"{rel}: {exc}")
    commit = git_head(root if root.is_dir() else root.parent)
    source = {"id": _source_id(kind, root.name), "kind": kind, "where": str(root), "read": "in_place",
              "pin": commit or f"read {_today()} (not a git repository)", "files_read": read}
    if len(files) >= MAX_FILES:
        found["problems"].append(f"stopped after {MAX_FILES} files; point at the folder that holds the tokens")
    return {"source": source, "found": found, "files_read": read, "problems": found["problems"]}


def _today() -> str:
    return _dt.date.today().isoformat()


def _source_id(kind: str, name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32] or "source"
    return f"{kind}-{slug}"


# ------------------------------------------------------------------------------------------------------- website
def _google_families(url: str) -> list[str]:
    q = urllib.parse.urlparse(url).query
    fams = []
    for value in urllib.parse.parse_qs(q).get("family", []):
        for fam in value.split("|"):
            fams.append(fam.split(":")[0].replace("+", " ").strip())
    return [f for f in fams if f]


def _links(html: str, base: str) -> tuple[list[str], list[str]]:
    """(stylesheets, Google Fonts families) linked from a page."""
    sheets, google = [], []
    for tag in re.findall(r"<link\b[^>]*>", html, re.IGNORECASE):
        rel = re.search(r"rel\s*=\s*['\"]?([^'\" >]+)", tag, re.IGNORECASE)
        href = re.search(r"href\s*=\s*['\"]([^'\"]+)['\"]", tag, re.IGNORECASE)
        if not href:
            continue
        url = urllib.parse.urljoin(base, href.group(1))
        rels = (rel.group(1).lower() if rel else "")
        if "fonts.googleapis.com" in url:
            google += _google_families(url)
        elif "stylesheet" in rels or url.lower().split("?")[0].endswith(".css"):
            sheets.append(url)
    for imp in re.findall(r"@import\s+(?:url\()?\s*['\"]?([^'\")\s;]+)", html, re.IGNORECASE):
        url = urllib.parse.urljoin(base, imp)
        if "fonts.googleapis.com" in url:
            google += _google_families(url)
        else:
            sheets.append(url)
    return list(dict.fromkeys(sheets)), list(dict.fromkeys(google))


def is_font_kit(url: str, css: str, page_url: str) -> bool:
    """A type service's kit: a stylesheet from another host that holds nothing but @font-face rules. Its URL is how a
    pack names those fonts by reference (source: {kind: kit, url})."""
    here, there = urllib.parse.urlparse(page_url).netloc, urllib.parse.urlparse(url).netloc
    if not there or there == here or "@font-face" not in css.lower():
        return False
    rest = _FONT_FACE.sub("", re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL))
    return len(re.sub(r"\s+", "", rest)) < 200


def _get(url: str) -> str:
    from slate import http  # noqa: PLC0415

    _, _, body = http.request("GET", url, headers={"Accept": "text/html,text/css,*/*"}, timeout=30, retries=1)
    if len(body) > MAX_BYTES * 2:
        body = body[: MAX_BYTES * 2]
    return body.decode("utf-8", errors="replace")


def _fetch_live(url: str, max_sheets: int = 12) -> dict:
    html = _get(url)
    sheets, _ = _links(html, url)
    css: dict[str, str] = {}
    for s in sheets[:max_sheets]:
        if not s.startswith(("http://", "https://")):
            continue
        try:
            css[s] = _get(s)
        except SlateError as exc:
            log.warn(f"couldn't read {s}: {exc.message}")
    return {"url": url, "html": html, "stylesheets": css}


def fetch_website(url: str) -> dict:
    """{url, html, stylesheets: {url: css}}: live over HTTPS, or from a recorded fixture in replay mode."""
    if replay.fake_vendors():
        try:
            data = replay.respond("web", "fetch", {"url": url})
        except replay.ReplayMissing:
            raise MissingPrerequisite(
                f"Replay mode (SLATE_FAKE_VENDORS=1) doesn't fetch websites, and there's no recorded copy of {url}.",
                hint="Run it again with --live (fetching a page costs nothing), or pass a saved copy of the page "
                     "(its folder or its .html file).") from None
        return {"url": url, "html": data.get("html") or "", "stylesheets": data.get("stylesheets") or {}}
    data = _fetch_live(url)
    if replay.recording():
        replay.record("web", replay.request_key("web", "fetch", {"url": url}), data)
    return data


def read_website(url_or_path: str) -> dict:
    """A website's tokens from its HTML and stylesheets (no browser, no JavaScript), or from a saved copy of a page."""
    target = str(url_or_path)
    if target.startswith(("http://", "https://")):
        data = fetch_website(target)
        source = {"id": _source_id("website", urllib.parse.urlparse(target).netloc), "kind": "website",
                  "where": target, "read": "fetched", "pin": f"fetched {_today()}"}
    else:
        p = Path(target).expanduser()
        page = p / "index.html" if p.is_dir() else p
        if not page.is_file():
            raise UsageError(f"{target} isn't a URL, a saved page or a folder with an index.html.")
        html = page.read_text(encoding="utf-8", errors="replace")
        css: dict[str, str] = {}
        sheets, _ = _links(html, page.as_uri())
        for s in sheets:
            local = Path(urllib.parse.unquote(urllib.parse.urlparse(s).path)) if s.startswith("file:") else None
            if local and local.is_file():
                css[str(local.relative_to(page.parent)) if local.is_relative_to(page.parent) else str(local)] = \
                    local.read_text(encoding="utf-8", errors="replace")
        data = {"url": str(page), "html": html, "stylesheets": css}
        source = {"id": _source_id("website", page.parent.name), "kind": "website", "where": str(page.resolve()),
                  "read": "in_place", "pin": git_head(page.parent) or f"read {_today()}"}
    found = _empty()
    _, google = _links(data["html"], data["url"])
    for fam in google:
        found["fonts"].append({"family": fam, "source": "google", "where": data["url"], "via": "Google Fonts link"})
    merge(found, read_file_text(data["html"], "page.html"))
    for name, css in (data.get("stylesheets") or {}).items():
        label = urllib.parse.urlparse(name).path.rsplit("/", 1)[-1] or name
        sheet = scan_css(css, label)
        if is_font_kit(name, css, data["url"]):  # a type service's kit names its families by reference
            for f in sheet["fonts"]:
                f["source"] = {"kind": "kit", "url": name}
                f["note"] = "served by a type service's kit: the pack names it by this stylesheet's URL"
        merge(found, sheet)
    return {"source": source, "found": found, "files_read": 1 + len(data.get("stylesheets") or {}),
            "problems": found["problems"]}


# ----------------------------------------------------------------------------------------------------------- PDF
_JXA = """
ObjC.import('PDFKit');
function run(argv) {
  var doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(argv[0]));
  if (!doc || doc.isNil()) { return JSON.stringify({error: 'PDFKit could not open the file'}); }
  var pages = [];
  for (var i = 0; i < doc.pageCount; i++) {
    var s = doc.pageAtIndex(i).string;
    pages.push(s && !s.isNil() ? ObjC.unwrap(s) : '');
  }
  return JSON.stringify({pages: pages});
}
"""


def pdf_text(path: str | os.PathLike) -> tuple[list[str], str]:
    """(one string per page, the engine that read it). PDFKit through osascript, else pdftotext."""
    p = Path(path).expanduser().resolve()
    if procs.which("osascript"):
        proc = procs.run(["osascript", "-l", "JavaScript", "-e", _JXA, str(p)], timeout=120)
        if proc.returncode == 0:
            try:
                data = json.loads((proc.stdout or "").strip() or "{}")
            except json.JSONDecodeError:
                data = {}
            if isinstance(data.get("pages"), list):
                return [str(x) for x in data["pages"]], "macOS PDFKit"
    if procs.which("pdftotext"):
        proc = procs.run(["pdftotext", "-layout", str(p), "-"], timeout=120)
        if proc.returncode == 0:
            return (proc.stdout or "").split("\f"), "pdftotext"
    raise MissingPrerequisite("Nothing on this Mac can read PDF text (macOS PDFKit through osascript, or pdftotext).",
                              hint="Install poppler (`brew install poppler`) for pdftotext.")


_PDF_HEX = re.compile(r"#\s?([0-9A-Fa-f]{6})\b")
_PDF_RGB = re.compile(r"\bR\s*G\s*B\s*:?\s*(\d{1,3})\s*[,/ ]\s*(\d{1,3})\s*[,/ ]\s*(\d{1,3})\b", re.IGNORECASE)
_PDF_RGB2 = re.compile(r"\bR\s*(\d{1,3})\s+G\s*(\d{1,3})\s+B\s*(\d{1,3})\b")
_PDF_WEIGHT = re.compile(r"\b([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)?)\s+(Thin|Hairline|Light|Regular|Book|Medium|"
                         r"Semi ?[Bb]old|Bold|Extra ?[Bb]old|Black|Heavy)\b")
_NOT_FAMILY = frozenset({"The", "This", "Use", "Our", "Primary", "Secondary", "Headings", "Body", "Font", "Type",
                         "Typeface", "Colour", "Color", "Logo", "Brand", "Text", "Copy", "Headline"})


def _label_before(line: str, pos: int) -> str:
    words = re.sub(r"(?i)\b(hex|rgb|cmyk|pms|pantone)\b[:]?", " ", line[:pos]).split()
    return " ".join(words[-4:]).strip(" :-–—") or ""


def read_pdf(path: str | os.PathLike) -> dict:
    """A guidelines PDF, read in place (never copied): colours with their labels, and type family candidates."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise UsageError(f"{p} doesn't exist.")
    pages, engine = pdf_text(p)
    found = _empty()
    for n, page in enumerate(pages, start=1):
        for line in page.splitlines():
            for m in _PDF_HEX.finditer(line):
                found["colours"].append({"name": _label_before(line, m.start()) or None,
                                         "value": colour.normalise("#" + m.group(1)), "raw": m.group(0),
                                         "where": f"page {n}", "via": "guidelines"})
            for rx in (_PDF_RGB, _PDF_RGB2):
                for m in rx.finditer(line):
                    r, g, b = (int(x) for x in m.groups())
                    if max(r, g, b) <= 255:
                        found["colours"].append({"name": _label_before(line, m.start()) or None,
                                                 "value": colour.to_hex((r, g, b, 1.0)), "raw": m.group(0),
                                                 "where": f"page {n}", "via": "guidelines"})
            for m in _PDF_WEIGHT.finditer(line):
                fam = m.group(1)
                if fam.split()[0] not in _NOT_FAMILY:
                    found["fonts"].append({"family": fam, "weight": m.group(2), "where": f"page {n}",
                                           "via": "guidelines", "line": line.strip()[:160]})
    sha = jsonio.sha256(p)
    source = {"id": _source_id("guidelines", p.stem), "kind": "guidelines", "where": str(p.resolve()),
              "read": "in_place", "pin": f"sha256 {sha[:16]}", "pages": len(pages), "engine": engine}
    return {"source": source, "found": found, "files_read": 1, "problems": found["problems"]}


# ------------------------------------------------------------------------------------------------------ WORK code
def is_work_path(spec: str) -> bool:
    return str(spec).lower().startswith(("work:", "work://"))


def remote_path(spec: str) -> str:
    """The path part of `work:<path>` (or `WORK:<path>`); anything else as it is."""
    return re.sub(r"^work:(//)?", "", str(spec), flags=re.IGNORECASE).strip()


def peer_procedure(spec: str, brand: str, *, step: str = "look") -> dict:
    """The read-only steps for company code on the WORK Mac. Slate prints them; a person runs them."""
    remote = remote_path(spec) or "<path on WORK>"
    steps = [
        f"peer ls '{remote}'",
        "   find the files that hold the design language: stylesheets and theme files (colour, type and easing "
        "tokens), SwiftUI views and asset catalogs, the guidelines",
        f"peer cat '{remote}/<file>' | slate brand onboard {brand} --step {step} --stdin-as 'work:{remote}/<file>' "
        "--source-pin <commit>",
        "   Slate reads the values from stdin and keeps only them, each with its path:line; no code touches this "
        "Mac's disk",
        f"peer cat '{remote}/.git/HEAD'",
        "   then the ref it names (or packed-refs) gives the commit to pin as --source-pin",
    ]
    return {
        "where": f"WORK:{remote}",
        "why": "Company source code is read in place, never copied: it stays on the WORK Mac, and only the values "
               "Slate reads from it (colours, eases, fonts) reach the brand pack.",
        "steps": steps,
        "never": ["peer fetch of company code", "redirecting peer cat into a file on this Mac",
                  "copying the code into the brand pack"],
    }
