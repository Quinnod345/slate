# The script as data: script/script.json

`script/script.json` is the only hand-edited script file. `slate script build` gates it and writes
`script/storyboard.json` and `script/SCRIPT.md` (both stamped "generated: do not edit by hand"); after voicing,
`slate timing` turns the storyboard and the takes into the film's clock. Change the script, rebuild, never edit what it
generates.

## A worked example

This script builds clean, all ten gates passing, for a 30-second format:

```json
{
 "title": "The new week view",
 "version": "draft-1: the first full draft",
 "language": "en",
 "acts": [
  {
   "name": "The mess",
   "job": "show the week the viewer lives with today",
   "shots": [
    {
     "id": "S01",
     "vo": "Your week lives in six different places.",
     "on_screen": "Six places",
     "picture": "Six plain cards scatter across the page, each a different shape.",
     "transition": "carry: the cards gather into one column",
     "sources": ["site"],
     "events": [{"id": "cards_scatter", "word": "six", "land": "enter:0.5", "note": "the cards land on 'six'"}],
     "dur": 7
    },
    {
     "id": "S02",
     "vo": "Now it lives in one.",
     "on_screen": "One view",
     "picture": "The column folds into a single week card.",
     "transition": "carry: the week card lifts forward",
     "notes": "Signature moment of act 1: six cards become one.",
     "sources": ["app"],
     "dur": 6
    }
   ]
  },
  {
   "name": "The view",
   "job": "show what the week view does",
   "shots": [
    {
     "id": "S03",
     "vo": "Plan a whole week in one view, then share it.",
     "on_screen": ["One view", "Share"],
     "picture": "The week card opens; a share sheet slides out from under it.",
     "transition": "hold, then the end card rises",
     "claims": ["c1"],
     "sources": ["app:src/views/week.tsx:40"],
     "dur": 10
    },
    {
     "id": "S04",
     "vo": "Try the week view today.",
     "on_screen": "Try the week view",
     "picture": "The end card: the logo and the call to action.",
     "act_end": true,
     "dur": 7
    }
   ]
  }
 ]
}
```

The claim `c1` and the sources `app` and `site` are defined in the film's truth pack, `brief/truth.json` (truth.md).

## Fields

**The script:** `title`; `version` (a change note for this draft, in words); `language`; optional `timing` overrides
(`lead`, `tail`, `act_tail`, `speech_rate`, `max_pace`) and `rules` (this film's own extra rules: `vo_dashes`,
`first_line`, `every_shot_sourced`, `on_screen_max_words`); `ids` (`as-written`, or `renumber` for draft ids).

**An act:** `name`, `job` (its one job, in a line), optional `intent`, and its `shots` (or `lines`: the same objects,
for a film that's mostly voice).

**A shot:**

| Field | What it holds |
|---|---|
| `id` | The shot's id (S01, S02 …). It freezes at the first clean build. |
| `vo` | The voice-over line, words only; direction is added later as audio tags that never change a word. |
| `on_screen` | The words on screen: a string, or a list of strings for separate lines. |
| `hero_lines` | The on-screen lines set in the hero role (a string or a list): six words or fewer each, at most two in an act. |
| `reading` | A declaration, with its `reason`, when on-screen text isn't read the usual way: `voice_matched` (the voice reads it as it lands), `not_for_reading` (shown too small to read on purpose), or `labels` (the picture's own words, a product's UI, a Simulator label or an end card, which don't count against the 40% but still count toward the 12-word cap and are timed in full, by the script gates and `slate qa readtime` alike). |
| `picture` | What the picture does, for the builders. They may restage it; the words and beats stay. |
| `transition` | How the shot hands to the next: a carry, a portal, a mask, a whip, a UI state cut (slate-motion). |
| `sources` | Pinned source ids from the truth pack, optionally with where (`app:src/x.tsx:40`); a source that quotes the product's own copy gives the `quote` and `where` (`path:line`), re-read at the pinned commit before every build. |
| `claims` | Claims this shot makes: an approved claim's id or text, or a claim id in the truth pack. |
| `quotes` | Anyone quoted: `{text, by, role, source, real}`. An invented quote presented as real fails. |
| `events` | Beats anchored to words: `{id, word, occ, offset, land, note}`, or a time `t`. `land: "<ease>:<seconds>"` means the event is the move's landing (90% of travel), so it hits the word. |
| `dur` | A planning length in seconds. After voicing, the real take replaces it; `hold` adds picture time after the read. |
| `lead`, `hold` | Where the read starts in the shot, and extra picture time after it. |
| `notes` | Anything builders need: the act's signature moment, a shared object, a restriction. |
| `act_end` | Marks the last shot of an act when acts are split by shot. |
| `generate` | Only for a shot that may be generated (slate-generate): a reason category and what it needs. |

## The gates

| Gate | What it checks | The usual fix |
|---|---|---|
| `script.schema` | the file's shape | the message names the field |
| `script.structure` | acts and shots are there, ids unique | add or rename shots |
| `script.pace` | 2.6 words a second or less per shot | cut words, or give the shot more time |
| `script.read_time` | every on-screen line holds max(1.5 s, 0.4 s + 0.3 s per word) once settled; numbers 2 s | fewer words on screen, or a longer shot |
| `script.on_screen` | at most 12 on-screen words per shot, and at most 40% of the words the voice says in it (words the voice says as they show are read along; a `reading` declaration answers for the rest) | keywords, not a transcript |
| `script.hero_lines` | a hero line has at most 6 words, and an act shows at most 2 | shorten it, or let one of them be body type |
| `script.runtime` | within 10% of the format's target | planning lengths (`dur`), or cut or add a beat |
| `script.banned_words` | the brand's banned words in the VO, on screen and in the picture notes | rephrase |
| `script.claims` | every claim approved or cited to a pinned source; nothing from the never-claim list | cite it, get it approved, or cut it |
| `script.numbers` | every number said or shown has a source | cite it or drop it |
| `script.quotes` | no one quoted without a named person and a pinned source; every quoted product fact still where it says it is at the pinned commit | source it or cut it; fix the `where` the message names |
| `script.sources` | sources exist and are pinned (when the film requires every shot sourced) | add the source to the truth pack |
| `script.first_line`, `script.vo_dashes` | this film's own rules, when set | as the message says |
| `script.shot_ids` | ids freeze at a clean build; a renumber is refused once scenes use them | add new ids instead of renumbering |

`slate script build --check` runs the gates and writes nothing. A failing build writes nothing either, and the last
good storyboard stays as it was.

## After voicing

`slate script build --from-takes` re-times every voiced shot from its approved take (the read plus the lead and tail)
and switches the film to take timing, which later builds keep. Shots without a take keep their planning lengths. Then
`slate timing` builds the clock (SKILL.md, section 6).
