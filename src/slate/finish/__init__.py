"""Finishing and the review loop: masters, the delivery set, review packets, notes and the reviewer's grade.

The last three stages of a film (docs/PLAN.md §7, stages 11 to 13):

- **Master** (`slate master`). After Effects finishes every film a person supervises: an editable AE project and the
  masters, built through slate.ae and only on the person's go (`--go`). ffmpeg masters autonomous and draft films,
  which are held for the owner. Gates: every master frame against the act renders at 40 dB PSNR or better, exact
  frame counts, and the AE project audit.
- **Deliver** (`slate deliver`). Masters, stems, captions, review and phone cuts, thumbnails and the AE project go to
  deliver/vN/, with the manifest written last and every file's SHA-256 in it. Every film gate passes first. The
  manifest records, internally, which voices and shots are synthetic. Every output is "not approved for publishing"
  until the owner approves that exact SHA-256 (`slate approved <file>` checks). Drafts are never final, a replayed
  stand-in blocks a final delivery, and a delivered version is never overwritten.
- **Review and correct** (`slate review`, `slate notes`). A packet per version: a phone cut with the timecode and
  shot id burned in, a contact sheet every half second, what changed since the last version and the open questions.
  It goes to Downloads for the owner's own films, and by iMessage with a push for Oneiro's. Notes are logged in the
  owner's words and mapped to shot, beat and stage; the fix plan edits the approved work, never replaces it.
- **The reviewer's grade** (`slate review grade-brief`, `slate review grade`). Before an autonomous film is held for
  the owner, a separate reviewer grades the contact sheet and the sound against the brand's references, the owner's
  taste and the visionary bar. Holding the film needs that grade.

Cross-module interface (docs/ARCHITECTURE.md §12):

    master.run(film_dir, *, method) -> dict
    deliver.run(film_dir) -> Path                  the manifest, written last
    review.packet(film_dir) -> Path                review/vN/packet.json
    slate.notify.imessage(text, attachment=None) / slate.notify.push(title, body)
"""

from __future__ import annotations

import importlib

_SUBMODULES = {"approved", "changes", "checks", "cli", "deliver", "deps", "film", "grade", "master", "media",
               "notes", "review", "settings", "sheet", "burnin", "synthetic"}


def __getattr__(name: str):
    # `slate.finish.master.run(...)` after a plain `import slate.finish`, without importing everything up front.
    if name in _SUBMODULES:
        return importlib.import_module(f"slate.finish.{name}")
    raise AttributeError(f"module 'slate.finish' has no attribute {name!r}")
