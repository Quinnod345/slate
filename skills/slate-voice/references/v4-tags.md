# Directing eleven_v4 with audio tags

An audio tag is a short natural-language instruction in square brackets, placed where the delivery should change:
`[warmly, with a smile] Hi, it's good to meet you.` The list of tags is open (they're direction, not a fixed menu), and
v4 follows them more reliably than earlier models, though not perfectly. Tags are billed as characters.

## The rule above all: full voice only

**Never use a tag that lowers vocal effort or adds breath.** That means whisper or whispering, talking softly, soft or
softly, quiet or quietly, gentle or gently, hushed, murmuring, under one's breath, breathy, intimate, calm, and a low
voice, in any form or combination. The owner heard them as whispery and a little scary. Even a tag that sounds harmless
("calm and reassuring") measured breathier than the voice's untagged reads. Slate refuses these words inside any tag
before a request is sent, for every brand; a brand can ban more, and nothing can un-ban these.

**Emotion comes through a full, clear voice**: warm, with a smile, bright, confident, reassuring, encouraging, sincere,
matter-of-fact, hopeful.

## The families (safe examples)

| Family | Examples that work |
|---|---|
| Emotion | `[happy]`, `[excited]`, `[thoughtful]`, `[curious]`, `[hopeful]`, `[delighted]`, `[impressed]`, `[reflective]` |
| Delivery and register | `[confident]`, `[warmly]`, `[reassuring]`, `[sincere]`, `[matter-of-fact]`, `[encouraging]`, `[conversational tone]`, `[professional]` |
| Longer narration direction | `[warm, conversational tone]`, `[bright, friendly, smiling]`, `[candid, kind]`, `[voice rising into firm resolve]` |
| Pacing and pauses | `[pause]`, `[short pause]`, `[long pause]` |
| Reactions (non-verbal) | `[laughs]`, `[chuckles]`, `[sighs]`, `[clears throat]`: only when the voice's character suits them |

Two families exist that a film narrator almost never wants: **sound effects** (the model can perform a tag as a sound
effect; a film's sound is designed separately) and **accents or singing**.

## The rules

**How many**
- Don't tag every line: v4 infers emotion from well-written text. Tag about half the lines at most.
- One emotion per phrase. A tag at the head of a line carries across the whole line.

**Where**
- Immediately before the segment it modifies (a reaction can follow it). If the feeling changes mid-sentence, put the tag
  before the exact clause.
- A softening tag in the middle of a line is where creak appeared on the first films: prefer the head of the line, and
  give any mid-line tag a creak check.

**Wording**
- A tag describes something you can **hear**, never a face, a gesture or music (not `[grinning]`, `[standing]`, `[music]`).
- Be explicit about voice quality, and word tags so they can't be read as a sound cue: describe the voice, never an
  object or an event.
- Match tags to the voice's character: a serious professional voice doesn't suit playful tags, and a designed voice is
  never asked for a performance it wasn't designed for (laughing, big emotion).

**The words**
- Tags never change a word. A tagged line must strip back to the script exactly (Slate refuses one that doesn't).
- If a line doesn't land, swap the tag rather than rewriting the text, then regenerate.

**Pauses**
- v4 has no SSML. Pause with punctuation and structure (an ellipsis adds pause and weight, a line break a longer beat) or
  a pause tag, sparingly. Air after heavy lines comes from the edit.

## What tags do, measured

On the first films a smile tag lifted the pitch about 2.4 semitones and brightened the voice; a brighter variant lifted
about 3.4 semitones, which risks sounding perky. Tags work, modestly. They also brought failures: one smile tag alone
before a short line made the model say the phrase twice, and a mid-line softening tag put the voice into creak. So
every tagged take is checked.

## Failure modes, and the guard for each

| Failure | Guard |
|---|---|
| A tag read aloud | tags stay inside the voice's character; Whisper checks every take for tag words spoken |
| A tag performed as a sound effect | tags name voice qualities, never objects or events |
| Over-acting | tag sparingly, one tag per segment, stability kept at its start value; pace and pitch range measured against an untagged control |
| A repeated phrase | a word-for-word check on every take |
| Creak (vocal fry) on a key phrase | a creak count (low, irregular pitch frames) against the untagged baseline |
| Drift between lines | one small vocabulary, the same settings and context rule; seed and request id recorded per take |
| A pause that breaks the edit | pause tags only where the edit can't give the air |
| A take that no longer fits its shot | every paced take measured against its shot's room |

## Measuring breath and effort

Every tagged take is measured against the same voice's untagged takes **of the same words** (several of them, so the
normal spread is known), on the spans the tag governs and on the whole line:
- **breath and whisper**: the voiced fraction of speech, cepstral peak prominence, harmonics-to-noise, the first-minus-
  second harmonic, spectral tilt, and effort (the span's level against the rest of the take);
- **warmth and smile**: pitch level and range, formants, spectral centroid;
- **energy**: loudness and its contour; **creak**: low, irregular pitch; **pace**: words a second and pauses; **words**:
  errors, repeats, tags spoken.

A take is rejected when its breath measures sit clearly above the untagged spread (on average more than one spread, or
two core measures well beyond it, or the voice dropping out). These checks were validated against the owner's ear on
takes they'd already judged, before they were trusted. The owner still picks by ear.

## The A/B before a full re-voice

Tag sparingly, then A/B a handful of key lines for the owner (the current take, the tagged take, both level-matched,
side by side) before re-voicing everything. Because every beat is keyed to the voice's words, a re-voice re-times the
film automatically; the renders and the mix still rebuild.

When a line's direction really matters, a single A/B isn't enough to know: the same tag can pass on one seed and measure
breathier on the next. Run a round of the tag loop instead (several candidates, several seeds, extra untagged seeds,
gates and a measured feeling, then an ABC reel), and adopt the pick as that exact file: [the tag loop](tag-loop.md).
How each measure is read: [measuring a read](measuring-reads.md).

## Settings and context

Tags work with the voice's stored settings (stability about 0.5). Neighbouring lines go in as plain words, never tagged
(directing.md).

Passing the next line as context can make a short final word read as a continuation (it rises). Dropping the
next-line context (a tag-loop candidate's `no_next`) lets it fall, though with no tag the model may run the words
together and lose the beat.
