"""Film state: the film folder, what's in it, what made it, who approved it, and where it stands.

The modules other parts of Slate call (docs/ARCHITECTURE.md §12):

- `film`: create(), load(), save(): film.json, made from the format's defaults and the brand;
- `lock`: record(), is_stale(), lock_asset(), is_locked(), guard_regenerate(), resolve(), timeline_locked(),
  freeze_shot_ids(), frozen_shot_ids(), check_renumber(): film.lock.json;
- `pins`: pin() (first use wins), get(), repin(), model(), tool();
- `approvals`: record() (with the SHA-256 of exactly what was approved), is_approved(), sittings(), ask(), answer();
- `production`: add_direction() (numbered, verbatim), add_ask(), mark(): the brief and its trackers;
- `status`: compute(): per-stage state and the one next command;
- `modes`: settings(mode), check(film_dir, action), park(): the four modes as rules;
- `park`: park(film_dir, *, reason, evidence, source): the door qa and others park the film through;
- `derive`: create(): cut-downs, new aspects and new languages, by reference;
- `revise`: start(), check(), done(), register_hook(): perfect, don't replace;
- `make`: run(): rebuild only what's stale.
"""

from slate.state import (_util, approvals, deps, derive, film, lock, make, modes, park, pins, production,
                         revise, scaffold, status)
from slate.state.lock import frozen_shot_ids

__all__ = ["_util", "approvals", "deps", "derive", "film", "frozen_shot_ids", "lock", "make", "modes", "park",
           "pins", "production", "revise", "scaffold", "status"]
