"""Generation: shots made on the Higgsfield Cloud API, only where code can't make them, judged before they ship.

- `policy`: when a shot may generate (the requester's none / rarely / where-it-helps, reason categories, drafts,
  autonomous approvals, brand uploads). `policy.allowed(film_json, shot) -> (bool, why)`.
- `run`: estimate, run (at least two takes, ledger-wrapped, resumable), request-approval, approve, cancel, takes.
- `judge`: the contact sheet, the REVIEW gate, the person's pick.
- `plates`: the plan, and the conform of an approved take to a JPEG sequence at the film's frame rate.
- `matte`: a bridge generation used only for its shape, revealing real frames.

The client is `slate.vendors.higgsfield.Client`. Commands: `slate gen …` and `slate plates …` (slate.gen.cli).
"""

from slate.gen import policy  # noqa: F401  (slate.gen.policy.allowed is a cross-module interface)
