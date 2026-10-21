"""Slate's ExtendScript files parse in After Effects: ExtendScript is ES3 and refuses a file that declares one of its
reserved words as a name ("Illegal use of reserved word"). validate.jsx once named a counter `native`, so `slate ae
audit` got no report back and every AE finish stopped at the audit."""

import re
from pathlib import Path

ENGINE_AE = Path(__file__).resolve().parents[2] / "engine" / "ae"
RESERVED = ("abstract boolean byte char class const debugger double enum export extends final float goto implements "
            "import int interface long native package private protected public short static super synchronized "
            "throws transient volatile").split()
NAME = r"(?:%s)" % "|".join(RESERVED)
# a declared name: `var a, native, b`, `function native(`, a parameter, or `native = `/`native++` as an assignment target
DECLARED = re.compile(r"\bvar\s+[^;]*?\b%s\b\s*[,;=]|\bfunction\s+%s\s*\(|\bfunction\s*\w*\s*\([^)]*\b%s\b[^)]*\)|"
                      r"(?:^|[;{}\s])%s\s*(?:\+\+|--|=(?!=))" % (NAME, NAME, NAME, NAME))


def _code(text: str) -> str:
    """The script without comments and string literals, where a reserved word is only words."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    return re.sub(r"\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'", '""', text)


def test_no_extendscript_file_declares_a_reserved_word():
    files = sorted(ENGINE_AE.rglob("*.jsx"))
    assert files, ENGINE_AE
    found = []
    for f in files:
        for n, line in enumerate(_code(f.read_text(encoding="utf-8")).splitlines(), 1):
            if DECLARED.search(line):
                found.append(f"{f.relative_to(ENGINE_AE)}:{n}: {line.strip()[:100]}")
    assert not found, "ExtendScript (ES3) reserved words used as names:\n" + "\n".join(found)
