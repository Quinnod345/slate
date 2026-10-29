# Creative history through the CLI and MCP

Selected creative history is private data outside the Slate checkout. Reading it does not edit the source or its
selection, grant creative approval, authorize spending, or block production when the history is unavailable.
The current brief and newer feedback take priority over remembered preferences.

The original CLI output remains available with `slate history context --json` or `--view=full`. It includes each
selected pattern's full evidence and counterexamples. `slate history context --view=planning --json` gives a
deterministic projection: exact guidance, scope, counterexamples, interpretation and approval fields where present,
with source locations and JSON pointers for evidence. `reference_only_fields` identifies original fields that need
detail retrieval. This projection is not an AI summary. Ordinary secret redaction still applies to displayed output.

Every page includes the selected source path and SHA-256, `total_patterns` before topic filtering, and
`matching_patterns` after filtering. `pattern_index` always identifies the original selected list. A planning page's
`paging.next_offset` is an offset in the filtered list; `null` means there is no following page. Use the returned
source SHA-256 as `--expect-sha256` on subsequent requests to refuse combining different source versions.

For example, read the motion preferences in pages:

```sh
slate history context --view=planning --topic=motion --limit=10 --json
slate history context --view=planning --topic=motion --offset=10 --limit=10 --expect-sha256=SOURCE_SHA256 --json
```

Replace `SOURCE_SHA256` with the first page's returned hash, and use its actual `paging.next_offset` rather than
assuming the page contains the requested number of patterns.

A detail request reads one original pattern or its named field:

```sh
slate history context --view=detail --pattern-index=0 --field=pattern --text-chars=16000 --json
```

`detail.text` is a fragment of the value's compact JSON serialization. Append fragments in `detail.offset` order,
using each `detail.next_offset` as `--text-offset`, and parse the joined text once. Individual fragments need not
be valid JSON. Use the same source hash on every request. `detail.complete` means this fragment reached the end;
`returned_chars` and `total_chars` describe the serialized value. Known secrets are redacted before fragmenting so
a key cannot escape protection across a page boundary; `detail.redacted` makes that exception explicit. The original
source bytes remain unchanged.

MCP's `run` stage for `history context` defaults to planning pages and needs no film. An explicitly supplied film
still must resolve normally; all other stages keep their film requirements. The MCP allowlist admits only the
read-only topic, view, page, detail and source-hash arguments. Unbounded `--view=full` remains a CLI operation.

Before returning a history page, MCP measures its entire serialized text envelope with the same JSON formatting as
the server's ordinary output limiter. It reduces whole-pattern page sizes or detail fragment sizes until that text
fits 60,000 Python characters, including the command and envelope. If one complete planning row cannot fit, MCP
returns an explicit index page with `fields_deferred` and `detail_args`; it never advances past an empty replacement
or reports missing source text as read. Metadata that cannot fit even that representation is refused clearly.
The returned command names the actual bounded CLI request, including options added by the wrapper.

The character limit is for one rendered JSON text envelope. It is not a limit on UTF-8 bytes, nor on the transport's
combined text and duplicate structured-content payload. Cached installed-client schemas may still need a separate,
authorized client refresh; source/server tests do not prove that an already-running client adopted this capability.
