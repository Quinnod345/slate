"""Slate eval probe: records the environment Slate gives each child process it starts.

An eval puts this folder on PYTHONPATH and sets SLATE_EVAL_PROBE_LOG. Every Python process the agent then starts
imports this file at startup (that's what `sitecustomize` is for); in any process that is running Slate, it notes each
child process's BROWSER value and how its stdin was set up, and on exit it notes that a Slate process ran at all, which
proves the probe reached Slate. The eval's graders read the log:

- every child with BROWSER other than `none` fails the no-browser check;
- a child that inherited the agent's stdin fails the slice's no-stdin check;
- Slate commands in the transcript with no Slate process in the log mean the probe didn't reach them (UNCHECKED).

Without SLATE_EVAL_PROBE_LOG this file does nothing at all. It records the program's name, never its arguments or
environment values other than BROWSER, so no key can end up in the log. After installing itself it runs any other
sitecustomize further along the path, so it never hides one.
"""

import os

_LOG = os.environ.get("SLATE_EVAL_PROBE_LOG")

if _LOG:
    import atexit
    import json
    import subprocess
    import sys
    import time

    _original_init = subprocess.Popen.__init__

    def _in_slate():
        return "slate.cli" in sys.modules or "slate.procs" in sys.modules

    def _write(record):
        try:
            with open(_LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError:
            pass

    def _stdin_kind(value):
        if value is None:
            return "inherit"
        if value == subprocess.DEVNULL:
            return "devnull"
        if value == subprocess.PIPE:
            return "pipe"
        return "file"

    def _probe_init(self, args, *rest, **kw):
        if _in_slate():
            # Popen(args, bufsize, executable, stdin, stdout, stderr, preexec_fn, close_fds, shell, cwd, env, ...)
            env = kw.get("env", rest[9] if len(rest) > 9 else None)
            stdin = kw.get("stdin", rest[2] if len(rest) > 2 else None)
            source = env if env is not None else os.environ
            if isinstance(args, (list, tuple)) and args:
                first = args[0]
            else:
                first = str(args).split(" ", 1)[0] if args else ""
            _write({"kind": "child", "pid": os.getpid(), "argv0": os.path.basename(os.fspath(first)),
                    "browser": source.get("BROWSER"), "stdin": _stdin_kind(stdin), "at": time.time()})
        _original_init(self, args, *rest, **kw)

    subprocess.Popen.__init__ = _probe_init

    def _goodbye():
        if _in_slate():
            _write({"kind": "slate_process", "pid": os.getpid(),
                    "command": os.path.basename(sys.argv[0]) if sys.argv else "", "at": time.time()})

    atexit.register(_goodbye)

# Chain to any other sitecustomize on the path, so the probe never hides one.
try:
    import importlib.util as _util
    import sys as _sys

    _here = os.path.dirname(os.path.abspath(__file__))
    for _entry in _sys.path:
        if not _entry or os.path.abspath(_entry) == _here:
            continue
        _candidate = os.path.join(_entry, "sitecustomize.py")
        if os.path.isfile(_candidate):
            _spec = _util.spec_from_file_location("_slate_chained_sitecustomize", _candidate)
            if _spec and _spec.loader:
                _spec.loader.exec_module(_util.module_from_spec(_spec))
            break
except Exception:  # noqa: BLE001 - a broken chained sitecustomize must never stop Python starting
    pass
