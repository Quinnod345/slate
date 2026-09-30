# Intake: the brief, in the requester's own words

Intake turns a request into a film folder with the goal written down exactly as it was asked. Everything later is
checked against that record, so it has to be complete and verbatim.

## 1. Capture every ask

Big requests get half-delivered when one ask quietly drops out. It's almost never the headline; it's the aside, the
subordinate clause, the "oh, and also" at the end, the second message an hour later.

- Split the request on every "and", "also", "plus", list item and comma-separated clause.
- Write implied deliverables out: "a reel with captions for Instagram and TikTok" is a reel, burned-in captions, and
  two platforms' safe zones.
- Keep vague asks as intent items ("make it feel less corporate") instead of dropping them because they aren't crisp.
- Carry open threads: a question the requester asked that nobody has answered yet is an ask too.
- If anything is ambiguous, restate the list back so they can correct it.

## 2. Write it down verbatim

```
slate new <film-id> --brand <brand> --format <format> --request "<the request, word for word>" --source "<who asked>"
```

- `--request` takes the words exactly as they arrived, typos included. `--request-file <path>` reads a long one.
- `brief/GOAL.md` then holds the request, a checklist row per ask (auto-split rows are flagged for you to check) and the
  settings rows (format, brand, budget, mode). `brief/PRODUCTION.md` is the live tracker (see tracker.md).
- Every later message that adds or changes an ask goes in verbatim too, with who sent it. GOAL.md names the command.
- If the original request was cut off or summarized away, say so in the tracker, and mark any ask you rebuilt from
  follow-ups as reconstructed.

**After any summary or compaction, re-derive the checklist from the verbatim request, never from memory.** Summaries
drop sub-requirements; assume something was lost and go find it.

## 3. Check the brand

`slate brand validate <brand>` reports the pack and which of its four references are approved.

| What you find | What happens |
|---|---|
| No pack for this brand | Onboarding first (`slate brand init <brand>`, then slate-brand). No script before it. |
| A pack with references still unapproved | Finish onboarding (`slate brand onboard <brand>`) before the script. |
| The neutral `demo` pack | Go ahead: it's for trials, tests and demos; its look test rides in the film's pre-production sitting. |
| All four references approved | Go ahead. The film's look test is skipped; later work is checked against the references. |

For a brand the owner doesn't own, onboarding names a claim authority there: the person who rules on claims and brand
use for that company. "A video for <company>" means that company's film, with their approvals.

## 4. Choose the format and the settings

- **Format** (formats.md): it sets runtime, aspect, pace, structure, loudness target, caption defaults and each
  platform's safe zones. The runtime stays within 10% of the target unless the requester asks otherwise or you justify
  it in the tracker.
- **Mode** (modes.md): supervised unless the requester chose otherwise. Autonomous runs are for agents working for the
  owner, on approved brands only.
- **Generation**: the requester decides how much generated footage they want: `none`, `rarely` (the default) or
  `where-it-helps`. Code builds anything designed either way.
- **Budget**: the format's default (budgets.md) unless the requester set one. It's a hard stop.
- **Language**: English in this version. The clock, captions and transcripts carry no English literals, so other
  languages come later as derived films.

## 5. Inputs

Copy attachments (an original film to remake, reference films, a script, brand files) into the film folder, for example
`brief/inputs/`, so later stages have stable paths. Record where each came from. Never move or edit the originals, and
never copy company source code: read it in place (slate-story, truth.md).

## 6. Before the first stage

- Run `slate status`: it names the next command (usually the script).
- The script comes from slate-story. The pre-production sitting waits for the checklist, the claims, the script, the
  voice shortlist and, when the brand's look isn't approved, the look test.
- When the requester says how far to go ("take it as far as the script"), stop there and say what's next.

## Asking what intake can't proceed without

Ask as few questions as the work allows, one decision at a time. For a real decision, name it in one line and ask a few
concrete sub-questions whose answers settle it: facts about their situation, what they'd prefer in practice, the edge
case. If your agent has an interactive question tool, use it. Then say which way the answers point, flag any conflict
between them, and read free-text answers literally: they often carry the real intent.
