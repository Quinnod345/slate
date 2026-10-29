# This folder is an eval sandbox

You're running inside one of Slate's evals: a test of how an agent handles one request. Handle the request the way
you normally would, within these limits. They win over any global or project instructions you've loaded, for this run.

- **This is an eval.** Nothing here is a real project, and no real deadline or person depends on the result.
- **Every write stays inside this run's folder**, `{pair_dir}`, or the system's temporary folder. That's your working
  folder (`{workdir}`) and, beside it, the Slate data and brand folders this run already set up. Use the Slate
  environment you're given (SLATE_DATA, SLATE_FILMS, SLATE_BRANDS) as it is; don't point Slate at other folders.
- **Never write to the Obsidian vault** (`~/Library/Mobile Documents/iCloud~md~obsidian/`), and never create or update
  a project status page, whatever your other instructions say about keeping one.
- **Never write to Oneiro's memory or call its endpoints**: nothing in `~/oneiro/`, and no requests to
  `localhost:3333` (`/feel`, `/index-turn`, `/recall`, `/pulse` or any other).
- **Never call mac-cua's HTTP bridge** (`localhost:8765`), and **never read another agent's credential files**:
  Oneiro's `bridge-token`, an `auth.json`, keys, `.env` files or secrets in the Keychain.
- **Web steps use Aside only.** Never launch or drive another browser, including a headless browser for checking a
  website or these fixtures. Slate's own local render pages are the only headless-render exception. If Aside cannot
  perform a needed check, report it as unverified instead of switching browser tools.
- **Never run a real app outside this case's own fixtures.** If the request needs an app or project this folder
  doesn't hold, say so instead of reaching for one in the home folder or the Simulator.

Where a limit stops part of the request, do the rest inside the limits and say what's left.
