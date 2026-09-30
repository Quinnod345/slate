# Orchestration: running a production

The production method is a workflow with blocking human approvals. docs/METHOD.md in the Slate repo has the full table;
this is how to run it well.

## The shape that worked

1. **Goal first.** The request verbatim and a checklist of every ask (intake.md).
2. **Toolchain proven** before anything expensive: `slate doctor` goes green, or names what's missing.
3. **Reference analysis** when there's an original film or reference films: `slate analyze <video>` (transcript,
   scenes, measured eases).
4. **Truth**: pinned sources, the claims list, sample data (slate-story).
5. **Motion law**: the brand's, from onboarding (slate-brand, slate-motion).
6. **Treatment and script as data**, through the script gates (slate-story).
7. **Voice by ear**, then every line, then timing from the real reads (slate-voice, slate-story).
8. **Look test** if the brand's look isn't approved (slate-motion).
9. **Kit first, then the builders' brief**, then the acts (slate-build).
10. **Generated shots** only where allowed and code can't do the job (slate-generate).
11. **Sound and score**, then the mix (slate-sound).
12. **Central finals**: renders, the AE assembly and masters (slate-finish).
13. **Validation** (slate-qa), **delivery**, and **the review loop**.

## Parallel production

If you can run sub-agents, build acts in parallel; otherwise build them in order. The parallel shape that worked:
- act builders who each own only their act's files, under one layered brief;
- one sound designer working in parallel from the builders' exported beats;
- a separate, timeboxed track for any alternative approach being tested;
- finals and sound done centrally, one at a time, never by the builders.

Research can be delegated the same way (if you can run sub-agents; otherwise do it in turn): a character's philosophy
into a voice brief, a motion spec from real references, a capability brief where every line carries a source. Every
delegated brief says what to return and where every claim came from.

## Choosing the production route

Before building, compare the candidate routes for the film (built in code with the Remotion kit, built in After
Effects, the open web renderer, generation for real-world shots), recommend one, and timebox a test of the strongest
alternative if it's a close call. Record the choice and the owner's approval. The engine can be chosen per brand or per
act during look development.

## One clock

Every beat is keyed to the voice's words, and picture and sound read the same resolved timeline. So a full re-voice
re-times the film automatically; the renders and the mix still have to be rebuilt, and `slate make <target>` rebuilds
only what's stale, in order. `slate make all` goes through to delivery; `slate make --list` shows each node's state
without running anything.

## Keeping intention across parallel versions

Intentionality degrades when several films or acts are built at once. Tie every move to a word or an idea, prefer fewer
and better moves, and judge new work against the best earlier film's code and notes. Hold masters until the pass is
done.

## Machines

- Render locally, one heavy job at a time, through the queue. Never on a borrowed machine or the owner's work computer.
- Too much parallel rendering thrashes a Mac: six builders plus finals once drove the load to 293 on 14 cores. The
  queue's per-resource locks and its load guard exist for this.
- Keep heavy media out of cloud-synced folders; films live in `~/Movies/Slate/` by default for that reason.
- Never edit a shell script while a job is running it (zsh reads scripts incrementally).
- One copy of any MCP client at a time: two copies ran every action twice.

## Evaluating a suggested tool

When someone suggests a library, an MCP server or a skill, check it quickly and record the verdict with reasons: does
the repository exist, does it do what it claims, does it write files or only play live, would it open a browser, does
it need system permissions (those installs go to the owner)? See dependencies.md.
