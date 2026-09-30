"""The Python side of Slate's Node engine (docs/ARCHITECTURE.md §8 and §12).

- `bridge.call(command, args, *, timeout=None) -> dict` runs `node <engine>/cli.mjs <command> --json '<args>'` and
  returns its one JSON object, raising the SlateError that matches the engine's error.
- `bridge.engine_dir() -> Path` is the engine to run: the installed one for the pinned Remotion version, else the
  repo's `engine/` (development), else SLATE_ENGINE_DIR when set.
- `install.ensure(version=None, *, npm_ci=False) -> Path` installs the engine into Slate's data folder.
- `skills` fetches Remotion's and ElevenLabs' agent skills into the installed engine, with Slate's overrides.

Commands: `slate engine install|status|path|skills`, `slate emit-events [--act N]`, `slate check <act>`.
"""

from slate.engine import bridge, install

__all__ = ["bridge", "install"]
