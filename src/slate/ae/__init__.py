"""After Effects: the finishing lane and the second way to build scenes.

Two routes reach After Effects, and both start only on a person's go:

- **Slate's own route** (needs no special AE setting): ExtendScript through `osascript` and `DoScriptFile`
  (`slate.ae.driver`), reporting back through AE's own preferences file because AE returns "0" to AppleScript for
  every script. It builds an editable AEP from the film's data (`slate.ae.project`), queues and renders chunked
  aerender masters (`slate.ae.masters`), audits the project (`slate.ae.audit`) and proves the toolchain with a
  1-second smoke render (`slate.ae.smoke`).
- **Higgsfield's AE bridge** (`fnf-after-effects-mcp`, pinned): builds scenes through its MCP tools and its 13
  motion-design skills, used as AE looks (`slate.ae.bridge`). It needs AE's "Allow Scripts to Write Files and Access
  Network" setting, which only the person turns on. Slate reads that setting and never changes it.

Rules this package enforces (docs/ARCHITECTURE.md §1, rules 5 and 6):

- AE's preferences file is edited only through `slate.ae.prefs`: bytes, one key, CR line endings kept, AE closed,
  never `Pref_SCRIPTING_FILE_NETWORK_SECURITY`. A text-mode rewrite once made AE reset every preference.
- Every command that drives AE or aerender needs `--go`, the person's explicit start. Autonomous films never touch
  AE: Adobe's enterprise terms (PSLT §1.2) bar operations a person didn't start (`slate.ae.guard`).
- Slate never quits AE and never discards changes. When AE has to be closed, it stops with exit 5 and asks the
  person to save their work and quit.
- One lock covers every route into AE, shared with the bridge's own lock (`slate.ae.driver.ae_lock`), because AE
  blocks on a modal when a second script arrives while one runs.

Import the submodules you need (`from slate.ae import prefs`); nothing heavy loads here.
"""

from __future__ import annotations

__all__ = ["acts", "audit", "bridge", "bridgelock", "cli", "driver", "eases", "guard", "masters", "prefs", "project",
           "report", "smoke", "spec", "svg2ae"]
