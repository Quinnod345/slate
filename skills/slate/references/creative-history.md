# Learn from the revisions

A finished reference shows one destination. The useful history shows the route: version, exact human feedback,
response, changed version, and the human's reaction. Keep unsuccessful experiments and reversals; they often explain
the preference better than the last approval.

## Read the context

`slate history context` returns selected private patterns with their scope, source evidence and counterexamples.
Use `--topic story`, `look`, `motion`, `voice`, `sound` or `process` to focus the read. The same read is available
through the MCP run stage `history context`. `slate status` points to it when a history is selected.

These patterns are guidance. The current brief and newer feedback take priority. A missing history is not a gate.
Do not read a preview approval as approval of a final render, a scale test as approval of a whole film, or permission
recorded in an old chat as permission to spend or publish now.

## Recover a local Desktop history

```text
slate history recover --metadata <desktop-session.json> --project <transcript-folder> --out <new-private-folder>
```

Repeat `--metadata` for explicit forks. The recovery reads every linked transcript, retains rewind edges, counts
copied UUIDs once and keeps their occurrences. It recovers human text and answered question rounds, including the
question and options that framed each choice. Assistant replies stay labelled as claims, not proof of success.
Thinking blocks, image pixels and unrelated tool results are not exported; original files remain unchanged.

Write a curated private JSON file with a `patterns` list. Each pattern needs `guidance` and `evidence`; add `topics`,
`scope`, `counterexamples` and chronological references where useful. Select it with:

```text
slate history use --source <private-patterns.json>
```

Company transcripts and patterns never enter the Slate checkout or a distributable skill.

## Record the next iteration

```text
slate history note --notes "<exact feedback>" --by <person> --before <draft-path> --after <revised-path> --outcome preferred
```

Paths are relative to the film. Missing versions are recorded as missing; available files or directories get hashes.
Outcomes distinguish `requested`, `draft`, `measured`, `preferred` and `rejected`; none grants publishing approval.
`slate revise start` and `slate revise done` append the corresponding baseline and result records automatically.

For each interpretation, state what the person rejected and what they wanted to retain. “Smooth this jitter” does
not imply “slow every move.” “Return to the earlier sound” does not imply “never explore another sound.” Match the
scope, preserve the good parts, and build the requested experiment.
