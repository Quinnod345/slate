# App captures: real footage on the film's clock

A product demo is often best told with the product itself. Slate records it deterministically (a pinned build, a
seeded demo account, scripted steps), conforms it to the film's frame rate and size, and lands its taps on the voice's
words, so a capture behaves like any other shot on the one clock.

## iOS

```
slate capture ios --spec captures/save-entry.json
```

A spec names the app (its path, bundle id and the SHA-256 of the pinned build), the demo account (launch arguments
that seed it), where it sits in the film (`place: {shot}`), and timed steps:

```json
{"kind": "ios", "name": "save-entry", "duration": 8,
 "app": {"path": "build/Demo.app", "bundle_id": "com.example.demo", "build_sha256": "<64 hex characters>"},
 "account": {"kind": "demo", "launch_args": ["-ui-testing", "-seed-demo-account"]},
 "place": {"shot": "S04"},
 "steps": [{"at": 0, "do": "launch"},
           {"at": 2.4, "do": "tap", "id": "save", "x": 201, "y": 742, "word": {"shot": "S04", "text": "save"}, "size": "s"}]}
```

- It runs on an isolated Simulator (a fresh device, deleted afterwards), with Apple's status bar: 9:41, full battery,
  full bars.
- A build whose hash doesn't match the spec is refused: the film shows the build that was approved.
- Taps need a tap driver; without one, the app taps itself (`tap_driver: scripted`) and marks when it does.
- Booting a Simulator is heavy: run captures when the machine is quiet, one at a time.

## Web

```
slate capture web --spec captures/pricing.json
```

- **Always through Aside**, never another browser. A local wrapper page holds the target at exactly the spec's viewport.
- Local folders and local dev servers are captured frame by frame on a virtual clock at the film's rate, so CSS and
  script animations land on exact frames.
- Other sites fall back to a real-time recording conformed from timestamps, and the capture record says so.

## After capturing

- The capture lands in `captures/<name>/`: `capture.json` (what, when, which build) and a JPEG sequence at the film's
  rate and size, which scenes play from film time.
- Taps and marks become visual events (kind `tap`, their size), beat-mapped onto the voice's words: holds and small
  retimes happen only in idle time, never inside a tap's response. A tap that still misses its word fails the capture's
  gate rather than warping the motion.
- A recording made by hand can be conformed the same way, as its own capture.

## Rules

- Capture for truth and speed; rebuild in code when a shot needs camera moves, restaging, motion blur, exact type or a
  state the demo account can't reach (app-ui.md).
- Use the demo account and sample data only; never a real person's account or data.
- Captures that were replayed in tests are marked so, and never ship in a final delivery.
- Recording an app to check its UI (jank, flicker, transitions) is QA, not film-making: that's video-verifier's job,
  not a capture.
