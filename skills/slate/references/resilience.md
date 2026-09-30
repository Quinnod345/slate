# Resilience: work that outlives the agent doing it

A production can run for days. Agents get cut off by rate limits, sessions end without warning, Macs sleep, and
research slices are killed after ten minutes. During the first films one rate limit stopped eight agents at once, and
a builder lost work that wasn't on disk yet. Slate is built so none of that loses anything.

## The film folder is the memory

- Everything a film knows is in its folder: film.json (clock, size, pins), film.lock.json (what made each file, from
  which inputs), approvals, the brief, the gates' results, the renders registry.
- `slate status` reads only the folder, so any agent in any session sees the same truth. Start every session with it.
- Pins make week three match week one: the voice, model, settings, seeds and request ids are recorded at first use, and
  a registry refresh never changes a pinned film. Re-pinning is explicit, tested and approved.

## Long work runs as detached jobs

- Renders, masters, word timings, score composition, generation batches and full QA passes go through the machine-wide
  job queue. `--detach` returns a job id at once; the job runs under caffeinate, in its own session, and resumes by
  frame, line or act after a crash.
- Follow a job with `slate job status <id>` and `slate job log <id>`; stop one with `slate job cancel <id>`.
- The queue holds one lock per resource (render slots, After Effects, each vendor's concurrency, disk), and supervised
  films outrank autonomous ones. Never work around it by running a second heavy process yourself.
- Every paid call writes an in-flight ledger row with its request id before waiting, so a restart polls that request
  instead of paying twice.
- Each job reserves its disk first and stops safely before the disk fills.

## Nothing prompts

- Some agents run with stdin as a pipe and are killed after ten minutes. Never start anything that waits for input, and
  never leave a command running in the foreground that outlives your turn; queue it.
- Keys are typed by a person in their own Terminal (`slate connect elevenlabs`, `slate connect higgsfield`), never in
  chat and never by an agent. A missing key is exit code 3 with the command for the person to run.
- A vendor rate limit sends the job back to the queue to retry later, not into a loop.

## Save early and often

- Get a first working version of every shot onto disk fast, then refine. A placeholder act renders from day one, so the
  whole film always plays.
- Keep NOTES.md current as you go (slate-build): decisions, restaging, what's left. The next agent reads it.
- Commit small, often: the film folder is a git repository for its small files (heavy media is ignored).

## When a decision changes, change the notes too

When a standing decision changes (where renders run, which voice, which sound design), update or retire every memory
note, tracker line and doc that states the old rule. On the first films, two notes still sent renders to a machine that
had been retired, and an agent that trusted them would have followed them.

## Don't pull the floor out from under other agents

- Never move, rename or archive files that running agents read. Clean up only after the builders finish, and archive
  with a note.
- Give each parallel builder its own scratch folder; they overwrite each other's otherwise.
- One heavy process at a time on a machine; the queue enforces it.

## Handing over

When you stop (a sitting, the end of a session, a blocker):
- run `slate status --write` so PRODUCTION.md shows where things stand;
- take the closing ledger snapshot and reconcile (budgets.md);
- say plainly what's done (with evidence), what's in progress (with job ids), and what's waiting on whom.
