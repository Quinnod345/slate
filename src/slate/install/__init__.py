"""Installing Slate for every agent on this Mac, from one checkout.

- `layout`: where things go, and the sandbox rule (a home that isn't this account's never reaches the real session);
- `links`: skill links and CLI shims (`plan(home, agents)`, `apply(plan)`, `audit(home)`);
- `mcp_register`: the MCP server's registration command for each agent (`add_argv`, `remove_argv`, `plan`, `apply`);
- `quinn_skills`: whether the owner's visionary and goal-fidelity are here and set up;
- `env`: the checkout's uv environment;
- `core`: the whole install (`install`, `check`, `uninstall`, and `wire(agents)` for `slate connect claude`);
- `cli`: `slate install` and `slate uninstall`.

install.sh bootstraps the environment and then hands over to `slate install`; `slate update` (slate.update) pulls
and re-links. INSTALL_FOR_AGENTS.md is the agent-facing guide; docs/CLEAN_MACHINE.md the clean-machine test.
"""

from slate.install import core, env, layout, links, manifest, mcp_register, quinn_skills  # noqa: F401

__all__ = ["core", "env", "layout", "links", "manifest", "mcp_register", "quinn_skills"]
