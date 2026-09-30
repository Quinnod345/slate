# Acts built in After Effects

After Effects is a full second engine: a look can call for it (the AE bridge's looks, a brand whose motion lives in AE),
and an AE-built act runs on the same film clock and through the same gates as a Remotion act. The engine is chosen per
brand or per act during look development.

## The rules that never bend

- **Every AE step starts on a person's go.** Adobe's enterprise terms bar operations a person didn't start, and Slate
  treats that as the rule until the owner's own terms are checked. The CLI refuses AE steps without `--go`.
- **Autonomous films never touch After Effects.**
- **AE's preferences are changed only through Slate's byte-safe prefs tool**, with AE closed, one key at a time, line
  endings kept. The file uses old Mac line endings; a text-mode rewrite once made AE reset every preference and switched
  off the scripting-security setting. That setting ("Allow Scripts to Write Files and Access Network") is the owner's
  choice and is never changed by Slate.
- **Never quit AE for the owner**, and never "without saving". Ask them to save and quit.
- **One lock**: AE runs one script at a time, and Slate's AE lock is shared with the AE bridge's own busy lock.

## Building an act in AE

- Timing comes from the film's clock: comp markers and keyframes from `timeline.json` and the word timings, never typed
  by hand.
- The brand's motion law is converted to AE speed and influence (slate-motion, motion-law.md): the same eases, the same
  durations, read back after they're set.
- Brand tokens, fonts (activated in AE, by PostScript name) and logo shapes come from the brand pack.
- The act exports its visual events and its motion trace like any act, and meets its neighbours on the same contract
  frames.
- It renders through `slate render --engine ae --act <N> --go`, whole acts only, conformed to the film's delivery codec,
  registered like any render, and gated like any act: the glitch scan, the motion checks, the act joins.

## ExtendScript

AE's scripting language is ES3: `var` only; no JSON object, `indexOf`, `forEach`, `map`, `trim` or `Object.keys`; no
trailing commas; each script an immediately invoked function returning a string; properties addressed by match name;
ease arrays sized to the property (one for spatial properties, three for scale); every ease read back after it's set.

- Build on ExtendScript, which Adobe's move to UXP doesn't affect; avoid CEP-panel routes for anything long-lived (CEP
  leaves the flagship apps in the coming years).
- Nested comps need a sampling bias: every nested layer starts a tenth of a frame early, so AE samples inside each
  frame. Without it, slices at exact fractional-rate times showed the previous frame, and the picture ran a frame behind
  the sound.
- The working space matches the footage's tag (Rec.709 Gamma 2.4 for BT.709-tagged renders), or AE converts the colour.

## When AE is the right engine

AE-driven motion design is strongest for logo reveals, product promos, explainers and presentation animation, and
weakest for complex character animation, intricate rigging and tightly art-directed performance. Choose it when the
look needs what AE does best; keep character work in the kit.

## Routes into After Effects

- **Slate's own route**: ExtendScript run through osascript, with reports read back through AE's own settings, and
  aerender for output. It needs no preference change. The first films' native AE act builders (event-timed carriers
  from the film clock) proved AE can build scenes; they're the basis for AE-built acts.
- **Higgsfield's local bridge** (ae-bridge.md): live project state and rendered frames, and its looks. Its frame render
  handles colour-managed projects with a temporary display transform and warns when it falls back, so its frames aren't
  colour evidence unless that warning is clear. Its clean-rig references are worth reading during look development.
  Its project export and import use its own JSON schema, not Lottie; it has no HTML-to-scene, cloud preview or Lottie
  converter.
- **The vendor's AE panel and cloud bridge** generate footage into an AE project through a CEP panel: a different route
  from the local bridge, aimed at repeated structured assignments rather than art-directed scenes.
- **Adobe's own AI** (the in-app assistant, its chat connectors) can't drive After Effects for an agent: connecting to
  AE means a local bridge plus aerender.
- **Community AE MCP servers** (panel-based) and data-driven template renderers built on aerender exist as untested
  fallbacks.
