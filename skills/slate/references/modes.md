# The four modes

A film's mode is in film.json (`slate new --mode`). The CLI enforces each mode's rules whatever an agent intends:
refusals come back with exit code 1 (refused) or 4 (spend refused) and say why.

**A film made autonomous stays autonomous.** `slate new` and `slate variant` record each film's mode in Slate's own data
folder, outside the film, and every rule (spend, keys, After Effects, the queue, review routing) judges the film by that
record, whatever its film.json says now. Only a person at their own Terminal changes it, with
`slate film set mode <mode> --by NAME --reason "…"`. An agent's attempt is refused and recorded, and an edit of
film.json changes nothing (`slate status` shows the mismatch). A derived film starts in its parent's mode.

## Supervised (the default)

- **Two sittings block:** pre-production (the checklist, the claims, the script, the voice shortlist, the look test)
  and the final cut. Stop at each and hand over.
- **Packets don't block:** hero moments, generated takes and sound proofs arrive in review packets the owner answers
  when they like.
- **Spend past the film's budget asks first.** The budget is a hard stop; the owner can record a raise.
- **After Effects finishes the film**, as an editable project plus the masters, and only on the owner's go. Ask them
  to save and quit AE before masters render; never quit it for them.

## Express

- A short film runs straight through to a review cut.
- Every approval that would have blocked earlier (checklist, claims, script, voice, look) is batched into the final-cut
  sitting. Nothing is skipped; it's just asked once, at the end.
- Use it for short pieces where the requester wants to see something before deciding anything.

## Draft

- No generated plates, a library voice, fewer motion-blur samples (4 instead of 16).
- For animatics, quick variants and trying an idea.
- **Never delivered as final.** A draft that becomes the real thing leaves draft mode and goes through the gates again.

## Autonomous (agents working for the owner, such as Oneiro's pursuits)

- **Brands:** only brands whose four references are approved.
- **Claims:** only from the brand's approved-claims list. A new claim parks the film with a question for the owner.
- **REVIEW verdicts park the film** with their evidence, and never count as passes. Only a person releases it.
- **Spend** is capped monthly in Slate's own budget file, which the CLI enforces whatever film.json says, and backed by
  vendor caps: a separate ElevenLabs key for autonomous work, with its own credit quota. An autonomous film can't raise
  its own budget.
- **Every generation waits for the owner's yes**, asked by iMessage, recorded, and passed to the run as an approval id.
- **No After Effects.** Autonomous films are mastered with ffmpeg and held. The owner can start the AE finish
  themselves once they approve the held film: a final-cut approval of its held master, then `slate master --ae --go` in
  their own Terminal (never queued, never `--detach`). No agent starts it.
- **Voices:** the owner's own professional clone is the only clone it may use.
- **Default output:** a 15 to 45 second 9:16 reel.
- **Before parking,** a separate reviewer agent grades the contact sheet and the audio against the brand's references,
  the owner's taste notes and the visionary bar (slate-qa, reviewer-agent.md).
- **Not approved for publishing:** every output says so in its manifest until the owner approves that exact SHA-256.
  After their yes, the agent posts that exact file through its own committing path, and never any other version.
- **Denied tools:** publishing through a vendor connector (TikTok, website publishing) and any tool that uploads the
  user's skills or a personality profile to a vendor.
- **Held films and questions** reach the owner by iMessage, with the phone cut, and as a push notification.

## Choosing a mode

| Situation | Mode |
|---|---|
| The owner (or someone they work with) is making a film and wants to steer it | supervised |
| A short piece, and they'd rather react to a cut than approve a script | express |
| Trying an idea, an animatic, a variant to compare | draft |
| An agent working on the owner's behalf, on an approved brand | autonomous |
