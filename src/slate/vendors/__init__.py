"""Vendor API clients. Each one is replay-aware (SLATE_FAKE_VENDORS=1 answers from fixtures or deterministic fakes)
and sends every paid call through `slate.ledger`: estimate, budget check, an in-flight row with a request id before
waiting, then the result.

- `slate.vendors.elevenlabs.Client`: text to speech, Voice Design, Remix, the Voice Isolator, Forced Alignment,
  Eleven Music and the free subscription read.
- `slate.vendors.higgsfield.Client`: the Higgsfield Cloud API.

Import the module you need; this package imports nothing on its own, so `slate --help` stays fast.
"""
