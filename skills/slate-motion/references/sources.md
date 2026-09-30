# Sources for the motion rules

The motion rules cite their research by label. Read the sources yourself when a rule needs defending or updating;
paraphrase them, and quote only briefly and exactly.

## Principles and systems

- **[Disney 12]** Frank Thomas and Ollie Johnston, *The Illusion of Life* (1981): the twelve basic principles of
  animation. Overview: https://en.wikipedia.org/wiki/Twelve_basic_principles_of_animation
- **[UX in Motion]** Issara Willenskomer, the twelve principles of UX in motion; and his note that only easing carries
  over directly from character animation to interface motion:
  https://blog.adobe.com/en/publish/2016/11/08/redesigning-the-12-principles-of-animation-for-motion-design
- **[M3 tokens]** Material 3 motion tokens (durations, easing, springs), in the material-web source:
  https://github.com/material-components/material-web
- **[M3 loader]** Material 3's loading indicator (quantized turns on one spring), in the androidx source:
  https://github.com/androidx/androidx
- **[Carbon]** IBM Carbon's motion tokens (productive and expressive):
  https://github.com/carbon-design-system/carbon/blob/main/packages/motion/src/dtcg/motion.json
- **[HIG]** Apple's Human Interface Guidelines, Motion:
  https://developer.apple.com/design/human-interface-guidelines/motion
- **[WWDC23]** Apple, "Animate with springs": https://developer.apple.com/videos/play/wwdc2023/10158/
- **[WWDC18]** Apple, "Designing Fluid Interfaces": https://developer.apple.com/videos/play/wwdc2018/803/

## Multi-part marks and loaders

- **[SpinKit]** Tobias Ahlin's SpinKit: the circle spinner's delays in exact steps of cycle over parts.
  https://github.com/tobiasahlin/SpinKit
- **[Replicator]** Core Animation's replicator layer: an instance delay of duration over count.
- **[Follow-the-leader]** After Effects expressions reading each layer's value at time minus index times delay (the
  classic rig, as documented by Dan Ebberts).
- **[Metachronal]** Metachronal rhythm: a fixed lag between neighbours, as in cilia and legs.
- **[Common fate]** Gestalt common fate: parts moving with the same timing read as one object.

## Perception and timing

- **[Flicker]** Temporal contrast sensitivity peaks near 8 Hz, which is why a 4 Hz wobble (blur pulsing at 8 Hz) reads
  as flicker.
- **[Speech envelope]** Speech loudness modulates mostly between 2 and 8 Hz (Ding and colleagues, 2017), which is why a
  character is never driven by the voice's loudness.
- **[Gesture timing]** Ye, McQueen and Bosker (2025), summarizing Leonard and Cummins and Roustan and Dohen: a gesture's
  top speed coincides with the stressed vowel, and its apex falls inside the stressed syllable.
  https://pmc.ncbi.nlm.nih.gov/articles/PMC12331874/
- **[McNeill's rule]** Nirme, Gulz, Haake and Gullberg (2024): a gesture precedes or ends at, but doesn't follow, the
  stressed syllable; gestures moved 500 ms early didn't hurt recall, 500 ms late did.
  https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2024.1345906/full
- **[AV sync]** ITU-R BT.1359 (viewers detect sound from about 45 ms early to 125 ms late) and EBU R37 (+40 / −60 ms).

## Read time, safe areas and accessibility

- **[Read it twice]** The practitioner rule that supers stay up long enough for a slow reader to read them twice.
- **[Subtitles]** Broadcaster and streaming timed-text guides: about 160 to 180 words a minute, and at most 17 to 20
  characters a second.
- **[Safe areas]** SMPTE ST 2046-1 and EBU R 95: action-safe 93%, title-safe 90%. https://tech.ebu.ch/publications/r095
- **[WCAG]** WCAG 2.2: 2.3.1 (three flashes) and 2.2.2 (pause, stop, hide).
  https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide.html

## How good marks move (studied for character design)

Voice-assistant and loader marks were studied for how they move, not copied: a ring that moves outward to respond and
inward to listen; dots rooted in consistent paths and timing; a breathing flower driven by one container animation; an
assistant cursor that pairs every move with a matched sound; tempo carrying meaning (a fast pulse for an error, a slow
one for do-not-disturb); scripted states rather than live loudness; stillness as a design decision. Take the principle,
never the look.
