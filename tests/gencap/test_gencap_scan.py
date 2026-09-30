"""Source scans: what must not exist in Slate, proven by reading the tree.

- The Higgsfield connector's sync_agents (it uploads a user's skills and a personality profile), TikTok publishing and
  website publishing exist nowhere in Slate's code or config, except in lists that deny them.
- Slate never generates through the Higgsfield CLI or app credits: the Cloud API is the one lane.
- Generation and capture code never buys, never hard-codes a frame size or rate, never names a company, never opens a
  browser other than Aside, and never starts a child process except through slate.procs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CODE_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".toml", ".yaml", ".yml", ".sh"}
SKIP_DIRS = {"node_modules", ".venv", ".git", "__pycache__", ".pytest_cache", "dist", "build"}
MINE = [REPO / "src" / "slate" / "gen", REPO / "src" / "slate" / "capture",
        REPO / "src" / "slate" / "vendors" / "higgsfield.py"]
MY_SCHEMAS = [REPO / "src" / "slate" / "schemas" / f"{n}.schema.json" for n in ("capture", "plate", "generation")]
DENIED_TOOLS = re.compile(r"\b(sync_agents|tiktok_(?:connect|reconnect|prepare_publish|publish_status|publish)|"
                          r"(?:create|deploy|publish)_website)\b")
# A name inside a denial ("denied", "never", "forbid", …) or a detector (a regex that flags the call in a transcript,
# as the evals do) guards the rule; anything else (a call, an allow-list, a tool config) breaks it.
DENIAL = re.compile(r"deny|denied|never|forbid|block|refuse|re\.compile|detect", re.I)


def _code_files(roots):
    for root in roots:
        root = Path(root)
        if root.is_file():
            yield root
            continue
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in CODE_SUFFIXES and not (set(path.parts) & SKIP_DIRS):
                yield path


def _slate_tree():
    roots = [REPO / "src", REPO / "engine", REPO / "plugins", REPO / "skills", REPO / "formats", REPO / "brands",
             REPO / ".claude-plugin", REPO / "install.sh", REPO / "registry.json"]
    return [p for p in _code_files(roots) if "tests" not in p.relative_to(REPO).parts]


def _json_mentions(data, where=()):
    """(key path, value) for every string in a JSON document that names a denied tool."""
    if isinstance(data, dict):
        for key, value in data.items():
            yield from _json_mentions(value, (*where, str(key)))
    elif isinstance(data, list):
        for value in data:
            yield from _json_mentions(value, where)
    elif isinstance(data, str) and DENIED_TOOLS.search(data):
        yield where, data


def test_denied_connector_tools_exist_nowhere_but_in_denials():
    """In JSON, a name is a denial when a key on its path says so (denied_connector_tools.tools, say); elsewhere, when
    the lines just above it do."""
    offenders, denials = [], []
    for path in _slate_tree():
        text = path.read_text(errors="replace")
        rel = path.relative_to(REPO)
        if path.suffix == ".json":
            try:
                doc = json.loads(text)
            except json.JSONDecodeError:
                doc = None
            if doc is not None:
                for where, value in _json_mentions(doc):
                    (denials if any(DENIAL.search(k) for k in where) else offenders).append(
                        f"{rel}:{'.'.join(where)}={value[:60]}")
                continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if DENIED_TOOLS.search(line):
                context = "\n".join(lines[max(0, i - 5):i + 1])
                (denials if DENIAL.search(context) else offenders).append(f"{rel}:{i + 1}")
    assert offenders == [], f"sync_agents / TikTok / website publishing appear outside a denial: {offenders}"
    assert any("vendors/higgsfield.py" in d for d in denials)  # the scan does see the one list that names them


def test_the_scan_still_catches_a_real_allow_list(tmp_path):
    """The JSON reading can't be fooled: the same names under an allow key are offenders."""
    allowed = {"mcp": {"higgsfield": {"allow": ["generate_video", "sync_agents"]}}}
    denied = {"denied_connector_tools": {"tools": ["sync_agents"]}}
    assert [w for w, _ in _json_mentions(allowed)] == [("mcp", "higgsfield", "allow")]
    assert not any(DENIAL.search(k) for w, _ in _json_mentions(allowed) for k in w)
    assert all(any(DENIAL.search(k) for k in w) for w, _ in _json_mentions(denied))


def test_slate_never_generates_through_the_higgsfield_cli():
    patterns = [re.compile(r"""["']higgsfield["']\s*,\s*["'](generate|workflow|website|image|video)"""),
                re.compile(r"\bhiggsfield\s+(generate|workflow|website)\b")]
    offenders = []
    for path in _slate_tree():
        if path.suffix == ".json":
            continue
        for i, line in enumerate(path.read_text(errors="replace").splitlines()):
            if any(p.search(line) for p in patterns):
                offenders.append(f"{path.relative_to(REPO)}:{i + 1}: {line.strip()}")
    assert offenders == [], offenders


def _mine_text():
    for path in list(_code_files(MINE)) + MY_SCHEMAS:
        yield path, path.read_text(errors="replace")


def test_generation_never_buys_credits_or_plans():
    buying = re.compile(r"buy[_ -]?credits|purchase|checkout|top[_-]?up|upgrade[_ ]plan|/billing|/subscriptions?\b",
                        re.I)
    offenders = [f"{p.relative_to(REPO)}" for p, text in _mine_text() if buying.search(text)]
    assert offenders == []


def test_no_frame_size_or_rate_literals_in_logic():
    literal = re.compile(r"(?<![\d.])(1920|1080|30000|1001)(?![\d])")
    offenders = []
    for path, text in _mine_text():
        for i, line in enumerate(text.splitlines()):
            if literal.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{i + 1}: {line.strip()}")
    assert offenders == [], "sizes and frame rates come from film.json: " + "; ".join(offenders)


def test_no_company_material():
    names = re.compile(r"truespace|vixa|trudy|inner\s?echo|eos\s?worldwide|degular", re.I)
    shouting = re.compile(r"\bEOS\b")
    roots = MINE + MY_SCHEMAS + [Path(__file__).parent]
    offenders = []
    for path in _code_files(roots):
        if path == Path(__file__):
            continue
        text = path.read_text(errors="replace")
        if names.search(text) or shouting.search(text):
            offenders.append(str(path.relative_to(REPO)))
    assert offenders == []


def test_capture_never_opens_another_browser_or_bypasses_procs():
    forbidden = [re.compile(r"^\s*(import|from)\s+(playwright|webbrowser|selenium|subprocess)\b", re.M),
                 re.compile(r"webbrowser\.open|chromium\.launch|puppeteer|\[\s*[\"']open[\"']\s*,", re.I),
                 re.compile(r"\bos\.system\(|\bos\.popen\(")]
    offenders = []
    for path, text in _mine_text():
        if path.suffix != ".py":
            continue
        for pattern in forbidden:
            if pattern.search(text):
                offenders.append(f"{path.relative_to(REPO)}: {pattern.pattern}")
    assert offenders == []
    web_code = (REPO / "src" / "slate" / "capture" / "web.py").read_text()
    assert '"repl"' in web_code and "aside" in web_code  # web steps go through Aside's REPL


def test_the_higgsfield_client_has_no_connector_tools():
    from slate.vendors import higgsfield

    public = {name for name in dir(higgsfield.Client) if not name.startswith("_")}
    assert {"probe", "estimate", "submit", "wait", "cancel", "upload"} <= public
    assert not public & {"sync_agents", "publish", "tiktok", "website"}
    assert higgsfield.LANE == "cloud-api" and higgsfield.BASE == "https://api.higgsfield.ai"
