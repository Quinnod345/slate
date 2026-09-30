"""A stand-in agent for the eval runner's tests. It never calls a model and never opens anything.

Usage: python fake_agent.py <mode> <prompt | -> — with "-" the prompt is read from stdin, the way Oneiro's slices send
it. It prints Claude Code stream-json on stdout, so the runner's grading path is exercised end to end.

Modes:
  env       reports the environment it was given (as its final message, JSON)
  stdin     reports the prompt it read
  slate     runs a real Slate child process through slate.procs (so the probe records it) and says so
  tripwire  hands a URL to $BROWSER, but only after checking $BROWSER is the eval's tripwire
  sleep     starts a command that never finishes, then waits to be killed
  envelope  prints an OpenClaw-style JSON envelope with a session id (for the collect step)
  collect        (collect step) prints a chat.history-style transcript for the session id it's given
  collect-path   (collect step) writes a session transcript to a temporary file and prints its path
  collect-jsonl  (collect step) prints a session transcript's JSONL itself
"""

import json
import os
import subprocess
import sys
import time


def emit(obj):
    print(json.dumps(obj), flush=True)


def tool(n, name, inp):
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"t{n}", "name": name, "input": inp}]}})


def tool_result(n, text, error=False):
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{n}", "content": text,
                                                     "is_error": error}]}})


def result(text):
    emit({"type": "result", "subtype": "success", "is_error": False, "result": text})


def main(argv):
    mode = argv[1]
    if mode == "collect":
        print(json.dumps({"sessionKey": "agent:main:test", "messages": [
            {"role": "assistant", "content": [{"type": "text", "text": f"collected {argv[2]}"}]}]}))
        return 0
    if mode in ("collect-path", "collect-jsonl"):
        lines = [{"type": "session", "id": argv[2]},
                 {"type": "message", "message": {"role": "assistant",
                                                 "content": [{"type": "text", "text": f"transcript of {argv[2]}"}]}}]
        body = "".join(json.dumps(x) + "\n" for x in lines)
        if mode == "collect-jsonl":
            print(body, end="")
            return 0
        import tempfile

        where = os.environ.get("SLATE_DATA")  # the test session's temporary data folder
        if where:
            os.makedirs(where, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix="fake-openclaw-", suffix=".jsonl", dir=where or None)
        with os.fdopen(fd, "w") as fh:
            fh.write(body)
        print(path)
        return 0
    prompt = sys.stdin.read() if argv[2] == "-" else argv[2]
    if mode == "envelope":
        print(json.dumps({"status": "ok", "sessionId": "sess-123", "payloads": [{"text": "hi"}]}))
        return 0
    emit({"type": "system", "subtype": "init", "model": "fake", "cwd": os.getcwd()})
    if mode == "env":
        keys = ["BROWSER", "SLATE_FAKE_VENDORS", "SLATE_LIVE", "HF_KEY", "ELEVENLABS_API_KEY", "SLATE_FILMS",
                "SLATE_DATA", "SLATE_BRANDS", "SLATE_EVAL_PROBE_LOG", "SLATE_EVAL_TRIPWIRE_LOG", "PYTHONPATH", "TERM",
                "PATH", "SLATE_CONTEXT", "SLATE_EVAL"]
        report = {k: os.environ.get(k) for k in keys}
        report["cwd"] = os.getcwd()
        report["prompt"] = prompt
        result(json.dumps(report))
    elif mode == "stdin":
        result(f"read {len(prompt)} characters from stdin: {prompt}")
    elif mode == "slate":
        tool(1, "Bash", {"command": "slate status"})
        code = "import slate.procs as p; p.run(['/usr/bin/true'])"
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        tool_result(1, proc.stdout or "ok", error=proc.returncode != 0)
        result("Slate ran one child process.")
    elif mode == "tripwire":
        browser = os.environ.get("BROWSER", "")
        if not browser.endswith(os.path.join("tripwire", "browser")):
            result("refusing: BROWSER isn't the eval's tripwire")
            return 3
        tool(1, "Bash", {"command": "gh browse"})
        subprocess.run([browser, "https://example.com/never-opened"], check=True)
        tool_result(1, "Opening in your browser.")
        result("asked $BROWSER to open a page")
    elif mode == "sleep":
        tool(1, "Bash", {"command": "slate connect elevenlabs"})
        time.sleep(60)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
