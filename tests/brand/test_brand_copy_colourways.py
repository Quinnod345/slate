"""A website's visible copy kept with its source (L-story-032), and a logo in every colourway a film needs
(L-brand-D016)."""

from __future__ import annotations

import json

from slate import jsonio
from slate.brand import onboard, pack, sources, validate

PAGE = """<!doctype html><html><head><title>Not copy</title><style>.x{color:#123456}</style>
<script>var notCopy = "hidden in code";</script></head>
<body>
<nav><a href="/">Home</a> <a href="/about">About us</a></nav>
<main>
<h1>Plans in <em>minutes</em>, not weeks</h1>
<p>Every number has a <strong>named</strong> source.</p>
<div hidden>A hidden line</div>
<p aria-hidden="true">Decorative words</p>
<p style="display: none">Collapsed words</p>
<ul><li>Fast</li><li>Calm</li></ul>
<img src="mark.png" alt="The studio mark">
<noscript>Turn on scripts</noscript>
</main>
<footer>© 2026 Studio</footer>
</body></html>"""


def _site(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(PAGE)
    return site


# ---------------------------------------------------------------------------------------------------------- copy
def test_visible_copy_is_leaf_text_lines_in_page_order():
    lines = sources.visible_copy(PAGE)
    assert lines == ["Home About us", "Plans in minutes, not weeks", "Every number has a named source.", "Fast", "Calm",
                     "The studio mark", "© 2026 Studio"]
    joined = " ".join(lines)
    for hidden in ("Not copy", "hidden in code", "A hidden line", "Decorative words", "Collapsed words",
                   "Turn on scripts", "#123456"):
        assert hidden not in joined


def test_the_look_step_keeps_a_websites_copy_beside_what_it_found(fresh, tmp_path):
    onboard.look("fresh", from_website=str(_site(tmp_path)))
    src = next(s for s in jsonio.read(fresh / onboard.LOOK_INPUTS)["sources"].values() if s["kind"] == "website")
    assert "found" in src and src["copy"][:2] == ["Home About us", "Plans in minutes, not weeks"]


def test_the_truth_step_keeps_a_saved_pages_copy_with_its_source(fresh, tmp_path):
    site = _site(tmp_path)
    onboard.truth("fresh", source=str(site / "index.html"), source_id="website")
    truth = jsonio.read(fresh / "brand.json")["truth"]
    src = next(s for s in truth["sources"] if s["id"] == "website")
    assert "Every number has a named source." in src["copy"]
    # the record still validates: a source may carry more than the schema names
    sch = next(r for r in validate.validate("fresh") if r.gate == "brand.schema")
    assert sch.status == "PASS", sch.evidence


# ---------------------------------------------------------------------------------------------------------- colourways
def test_colourways_come_from_each_logos_ground_and_name():
    cases = {"mark": ({"on": "page"}, ("mark", "ink")), "mark-inverse": ({"on": "inverse"}, ("mark", "white")),
             "wordmark-white": ({}, ("wordmark", "white")), "wordmark_ink": ({}, ("wordmark", "ink")),
             "seal": ({"on": "dark"}, ("seal", "white")), "badge-mono": ({}, ("badge", "mono"))}
    for name, (spec, want) in cases.items():
        assert pack.colourway_of(name, spec) == want, name


def test_the_demo_pack_has_its_mark_in_ink_and_white(studio):
    assert pack.colourways("studio") == {"mark": {"ink": "mark", "white": "mark-inverse"}}
    logos = next(r for r in validate.validate("studio") if r.gate == "brand.logos")
    assert not any("colourway" in e for e in logos.evidence)


def test_a_mark_in_one_colourway_is_flagged(studio):
    raw = jsonio.read(studio / "brand.json")
    del raw["logos"]["mark-inverse"]
    (studio / "brand.json").write_text(json.dumps(raw, indent=1))
    logos = next(r for r in validate.validate("studio") if r.gate == "brand.logos")
    assert logos.status == "REVIEW"
    assert any("only one colourway (ink)" in e and "mark-inverse" in e for e in logos.evidence)


def test_declared_colourways_on_one_logo_count():
    raw = {"logos": {"mark": {"file": "logos/mark.svg", "colourways": {"ink": "logos/mark.svg",
                                                                        "white": "logos/mark-white.svg"}}}}
    assert pack.colourways(raw) == {"mark": {"ink": "logos/mark.svg", "white": "logos/mark-white.svg"}}
