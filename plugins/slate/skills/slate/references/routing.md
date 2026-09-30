# Routing: getting every video request to the right place

Slate claims any request to make, edit, plan or review a video. Higgsfield's skills also claim "make a video", so the
front door has to win the request and then send it to the right lane. Routing evals check this in every agent (Claude
Code, Codex, a pursuit slice, OpenClaw): open requests get the question, scoped briefs load Slate, UI recordings still go
to video-verifier, and unrelated requests never load Slate.

## What counts as a video request

Explainers, ads, social reels, promos, launch and feature announcements, teasers, product demos, brand films, website
hero loops, App Store previews, kinetic type, logo stings, character-led pieces, cut-downs and new aspects of a film,
voice-overs and captions for a film, a review of a cut, and delivering or posting a finished film.

What doesn't count, even when the word "video" appears:
- styling, embedding or debugging a `<video>` element or a hero video's CSS (ordinary web work);
- recording an app or site to verify its UI, transitions or motion (video-verifier's job);
- an email, a document, a single still image, a standalone thumbnail for someone else's video.

## The one question

An **open** request names no length, no format, no brand and no destination, and doesn't pick a lane: "make a video",
"can you make me a short video of a paper boat in a storm?", "I need a video about our app".

- Ask exactly one question, and nothing else in that message:
  *"Do you want a full production or a quick generated clip?"*
- Don't run any command and don't write any file before the answer. Not `slate new`, not a draft script.
- Don't bundle the brand, length or format into the same message; they come next, once the lane is chosen.
- One line of context is fine if it helps them choose: a full production is scripted, voiced, designed, scored and
  checked, and takes sittings; a quick clip is one generated shot in minutes, with no script or brand checks.

After the answer:
- **Full production:** intake (SKILL.md section 3). Now ask what's missing: the brand, the length or format, where it
  will be shown.
- **Quick clip:** the fast lane (below), unless a brand turns out to be involved.

## Scoped briefs go straight to production

A request is scoped when it names any of: a length ("45-second"), a format or aspect ("a 9:16 reel", "a 6-second ad"),
a brand ("for the demo brand", "for our company"), a destination ("for our App Store page", "for LinkedIn"), or when it
comes with a script, storyboard or approved plan. Examples:
- "make a 45-second launch video for <brand>";
- "cut a 15-second 9:16 reel for <brand>'s spring sale, with the captions burned in";
- "turn this script into an explainer".

Don't ask the lane question for these, and don't mention a quick clip at all. Go to intake. Ask only what intake can't
proceed without, and ask it as its own question.

## The fast lane

The fast lane is Higgsfield's own skills: **higgsfield-video-explainer** (a narrated, non-photoreal explainer built from
10-second blocks with one style key) or **higgsfield-generate** (a single generated clip). Use it only when:
1. the requester picked it, at the question or up front ("a quick generated clip please, not a full production"); and
2. no brand is involved.

Once picked, don't ask the question again and don't make a Slate film (no `slate new`). Hand over to the Higgsfield
skill and let it run.

**Credits.** Outside Slate, Higgsfield's skills generate through its CLI, which bills app credits: a separate pool from
the Cloud API balance Slate uses, signed in by the owner in their own Terminal. That pool isn't funded by default. If the
CLI reports no credits or no sign-in, don't try to fix it (never buy credits, never run a browser sign-in from an agent):
tell the requester, and offer the same shot as a small Slate production, where a generated shot runs on the Cloud API
with an estimate first.

**Never for brand work.** A brand's film goes through Slate even when the ask says "a quick Higgsfield clip for
<brand>'s launch". Stylized generated clips aren't the brand's type, colour or real UI, and the fast lane skips truth,
claims, the ledger and take judging. Say that in one line, then run a short production: express mode suits it, with
`--generation where-it-helps` when the requester wants generated footage.

## UI recordings go to video-verifier

"Screen-record the sign-up flow in the Simulator and check the transitions frame by frame" is QA of an app, not a film.
Load video-verifier, not Slate. Its web recordings go through Aside, never another browser.

A **product demo film** that uses real app footage is Slate's: the footage comes from `slate capture ios` or
`slate capture web`, conformed to the film's clock and beat-mapped to the voice (slate-build).

## Editing and reviewing

- **Editing a Slate film:** notes on approved work are a revision of that exact artifact (perfect, don't replace); a
  cut-down, a new aspect or a new language is a derived film (slate-finish).
- **Editing an outside video:** `slate analyze <video>` breaks it down (transcript, scenes, measured eases); then either
  rebuild it as a Slate film or, for a trivial trim, make the trim and say that's all it was.
- **Reviewing a cut:** a Slate film's review packet comes from `slate review`; an outside film gets `slate analyze` and
  the review checklist in slate-qa.

## Posting and publishing

Nothing publishes itself. A request to post a film is refused unless the owner approved that exact file: its SHA-256
matches their approval, and the delivery manifest marks it approved for publishing. When either is missing, say exactly
which approval is missing and stop. Don't upload it anywhere, don't open a site to post it, and don't take any other
committing action on the requester's behalf.

## Out of scope in this version

Shooting live action, and audio description. Say so, then offer what Slate can make: a film from real app captures,
generated real-world shots where the generation setting allows, or captions and a transcript.

## When a vendor skill keeps winning

If Higgsfield's (or any vendor's) skill keeps taking requests Slate should get, adjust Slate's own trigger (the skill
descriptions), with the owner's one-time permission, and re-run the routing evals. Never delete or edit a vendor skill.
