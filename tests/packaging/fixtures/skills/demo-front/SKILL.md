---
name: demo-front
description: >
  A neutral front-door skill used by Slate's packaging tests. It routes a video request to intake and formats,
  and it never names one agent's own tools.
license: Proprietary
metadata:
  owner: tests
  version: "1"
---

# Demo front door

Start every request with [intake](references/intake.md), then pick a shape from [the formats](references/formats.md).

Run `slate status` to see where a film stands.
