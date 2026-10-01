"""The end-to-end tests: `slate demo` runs every stage for real (a render, every gate, a master), so they take minutes
and read the network once (the demo brand's open font). Opt in with SLATE_TEST_INTEGRATION=1 or `-m integration`; the
default run skips them."""

from __future__ import annotations

import os
import re

import pytest

INTEGRATION = "integration"


def wanted(config) -> bool:
    expr = config.getoption("markexpr", default="") or ""
    return os.environ.get("SLATE_TEST_INTEGRATION") == "1" or bool(re.search(r"(?<!not )\bintegration\b", expr))


def pytest_collection_modifyitems(config, items):
    if wanted(config):
        return
    skip = pytest.mark.skip(reason="runs `slate demo` end to end (minutes: a real render and every gate); opt in with "
                                   "SLATE_TEST_INTEGRATION=1 or -m integration")
    for item in items:
        if item.get_closest_marker(INTEGRATION):
            item.add_marker(skip)
