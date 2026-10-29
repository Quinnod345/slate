# App captures: real footage on the film's clock

A product demo is often best told with the product itself. Slate records it deterministically (a pinned build, a
seeded demo account, scripted steps), conforms it to the film's frame rate and requested capture size, and lands its
taps on the voice's words, so a capture behaves like any other shot on the one clock.

## iOS

```
slate capture ios --spec captures/save-entry.json
```

A spec names the app (its path, bundle id and the SHA-256 of the pinned build), the demo account (launch arguments
that seed it), where it sits in the film (`place: {shot}`), and timed steps:

```json
{"kind": "ios", "name": "save-entry", "duration": 8,
 "app": {"path": "build/Demo.app", "bundle_id": "com.example.demo", "build_sha256": "0000000000000000000000000000000000000000000000000000000000000000"},
 "account": {"kind": "demo", "launch_args": ["-ui-testing", "-seed-demo-account"]},
 "place": {"shot": "S04"},
 "steps": [{"at": 0, "do": "launch"},
           {"at": 2.4, "do": "tap", "id": "save", "x": 201, "y": 742, "word": {"shot": "S04", "text": "save"}, "size": "small"}]}
```

Replace the example's zero hash with the actual app bundle's SHA-256 and use launch arguments that the app supports.
Tap sizes are `small`, `medium` or `large`.

- By default it creates or reuses a Slate-owned Simulator, erases it before recording, and shuts it down afterwards.
  The device is retained for reuse; other devices are left alone. Apple's status bar shows 9:41, full battery and
  full bars.
- A build whose hash doesn't match the spec is refused. The hash pins the recorded bytes; owner approval and the
  build's correspondence to source are separate evidence.
- Tap and type steps need an installed idb or AXe driver. Slate refuses when neither is available; it does not
  silently simulate an interaction. Choose `tap_driver: scripted` only when the app actually plays its own demo
  from launch arguments: those steps mark interactions the app performs, rather than injecting taps or text.
- Booting a Simulator is heavy: run captures when the machine is quiet, one at a time.

Prepare the demo profile's setup and privacy choices before recording. Record those choices and distinguish seeded
content from actual feature output. Inspect startup frames and first-use tips before selecting film footage; allow
the app to settle. Clear notices through the app's real demo state or controls, and keep optional sharing disabled
when the intended demo declines it. Removing a notice is not a reason to grant sharing or manufacture AI output.

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
  rate, which scenes play from film time. `fit: native` preserves the device's pixels; `contain` or `cover` fits to
  the spec's size, or the film's size when no size is supplied. A native sequence is source footage, not a finished
  film frame.
- Taps and marks become visual events (kind `tap`, their size), beat-mapped onto the voice's words: holds and small
  retimes happen only in idle time, never inside a tap's response. A tap that still misses its word fails the capture's
  gate rather than warping the motion.
- A recording made by hand can be conformed the same way, as its own capture.
- Build previews from the capture record's frame count and rational frame rate. Keep the film's clock when copying
  stills or encoding a review movie; do not round a fractional rate to an integer.

## Rules

- Capture for truth and speed; rebuild in code when a shot needs camera moves, restaging, motion blur, exact type or a
  state the demo account can't reach (app-ui.md).
- Use the demo account and sample data only; never a real person's account or data.
- Captures that were replayed in tests are marked so, and never ship in a final delivery.
- Recording an app to check its UI (jank, flicker, transitions) is QA, not film-making: that's video-verifier's job,
  not a capture.
