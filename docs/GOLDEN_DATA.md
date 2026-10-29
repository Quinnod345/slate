# Delivered golden data

Golden tests compare Slate with independent delivered records. A film's working folder can change after delivery:
its current timing may describe a new voice take while its audio stem still belongs to the earlier cut. Such a
comparison is a failing test until the matching inputs are recovered. Never regenerate the expected results from
Slate merely to turn it green, increase the tolerance to cover another cut, or skip a comparison because it failed.

The default `filmroot` fixture still reads the legacy film project in place. For reproducible runs on another
machine, select a private snapshot with `SLATE_GOLDEN_ROOT`. That snapshot preserves relative paths and includes
both the source inputs and the delivered expected records. Private media and company data stay outside this repo.

## Capture one coherent revision

Find the delivery's own records first. Preserve its input clock, cue sheet, original takes, rendered stems, score,
and expected measurements together. Explicitly list every file consumed by the selected tests in a JSON array of
relative paths. Include an archived input revision when the live film has moved on. File names and a successful
capture alone do not establish parity; the unchanged measurement tests must pass afterward.

```sh
.venv/bin/python tests/golden_oracle.py capture \
  --source "$FILM_SOURCE" --output "$PRIVATE_ORACLE" \
  --files "$INPUT_FILE_LIST" --label "delivered-v1"
.venv/bin/python tests/golden_oracle.py verify "$PRIVATE_ORACLE"
SLATE_GOLDEN_ROOT="$PRIVATE_ORACLE" SLATE_GOLDEN_HEAVY=1 \
  .venv/bin/python -m pytest tests/sound/test_sound_golden.py -v -p no:cacheprovider
```

Capture copies only the listed files, rejects symlinks and paths outside the source, and never overwrites a
snapshot. It compares source hashes and metadata before publishing the snapshot, and refuses a source that changed
while copying. Files are independent copies with writing disabled, never hard links. The `.slate-golden.json`
manifest contains the delivery label, source location, file sizes and SHA-256 hashes. Move the entire snapshot to
another machine without changing those relative paths.

The session verifies the selected manifest before the tests and again after them. A missing explicit snapshot,
changed hash, missing input, symlink or unlisted file fails instead of falling back to live data or skipping.
Snapshots can cover only a selected test set: running other tests against an incomplete snapshot should fail.
The absence of the legacy project still produces the existing skip when no explicit snapshot was selected.

## The four failures from October 1

The October 4 investigation recovered these causes without changing the original expected measurements:

- Film A's v2 mix inputs were compared with the current delivery pointer, which had become a p3 mix. The v2
  directory retains its own matching record and delivery stems; that is now the comparison source.
- Film C's delivered audio has 6,553 frames; its re-voiced working clock has 6,503. The voice archive retains the
  original clock and takes. The test selects exactly one revision whose sample count and every original cue start
  match the delivery record, then independently checks each waveform at the original 0.5 ms and correlation bars.
  Missing or ambiguous provenance fails clearly.
- Film B's current balance metadata omits `pinned`. Its source checker defines that as no pinned spans. The adapter
  now applies the same meaning, without treating full-depth rides as approvals.
- Once Film B's balance test reached its numbers, subtracting rounded decimal measurements produced
  `0.10000000000000142` at an exact 0.1 LU boundary. Its comparison now tolerates floating-point rounding at that
  boundary; the stated 0.1 LU bar is unchanged.

These are repairs to test input provenance and numeric comparison. They do not change the mix or alignment
algorithms, approve a film or prove the rest of the application complete. Current run receipts live in
`docs/build_reports/codex-golden-audit.json`.
