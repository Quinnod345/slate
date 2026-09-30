# Plan tracker

Every point of "Slate — Plan" (the vault copy), with its status and evidence. A point is **done** only when it is built and verified, or, where it needs Quinn, prepared and waiting on him with that stated. Generated from the plan, then kept by hand.

| ID | Section | Point | Status | Evidence |
|---|---|---|---|---|
| P001 | Slate — Plan | - **Name:** Slate. | open | |
| P002 | Slate — Plan | - **After Effects:** a full creative engine. It finishes every film you supervise and is a second way to build scenes, using the AE bridge's 13 motion-design skills as looks. | open | |
| P003 | Slate — Plan | - **Oneiro:** capped, and every film held for your approval. | open | |
| P004 | Slate — Plan | - **Who it's for:** yours first, shareable later, with company brand packs on PERSONAL only. | open | |
| P005 | The promise | 1. **Download.** One command clones Slate and runs its installer. | open | |
| P006 | The promise | 2. **Connect Claude.** Slate's skills, command-line tool and MCP server go into Claude Code, and into Codex and OpenClaw when they're on the machine. | open | |
| P007 | The promise | 3. **Connect Higgsfield.** You sign in yourself in Terminal; Slate never opens a browser for you. An optional Cloud API key goes into the Keychain. | open | |
| P008 | The promise | 4. **Connect ElevenLabs.** An API key, typed into Terminal (never into chat) and stored in the Keychain. | open | |
| P009 | The promise | 5. **Connect After Effects.** Slate finds AE and aerender, checks permissions and fonts, installs Higgsfield's AE bridge, and renders a 1-second smoke test. Building scenes in AE through the bridge needs AE's "Allow Scripts to Write Files and Access Network... | open | |
| P010 | The promise | \| Homebrew tools: ffmpeg, Rubber Band (arm64), uv, Node ≥ 24 \| Free \| The installer, asking first \| | open | |
| P011 | The promise | \| Whisper model (1.6 GB), Remotion engine and headless Chrome \| Free \| The installer, asking first \| | open | |
| P012 | The promise | \| GarageBand's sound library (the sampled instruments proof 2's sound uses) \| Free \| You, from GarageBand's Sound Library \| | open | |
| P013 | The promise | \| Xcode command-line tools (only for iOS app captures) \| Free \| You \| | open | |
| P014 | The promise | \| After Effects \| Paid (Creative Cloud) \| You \| | open | |
| P015 | The promise | \| AE's "Allow Scripts to Write Files and Access Network" pref (needed to build scenes in AE) \| Free \| You, in AE's scripting settings (done on this Mac) \| | open | |
| P016 | The promise | \| ElevenLabs: Creator works; Pro or higher for lossless voice \| Paid \| You \| | open | |
| P017 | The promise | \| Higgsfield Cloud API balance (plates, optional) \| Paid, per generation \| You \| | open | |
| P018 | The promise | \| Remotion licence, only if a company's published film needs one (see Risks) \| Free for individuals, pilots and teams of up to 3 \| You, when you decide to publish \| | open | |
| P019 | The promise | \| Brand fonts \| Licensed per brand \| The brand \| | open | |
| P020 | The promise | - **Inside a film:** act builders work in parallel wherever the agent can run sub-agents (Claude Code workflows do). | open | |
| P021 | The promise | - **Across films:** one machine-wide job queue lets your EOS film and Oneiro's Inner Echo reel run on the same Mac without fighting over CPU, After Effects or API limits. | open | |
| P022 | The promise | - **Express mode:** a short film runs straight through to a review cut, with its approvals batched at the end. | open | |
| P023 | What we're packaging | - **Visual engine.** One Remotion project with a shared kit: the film clock, the motion law, 2.5D paper cards, type, app UI primitives, the logo build, charts, and a character rig (Trudy's). `render_mb.mjs` renders true motion blur, 16 sub-frames averaged i... | open | |
| P024 | What we're packaging | - **Timing.** `storyboard.json` → `shots.json` → `timeline.json`, anchored to Whisper word timings, so every move, sound and word share one clock. | open | |
| P025 | What we're packaging | - **Voice.** Casting shootouts, Voice Design with measured ranking, eleven_v4 with neighbouring-line context and audio tags, cleanup, pacing, alignment and captions. | open | |
| P026 | What we're packaging | - **Sound.** Proof 2's design, being perfected right now in `sound/p3/` with your notes: | open | |
| P027 | What we're packaging | - event-tied wood, air, subs, small felt notes and pads, with tonal roles on real sampled instruments; | open | |
| P028 | What we're packaging | - one deliberate sound per kind of interaction, with natural variation between repeats; | open | |
| P029 | What we're packaging | - no glides, no slide-ups, no room-tone drone; | open | |
| P030 | What we're packaging | - effects about 11 LU and UI clicks about 17 LU under the voice, every sound within a frame of its beat. | open | |
| P031 | What we're packaging | - **After Effects.** An AEP built from data, chunked aerender masters, colour-managed (Rec.709 Gamma 2.4). | open | |
| P032 | What we're packaging | - **QA.** Film-level gates: read time by OCR, sync, loudness, banned words, the frame-glitch scan, master-vs-render PSNR and more. | open | |
| P033 | What we're packaging | - **Method.** Goals kept in the requester's own words, truth packs, look tests, kit-first foundations, parallel act builders with frame-exact contracts, review cuts. | open | |
| P034 | What we're packaging | - **Knowledge.** `MOTION.md`, `TRUDY_MOTION.md`, `SOUND.md`, `V4_TAGS.md`, 20 act `NOTES.md` files (3,565 lines) and the memory notes, most of it hard-won. `SOUND_V2.md` is kept as a record only. | open | |
| P035 | How it fits together | 1. **One source of truth, many front doors.** One git repo feeds Claude Code, Codex, OpenClaw and the Claude desktop app. | open | |
| P036 | How it fits together | 2. **CLI-first.** Every stage is a command. Oneiro's research agents can't see MCP servers, and every agent can run a shell command, so the CLI is the contract and MCP is a convenience. | open | |
| P037 | How it fits together | 3. **No brand in the core.** Brands are data packs, and every brand gets its own look. TrueSpace becomes one private pack. | open | |
| P038 | How it fits together | 4. **Film as data, one clock.** Sync is guaranteed by construction, not fixed after the fact. | open | |
| P039 | How it fits together | 5. **Gates, not vibes.** Every step with a measurable result is gated by a machine. The look, hero moments and final cut are judged by you. Nothing unchecked is ever reported as passed. | open | |
| P040 | How it fits together | 6. **Spend is explicit.** Estimates before generating, one ledger, a budget per film and per month. Slate never buys credits, upgrades a plan or starts a licence; when a balance runs out, the work stops and asks. | open | |
| P041 | How it fits together | 7. **People start After Effects, and nothing publishes itself.** AE finishes every film you supervise and builds scenes when a look calls for it. Oneiro's autonomous films never touch AE: Adobe's enterprise terms (PSLT §1.2) bar operations a person didn't s... | open | |
| P042 | How it fits together | 8. **Perfect, don't replace.** Approved work plus notes means edits to that exact artifact. A redesign needs your yes. | open | |
| P043 | 1. Skills: what the agent knows | \| `slate` (front door) \| Intake, formats, routing, brand onboarding, approvals, budgets, the production tracker, `slate status` \| GOAL/PRODUCTION trackers, goal-fidelity \| | open | |
| P044 | 1. Skills: what the agent knows | \| `slate-story` \| Treatment, script as data, truth packs, VO writing (≤ 2.6 words/s), one signature moment per act \| storyboard gates, C's treatment and capability brief, visionary \| | open | |
| P045 | 1. Skills: what the agent knows | \| `slate-motion` \| The motion law, look and motion development, character and motif motion, anti-glitch rules, read time, safe areas \| MOTION.md, TRUDY_MOTION.md, the glitch catalogue, act NOTES; The Browser Company's craft as the north star (the feeling... | open | |
| P046 | 1. Skills: what the agent knows | \| `slate-build` \| Building scenes with the kit: act scaffolds and placeholders, contracts, events, hand-in checks, parallel builder briefs, app captures \| KIT.md, KIT_B/KIT_C, the placeholder act, review sheets \| | open | |
| P047 | 1. Skills: what the agent knows | \| `slate-voice` \| Cast or design a voice, direct it (v4 tags, never whisper, soft or quiet tags), voice, clean, pace, align, caption; the cloning policy \| TRUDY_VOICE method, V4_TAGS.md, the voice tools, your voice-tag notes \| | open | |
| P048 | 1. Skills: what the agent knows | \| `slate-sound` \| Proof 2's design and taste as a starting point, each brand's own palette, event spotting, one sound per interaction, composed scores, mix, verification \| proof 2, SOUND.md, the p3 rework once you approve it, your taste notes; SOUND_V2.m... | open | |
| P049 | 1. Skills: what the agent knows | \| `slate-generate` \| When to generate (rarely by default, as much as the requester wants); code first for anything designed; generated real people, real scenes and stock-style shots where code can't go; judging takes; best models; grounding; mattes; spend... | open | |
| P050 | 1. Skills: what the agent knows | \| `slate-finish` \| Renders, masters, derived films (cut-downs, aspects), captions, thumbnails, the delivery manifest \| the masters and AE lessons, review cuts, delivery manifest \| | open | |
| P051 | 1. Skills: what the agent knows | \| `slate-qa` \| Every gate, triage, review packets, "done means verified" \| validate_film, glitch_scan, QA.md \| | open | |
| P052 | 1. Skills: what the agent knows | \| `slate-brand` \| Brand onboarding: truth, look and motion, voice, sound, fonts; building a pack from a website, guidelines PDF or app \| RESEARCH.md's method, higgsfield-brandkit, ElevenLabs brand kits \| | open | |
| P053 | 1. Skills: what the agent knows | - Spec-only frontmatter, `SKILL.md` under 500 lines, real `references/` files for depth, relative paths. | open | |
| P054 | 1. Skills: what the agent knows | - Agent-neutral wording, with no Claude-only tool names: "If you can run sub-agents, build acts in parallel; otherwise build them in order." | open | |
| P055 | 1. Skills: what the agent knows | - `slate guide <topic>` prints the same reference files, so the skills never depend on the CLI to be readable. | open | |
| P056 | 1. Skills: what the agent knows | - `slate skills build` generates the per-client extras: a claude.ai zip with the references inlined, Codex sidecars (`agents/openai.yaml`) and OpenClaw entries. CI fails if a generated copy drifts. | open | |
| P057 | 1. Skills: what the agent knows | 1. `slate` claims any request to make, edit, plan or review a video, including explainers, ads, reels and promos. When a request is open ("make a video"), Slate asks one question first: a full production, or a quick generated clip? A clearly scoped brief ("... | open | |
| P058 | 1. Skills: what the agent knows | 2. Higgsfield's skills stay global, because you use them directly sometimes, and they're installed for Codex and OpenClaw too. Remotion's and ElevenLabs' skills are installed inside Slate's engine folder, not globally. | open | |
| P059 | 1. Skills: what the agent knows | 3. The fast lane (`higgsfield-video-explainer` or a Higgsfield clip) is used when someone picks it, and never for brand work. | open | |
| P060 | 1. Skills: what the agent knows | 4. Routing evals check that open requests get the question, scoped briefs load `slate` in every agent, and UI screen-recording requests still go to video-verifier. If a vendor skill keeps winning, Slate adjusts how it triggers, under your one-time permissio... | open | |
| P061 | 2. Engine: what does the work | \| **state** \| the three films' `build/` folders, `c_storyboard.py`, the input-hash watchers \| `slate new`, `slate status [--json]`, `slate make <target>`; `film.lock.json` (§3) records what produced every file, so stale steps rebuild and approved paid as... | open | |
| P062 | 2. Engine: what does the work | \| **timing** \| make_shots.py, timeline.py, vo_words.py, kit/time.tsx, c/kit/clock.ts \| Python resolves every frame; TypeScript only reads the resolved JSON. Word lookups never throw, and a miss list must be empty at hand-in. fps and size come from `film.... | open | |
| P063 | 2. Engine: what does the work | \| **visual kit** (Remotion) \| src/kit, c/kit, per-act helpers \| Brand-token adapter; declarative fonts with a gate that fails on substitution; logo build from a spec; a motif rig generalized from Trudy's (N-part marks, ripples, pulses, waves); exact-endp... | open | |
| P064 | 2. Engine: what does the work | \| **engine boundary** \| the relative imports 4–5 levels deep \| One versioned Node project (Remotion pinned exactly) under Slate's data folder. A film's scenes compile through a generated entry that aliases `@slate/kit` and `@film/data`, with a generated ... | open | |
| P065 | 2. Engine: what does the work | \| **looks** \| the "homepage mix"; the AE bridge's skills \| Paper 2.5D is a library look, used only where a brand's own design language is papery. The AE bridge's 13 motion-design skills (for example depth and space, the transition kit, UI mastery, matte ... | open | |
| P066 | 2. Engine: what does the work | \| **capture** \| video-verifier's `record_ios_sim.sh`; Oneiro's hand-built Inner Echo reels \| `slate capture ios\\|web`: the Simulator with status-bar overrides, a seeded demo account and a pinned build; web through Aside at a fixed viewport. Captures are... | open | |
| P067 | 2. Engine: what does the work | \| **render** \| render_mb.mjs, stills.mjs, splice_render.py, render_local.sh, c_renders_json.py \| `slate render`: chunked, resumable by frame count, proven splices, a renders registry, bundle cleanup everywhere \| | open | |
| P068 | 2. Engine: what does the work | \| **jobs** \| masters.sh's chunking, the watchers \| One machine-wide queue run by a per-user launchd agent. Jobs are detached from the agent that started them, run under `caffeinate`, and are resumable. One lock per resource: render slots, After Effects, ... | open | |
| P069 | 2. Engine: what does the work | \| **voice** \| elevenlabs_tts, voice_script, voice_c, trudy_voice_api, measure/rank_previews, tts_check, clean_test, remix_clean, tag_samples, tag_ab, vo_pace, captions \| `slate voice cast\\|design\\|tts\\|clean\\|pace\\|align\\|captions`, with a VoicePro... | open | |
| P070 | 2. Engine: what does the work | \| **sound** \| `sound_engine.py` and the p3 pass; plumbing from v2 per HANDOVER.md; the Eleven Music composer and assemblers (compose_C, assemble_B/C, score_qa) \| The effects engine starts once you approve proof 3 and A's p3 mix, and is re-inventoried the... | open | |
| P071 | 2. Engine: what does the work | \| **generate** \| hf_api, hf_plates, plate_conform, plate_ridge, bridge_matte \| `slate gen`/`slate plates`, Cloud API only: estimate first, ledger, budget check, best model for the shot unless the film overrides it, at least two takes judged, conform to i... | open | |
| P072 | 2. Engine: what does the work | \| **QA** \| validate_film, glitch_scan, boundary_check, qa_shot, banned_words_scan, ocr tools, validate.jsx \| `slate qa`: every tool takes the film folder. Adds `slate qa motion` and the new gates in "Quality". Fixes the known validator bugs (24-bit not g... | open | |
| P073 | 2. Engine: what does the work | \| **ae** (driver) \| run_jsx.sh, read_ae_report.py, lib.jsx, build_film.jsx, queue_masters.jsx, masters.sh, probes \| The AE driver: osascript DoScriptFile with the prefs report channel (version-sorted prefs folders), a dirty open project saved before any ... | open | |
| P074 | 2. Engine: what does the work | \| **ae** (creative engine) \| Higgsfield's AE bridge (fnf-after-effects-mcp) and its 13 skills; act1.jsx and smoke_build.jsx, which proved native AE builds; svg2ae.py \| A second way to build acts. AE-built acts run on the same film clock: timing comes fro... | open | |
| P075 | 2. Engine: what does the work | \| **open web renderer** \| your library list (2026-09-29): three, @react-three/fiber, @react-three/drei, GSAP, puppeteer, FFmpeg; Accum.tsx, the accumulation renderer \| A third engine that needs no Remotion licence. A local page renders a scene at any tim... | open | |
| P076 | 2. Engine: what does the work | \| **finish** \| review_cuts.sh, delivery_manifest.py \| `slate master`: AE finishes every film you supervise, making an editable AE project and the masters, and you start it. ffmpeg masters autonomous and draft films, which are held for you. `slate deliver... | open | |
| P077 | 2. Engine: what does the work | \| **analyze** \| reference-film analysis (shell commands today) \| `slate analyze <video>`: Demucs split, transcript, scene cuts, OCR, measured eases \| | open | |
| P078 | 2. Engine: what does the work | \| **secrets** \| Keychain reads scattered across tools \| Environment first, then `security find-generic-password`, then a non-zero exit that says "run `slate connect …` in Terminal". It never prompts outside `slate connect` and never prints a value \| | open | |
| P079 | 2. Engine: what does the work | \| **doctor / connect** \| PIPELINE.md's toolchain proof \| Checks binaries, versions, keys (presence only), AE permissions per host app, fonts, disk and the default browser, run from each real context (Claude Code, a Codex slice, the OpenClaw gateway). The... | open | |
| P080 | 3. The film folder, its state and its pi | - **One hand-edited source.** `slate script build` writes the storyboard, runs the script gates and refuses to write on any failure. After voicing, it re-derives durations from the real takes. Shot ids freeze at the build gate, and a renumber is refused onc... | open | |
| P081 | 3. The film folder, its state and its pi | - **State.** Gate results carry the hashes they checked and turn STALE when an input changes. Approved takes, score takes and plates are locked by content hash and are never regenerated without an explicit force and a spend approval. The timeline locks once... | open | |
| P082 | 3. The film folder, its state and its pi | - **Pins.** `film.json` records the TTS model, voice id, settings, seeds and request ids, the music and plate model ids, and the Remotion and Chrome versions, each at first use. A registry refresh never changes a pinned film; re-pinning is explicit, A/B-tes... | open | |
| P083 | 3. The film folder, its state and its pi | - **Derived films.** A cut-down, a new aspect or a new language is a derived film with a `parent`. It reuses the parent's approved takes, timings, beats and assets by reference, re-stages its layout with its own type scale and safe areas, re-plans its sound... | open | |
| P084 | 3. The film folder, its state and its pi | - **Language.** v1 is English-only, but the new clock, caption and transcript code carry no English literals. | open | |
| P085 | 4. Brands: onboarding once, then a pack | 1. **Truth:** sources of truth, a named claim authority (who rules on claims for this brand) and an approved-claims list. For a brand you don't own, such as EOS, that authority may be someone at EOS. | open | |
| P086 | 4. Brands: onboarding once, then a pack | 2. **Look and motion:** | open | |
| P087 | 4. Brands: onboarding once, then a pack | - Inputs: the brand's own components (website code, app screens or SwiftUI views, guidelines); two or three reference films run through `slate analyze` and `fit_ease`; the product's own motion values (CSS easing tokens, or SwiftUI spring response and damping). | open | |
| P088 | 4. Brands: onboarding once, then a pack | - Outputs: kit components rebuilt from the brand's design language; a brand `MOTION.md` and `motion.json` (named eases and where they come from, duration scale, staggers, overshoot policy, the brand motif's signature motion); a kit review sheet. | open | |
| P089 | 4. Brands: onboarding once, then a pack | - Gate: at least two moodboard directions grounded in the brand kit, then you approve a moving 10–15-second look-and-motion test with sound. Stills don't count. | open | |
| P090 | 4. Brands: onboarding once, then a pack | 3. **Voice:** an approved take, with its model, settings and seed. | open | |
| P091 | 4. Brands: onboarding once, then a pack | 4. **Sound:** a 10–20-second reference proof, with its levels measured against the voice. | open | |
| P092 | 5. Connectors, credentials and the brows | \| ElevenLabs \| API: eleven_v4 TTS, eleven_ttv_v3 design (always pass model_id), Voice Remix, Voice Isolator, Forced Alignment (to evaluate), Music on its best model (a score is part of most films); your professional voice clone once you've verified it \| ... | open | |
| P093 | 5. Connectors, credentials and the brows | \| Higgsfield \| Cloud API, the one funded lane, for every Slate generation \| Hosted MCP connector (OAuth) \| `HF_KEY` (id:secret), or Keychain `higgsfield-cloud-api`; `higgsfield auth login`, run by you \| Zero-cost probe (404 with the key, 401 without); ... | open | |
| P094 | 5. Connectors, credentials and the brows | \| After Effects \| aerender and ExtendScript through osascript for finishing; the AE bridge (fnf-after-effects-mcp, pinned) for building scenes \| None: chat can't reach local apps \| Adobe sign-in; macOS Automation permission per host app; "Allow Scripts ... | open | |
| P095 | 5. Connectors, credentials and the brows | \| Claude \| Skills, MCP server, CLI \| Knowledge-only skill uploaded to claude.ai; the Slate desktop extension (`.mcpb`) submits jobs \| None \| Skills listed once each; MCP connected \| | open | |
| P096 | 5. Connectors, credentials and the brows | 1. Every Slate child process gets `BROWSER=none`, and Remotion Studio always runs with `--no-open`. Previews are stills and MP4s, or pages opened through Aside. | open | |
| P097 | 5. Connectors, credentials and the brows | 2. `slate connect higgsfield` never runs the browser sign-in from an agent. It prints the one command for you to run in your own Terminal. | open | |
| P098 | 5. Connectors, credentials and the brows | 3. Web captures and any other agent-side web step go through Aside. video-verifier's Playwright path is never used. | open | |
| P099 | 5. Connectors, credentials and the brows | 4. Doctor reports the default browser and flags any step that would open it. | open | |
| P100 | 5. Connectors, credentials and the brows | 5. An eval checks that no browser other than Aside is ever launched. | open | |
| P101 | 6. Every agent: Claude Code, Codex, Open | \| Claude Code (Opus 5.5) \| Symlinked into `~/.claude/skills`; it doesn't read `~/.agents/skills` \| `claude mcp add --scope user` \| `~/.local/bin` \| Workflows run act builders in parallel. The plugin is for other people; you use symlinks \| | open | |
| P102 | 6. Every agent: Claude Code, Codex, Open | \| Codex (npm 0.153 and ChatGPT.app 0.155; gpt-6-astra by default) \| `~/.agents/skills` **only**. Both binaries also read `~/.codex/skills`, so installing to both would list every skill twice (it already happens for three skills) \| `codex mcp add` \| `~/.... | open | |
| P103 | 6. Every agent: Claude Code, Codex, Open | \| OpenClaw gateway (Oneiro main; deployed agents on gpt-5.6-sol) \| `~/.agents/skills` (the gateway uses the default state dir, so it reads that root) \| `openclaw mcp set` \| Also linked into `/opt/homebrew/bin`, because the gateway's PATH doesn't include... | open | |
| P104 | 6. Every agent: Claude Code, Codex, Open | \| Oneiro pursuit research slices (`codex exec --ignore-user-config`, gpt-6-sol) \| `~/.agents/skills` \| None: user MCP config is ignored \| `~/.local/bin` is on their PATH \| Killed after 10 minutes, with stdin a pipe. So everything long is a detached job... | open | |
| P105 | 6. Every agent: Claude Code, Codex, Open | \| Claude desktop chat and Cowork \| Knowledge-only skill on claude.ai \| Higgsfield and ElevenLabs connectors; the Slate desktop extension \| None \| Plans, writes and voices; starts renders only through the extension \| | open | |
| P106 | 6. Every agent: Claude Code, Codex, Open | 1. add one video capability line to its strategist prompt and composeBrief; | open | |
| P107 | 6. Every agent: Claude Code, Codex, Open | 2. accept Slate's spend interface, where Slate requires the actuator's approval id or a budget envelope granted to the pursuit; | open | |
| P108 | 6. Every agent: Claude Code, Codex, Open | 3. treat posting a Slate file without an approval record as a committing action that needs your yes; | open | |
| P109 | 6. Every agent: Claude Code, Codex, Open | 4. post the exact file you approved once you say yes, as its campaign plan says; | open | |
| P110 | 6. Every agent: Claude Code, Codex, Open | 5. send held films to you by iMessage, with the phone cut, and as a push notification. | open | |
| P111 | 7. The production method | \| 0 \| Brand onboarding, once per brand (§4) \| Approve the four references \| References recorded with hashes \| | open | |
| P112 | 7. The production method | \| 1 \| Brief: the request verbatim → checklist, format, brand, budget \| Pre-production sitting \| Every ask captured \| | open | |
| P113 | 7. The production method | \| 2 \| Truth: pinned sources, research, sample data \| Pre-production sitting (claims) \| Every claim on the approved list or sourced \| | open | |
| P114 | 7. The production method | \| 3 \| Treatment and script (`script.json` → `storyboard.json`) \| Pre-production sitting \| ≤ 2.6 words/s, read time, banned words, runtime ±10%, no unsourced numbers \| | open | |
| P115 | 7. The production method | \| 4 \| Voice: cast or design, then voice every line \| Pre-production sitting (shortlist) \| Whisper hears every word and the brand words; no leaked tails; each read fits its shot \| | open | |
| P116 | 7. The production method | \| 5 \| Timing: shots from the real reads, words, events \| \| Frame-exact and contiguous \| | open | |
| P117 | 7. The production method | \| 6 \| Look test (skipped when the brand's look is approved) \| Pre-production sitting \| \| | open | |
| P118 | 7. The production method | \| 7 \| Kit, builders' brief, act builds in Remotion or After Effects (in parallel where the agent can); captures \| Hero moments, in the packet \| Type check, empty word-miss list, bit-identical contracts, every act's events exported \| | open | |
| P119 | 7. The production method | \| 8 \| Generated shots (only when the film's generation setting allows, and code can't do the job) \| In the packet \| Estimate first; at least two takes judged; ugly or AI-looking takes never ship \| | open | |
| P120 | 7. The production method | \| 9 \| Score, sound and mix \| Sound proofs, in the packet \| −14 LUFS ±0.5, ≤ −1 dBTP, stems sum, cues within a frame, no repeats, no unplanned music gaps, levels against the brand's sound reference \| | open | |
| P121 | 7. The production method | \| 10 \| Render with true motion blur \| \| Frame counts, glitch scan triaged, act joins, motion checks \| | open | |
| P122 | 7. The production method | \| 11 \| Master: AE finish for supervised films; ffmpeg for autonomous and draft films \| Start the AE finish \| Master-vs-render PSNR ≥ 40 dB, exact frame counts, AE project audit \| | open | |
| P123 | 7. The production method | \| 12 \| Validate and deliver \| Final-cut sitting \| Every film gate passes; manifest written last \| | open | |
| P124 | 7. The production method | \| 13 \| Review and correct \| Notes \| The same gates, re-run on what changed \| | open | |
| P125 | 7. The production method | - Everything a film knows is in its folder, so `slate status` tells any agent, in any session, where it stands and what's next. | open | |
| P126 | 7. The production method | - Long work runs as detached jobs in the queue, under `caffeinate`, resumable by frame, line or act. A dying agent, a sleeping Mac or a rate limit doesn't lose it; during the films, one rate limit stopped eight agents at once. | open | |
| P127 | 7. The production method | - Builders get a first working version of every shot onto disk fast and keep `NOTES.md` current. | open | |
| P128 | 7. The production method | - Each job reserves disk space first and stops safely before the disk fills. | open | |
| P129 | 7. The production method | - **Supervised** (default): the two sittings, with spend past the film's budget asking first. | open | |
| P130 | 7. The production method | - **Express:** a short film runs straight to a review cut, with every approval batched into the final-cut sitting. | open | |
| P131 | 7. The production method | - **Draft:** no plates, a library voice, fewer motion-blur samples. For animatics and quick variants. Never delivered as final. | open | |
| P132 | 7. The production method | - **Autonomous** (Oneiro's pursuits): | open | |
| P133 | 7. The production method | - only for brands whose four references are approved, and only claims from the approved-claims list; a new claim parks the film with a question for you; | open | |
| P134 | 7. The production method | - REVIEW verdicts park the film with their evidence and never count as passes; | open | |
| P135 | 7. The production method | - spend is capped monthly in Slate's own budget file, which the CLI enforces whatever `film.json` says, and backed by vendor caps: a separate ElevenLabs key for Oneiro with a credit quota and TTS, music and alignment scopes. The proposed starting cap is 20,... | open | |
| P136 | 7. The production method | - every Higgsfield generation waits for your yes by iMessage; | open | |
| P137 | 7. The production method | - no After Effects (you can start the AE finish when you approve a held film); your own professional voice clone is the only clone it may use; | open | |
| P138 | 7. The production method | - default output is a 15–45-second 9:16 reel; | open | |
| P139 | 7. The production method | - before parking, a separate reviewer agent grades the contact sheet and the audio against the brand references, your taste notes and the visionary bar; | open | |
| P140 | 7. The production method | - every output is marked "not approved for publishing" in its manifest until you approve that exact SHA-256. After your yes, Oneiro posts that exact file through its normal committing path, and never any other version; | open | |
| P141 | 7. The production method | - denied tools: TikTok publishing and website publishing through the Higgsfield connector, and Higgsfield's `sync_agents`; | open | |
| P142 | 7. The production method | - held films and questions reach you by iMessage, with the phone cut, and as a push notification. | open | |
| P143 | 7. The production method | - **Voices:** | open | |
| P144 | 7. The production method | - Designed or library voices by default. | open | |
| P145 | 7. The production method | - Your own voice can be a professional clone, which you verify with ElevenLabs yourself. | open | |
| P146 | 7. The production method | - Anyone else's voice only as a real recording, never a clone, and never a public figure. | open | |
| P147 | 7. The production method | - Oneiro may use your clone; its films are still held for you. | open | |
| P148 | 7. The production method | - **People and claims:** no invented testimonials or customer quotes presented as real, which is also the law, and no claim without a source. | open | |
| P149 | 7. The production method | - **AI labels:** none unless the law requires one. Slate still records which voices and shots are synthetic in each film's manifest (internal only), so we can tell when a law does apply. Platforms such as TikTok, YouTube and Meta have their own rules that c... | open | |
| P150 | 7. The production method | - **Company material:** company source code is read in place, never copied. A TrueSpace truth pass reads the company code in place on WORK, read-only. Brand material goes to generators only when the brand owner allows it. | open | |
| P151 | 7. The production method | - **Spend and uploads:** Slate never buys credits, upgrades a plan or starts a licence, and never calls Higgsfield's `sync_agents`, which uploads a user's skills and a personality profile. | open | |
| P152 | 7. The production method | - **Browsers:** on your Macs, browsing and web capture go through Aside (§5). | open | |
| P153 | 8. Formats and costs | - **Brand film:** 2–6 minutes, 16:9 (A, B and C's shape) | open | |
| P154 | 8. Formats and costs | - **Explainer:** 60–120 seconds | open | |
| P155 | 8. Formats and costs | - **Product demo:** real app captures through `slate capture`. Capture for truth and speed; rebuild in code when a shot needs camera moves, restaging, motion blur or exact type | open | |
| P156 | 8. Formats and costs | - **Social reel:** 9:16, 15–45 seconds, captions burned in by Remotion in the brand's font (Inner Echo's reels, Instagram, TikTok, YouTube Shorts) | open | |
| P157 | 8. Formats and costs | - **Ad:** 6, 15 or 30 seconds, in 16:9, 1:1 or 9:16 | open | |
| P158 | 8. Formats and costs | - **LinkedIn cut:** 1:1 and 4:5 | open | |
| P159 | 8. Formats and costs | - **Website hero loop and App Store preview:** each to its platform's specs | open | |
| P160 | 8. Formats and costs | - **Launch teaser or feature announcement** | open | |
| P161 | 8. Formats and costs | - **Kinetic type or logo sting** | open | |
| P162 | 8. Formats and costs | - **Character-led:** a brand motif or mark as a character, like Trudy | open | |
| P163 | 8. Formats and costs | \| Reel or ad (6–45 s) \| VO 500–3,000, score 1,000–2,000 \| $0 by default \| Generation only when it's allowed and adds something. About 1% of a Pro month \| | open | |
| P164 | 8. Formats and costs | \| Explainer (60–120 s) \| VO 1,000–5,000, score 2,500–5,000 \| $0–60 \| 0–2 generated shots at about $15–30 each, including one reject \| | open | |
| P165 | 8. Formats and costs | \| Brand film (2–6 min) \| VO 4,000–12,000 (including tag A/Bs), score 9,000–28,000 \| $40–85 \| As on A and B \| | open | |
| P166 | 8. Formats and costs | \| Brand onboarding (once) \| Voice Design about 5,000 \| $15–20 for look tests \| The sound reference is made locally \| | open | |
| P167 | 8. Formats and costs | \| Remotion \| Per organisation, not per film \| \| $0 now: you're the only operator and the films are pilots. Revisit when a TrueSpace or EOS film is about to be published \| | open | |
| P168 | Quality: gates, tests and evals | - **Events complete:** every act in `shots.json` has a valid events file, or an explicit `silent: true` with a reason. Right now C's acts 1 and 2 have no events file, yet C's sync check passes. | open | |
| P169 | Quality: gates, tests and evals | - **Motion (`slate qa motion`):** | open | |
| P170 | Quality: gates, tests and evals | - only the brand's named eases, with durations from its scale, and stagger spreads within their caps; | open | |
| P171 | Quality: gates, tests and evals | - entrances start from at least 85% scale; | open | |
| P172 | Quality: gates, tests and evals | - peak speed ≤ 30 px/frame and acceleration ≤ 8 px/frame² at 1080p; | open | |
| P173 | Quality: gates, tests and evals | - no loop faster than a 16-frame period, and symmetry for mark rigs. | open | |
| P174 | Quality: gates, tests and evals | - **Glitch triage:** flags within ±3 frames of a planned beat are skipped, so smooth fast moves stop drowning real glitches (1,442 flags on B). | open | |
| P175 | Quality: gates, tests and evals | - **Accessibility:** | open | |
| P176 | Quality: gates, tests and evals | - at most 3 flashes a second; | open | |
| P177 | Quality: gates, tests and evals | - text contrast from the brand's token pairs (4.5:1 for body text, 3:1 for large text); | open | |
| P178 | Quality: gates, tests and evals | - captions at ≤ 17 characters a second as well as 2 lines of 42; | open | |
| P179 | Quality: gates, tests and evals | - 9:16 platform safe zones, checked on the contact sheet. | open | |
| P180 | Quality: gates, tests and evals | - **One OCR engine:** Apple Vision through the uv environment. When it's unavailable the gate is UNCHECKED, never a quiet pass from another engine. | open | |
| P181 | Quality: gates, tests and evals | 1. **Golden data.** The generalized timing, cue, caption and mix tools rerun on A, B and C's inputs and must match the delivered JSON within stated tolerances. The three films are a ready-made oracle. | open | |
| P182 | Quality: gates, tests and evals | 2. **DSP unit tests:** equal-power joins, loudness on known signals, stems summing to within 1e-6, the limiter-latency regression, verify anchors including overlapping notes. | open | |
| P183 | Quality: gates, tests and evals | 3. **Cross-language vectors:** eases, T90 and spoken-number matching, checked by pytest, vitest and an ExtendScript runner against one shared vectors file. | open | |
| P184 | Quality: gates, tests and evals | 4. **Golden stills** per kit component on software GL, at PSNR ≥ 45 dB, with the two-baseline rule on GPU (the GPU raster isn't bit-exact run to run). | open | |
| P185 | Quality: gates, tests and evals | 5. **Regression tests** for the four known validator bugs. | open | |
| P186 | Quality: gates, tests and evals | 6. **A replay mode** (`SLATE_FAKE_VENDORS=1`) that tests, evals and `slate demo` use by default, so they spend nothing on vendors; `--live` spends. | open | |
| P187 | Quality: gates, tests and evals | - Claude Code (Opus 5.5); | open | |
| P188 | Quality: gates, tests and evals | - Codex CLI on gpt-6-astra; | open | |
| P189 | Quality: gates, tests and evals | - the pursuit-slice setup: `codex exec --ignore-user-config` on gpt-6-sol, with only the aside and apple MCPs; | open | |
| P190 | Quality: gates, tests and evals | - OpenClaw main on gpt-5.6-sol. | open | |
| P191 | Quality: gates, tests and evals | 1. **Parity.** | open | |
| P192 | Quality: gates, tests and evals | - Data parity on all three films: timing, cue sheets and mix data regenerate and match what was delivered. | open | |
| P193 | Quality: gates, tests and evals | - Render parity on A's Act 4, a kit-built act, at Remotion 4.0.521: every frame ≥ 40 dB PSNR against the delivered render. | open | |
| P194 | Quality: gates, tests and evals | - Sound is checked against the approved p3 mix, not the retired v2 mix. | open | |
| P195 | Quality: gates, tests and evals | 2. **EOS:** a 45–60-second promo from a new Claude Code session, supervised. Done when you've watched it next to its brand references and approved it in your own words. | open | |
| P196 | Quality: gates, tests and evals | 3. **Inner Echo:** a 9:16 reel made by Oneiro's pursuit #27 in autonomous mode, on a topic Oneiro chooses, held for you and sent by iMessage and push. Done when you approve that exact file and Oneiro posts it. Target: by Oct 8. | open | |
| P197 | Build phases | \| 1. Foundations and wiring (starts today) \| 1–2 days \| Repo, `install.sh`, secrets, doctor, the job queue, the browser guardrail. A spike runs `slate doctor --json` from Claude Code's Bash, from a pursuit slice and from the OpenClaw gateway. File Oneiro... | open | |
| P198 | Build phases | \| 2. Tracer bullet: the Inner Echo reel, by Oct 8 \| 2–4 days \| A light Inner Echo onboarding (truth, look and motion, voice and sound references; your approvals needed by about Oct 4). Then a 15-second 9:16 reel, supervised first, with one Simulator capt... | open | |
| P199 | Build phases | \| 3. Depth \| 1–2 weeks \| Parallel workstreams, each shipping its module, skill, gate and tests: kit and looks, engine boundary, capture, voice, generate, QA and motion checks, finish, the AE creative engine (ES3 kit parity with the Remotion kit, the brid... | open | |
| P200 | Build phases | \| 4. Knowledge and evals \| 3–5 days, overlapping phase 3 \| The ten skills and their references, the knowledge ledger, `registry.json`, the eval matrix \| Nothing unmapped; evals pass in all four configurations \| | open | |
| P201 | Build phases | \| 5. Everywhere \| 1–2 days \| Installs for Claude Code, Codex and OpenClaw (including Higgsfield's skills); the MCP server and desktop extension; the claude.ai knowledge skill \| Each agent runs `slate demo`; the clean-machine test passes \| | open | |
| P202 | Build phases | \| 6. Onboarding and proofs \| About a week, mostly your review time \| Onboard EOS (with an EOS Worldwide approver for claims and brand) and finish Inner Echo's onboarding, then the parity and EOS proofs \| Both approved by you \| | open | |
| P203 | Build phases | \| 7. Ship and upkeep \| 1 day \| Private GitHub, plugin marketplace for other people, versioning, updates, the second knowledge harvest \| Updates reach every agent \| | open | |
| P204 | Build phases | \| 8. Fast variants \| 2–3 days \| Benchmark Higgsedit against a Remotion cut-down on the same film for social channels. Keep it only if it's genuinely faster at acceptable quality \| A measured yes or no \| | open | |
| P205 | Risks and licences | \| Remotion licence \| Free for individuals, for teams of up to 3 and for evaluation. Remotion's coming v5 terms count the people operating it, contractors included, and say receiving finished media isn't use. Under today's terms, company work at a company ... | open | |
| P206 | Risks and licences | \| Adobe terms \| The enterprise PSLT §1.2 bars operations a person didn't start. Your licence's consumer terms haven't been checked yet \| AE steps start only on a person's go; autonomous mode never uses AE \| | open | |
| P207 | Risks and licences | \| Eleven Music terms \| Online commercial use is fine on paid plans; film, TV and radio need Enterprise. Scores are now in most films \| Broadcast formats flag it \| | open | |
| P208 | Risks and licences | \| Platform AI rules \| You chose no labels unless the law requires them. TikTok, YouTube and Meta have their own rules on realistic AI voices and people \| Slate records synthetic media internally; labelling per platform is your call \| | open | |
| P209 | Risks and licences | \| Sound instruments \| Proof 2's tonal roles need Apple's sampled instruments, which can't be redistributed \| Built locally from each machine's installed content; doctor fails, rather than faking it, when they're missing \| | open | |
| P210 | Risks and licences | \| Fonts \| Brand fonts are licensed per brand (Degular through TrueSpace's Adobe Fonts) \| Fonts by reference only; the font gate fails on substitution \| | open | |
| P211 | Risks and licences | \| Vendor skills and MCPs \| Remotion's skills have no licence file. fnf-after-effects-mcp is pre-1.0 (four releases in two weeks), has an unclear licence, and is now a core lane \| Depend and pin, never vendor. Wrap the bridge behind Slate's AE lock with a... | open | |
| P212 | Risks and licences | \| A moving source \| B, C and the p3 sound are still changing; `render_local.sh` changed this morning \| Harvest twice; sound waits for proof 3 \| | open | |
| P213 | Risks and licences | \| Knowledge cutoffs \| GPT-6 Astra (April 2026) and Opus 5.5 (June 2026) predate Eleven v4, Remotion 4.0.529 and the Higgsfield API \| `registry.json` with verified-on dates, refreshed before production; films are pinned \| | open | |
| P214 | Risks and licences | \| Skill collisions \| Higgsfield's skills also claim "make a video" \| Slate asks which tool on open requests; routing evals; trigger adjustments under your one-time permission \| | open | |
| P215 | Risks and licences | \| One Mac, many agents \| Six builders once drove the load to 293 on 14 cores; AE blocks on a second script \| The job queue and its locks \| | open | |
| P216 | Risks and licences | \| Disk \| A 6-minute master is about 10 GB, AE's cache once took 60 GB, each Remotion bundle is 290 MB \| Films outside iCloud, disk reservations per job, bundle cleanup, AE cache off during masters \| | open | |
| P217 | Risks and licences | \| macOS lock-in \| Vision OCR, osascript, Keychain, Apple sounds \| macOS first; other hosts later \| | open | |
| P218 | Risks and licences | \| Company material \| TrueSpace code and brand; EOS Worldwide material \| Never in the core. Their packs stay on PERSONAL only, and WORK is never used for renders or builds \| | open | |
| P219 | Risks and licences | \| The Oct 8 date \| Oneiro's self-want lands on Oneiro's own schedule, and a light Inner Echo onboarding needs your approvals by about Oct 4 \| File the self-want on day 1; if it's late, the reel still ships through a supervised run that Oneiro's campaign ... | open | |
| P220 | Decisions for you | 1. **Name:** Slate. | open | |
| P221 | Decisions for you | 2. **After Effects: a full creative engine.** | open | |
| P222 | Decisions for you | - AE finishes every film you supervise, as an editable project plus the masters, and you start it. | open | |
| P223 | Decisions for you | - AE is also a second way to build scenes, with the bridge's 13 motion-design skills as looks. | open | |
| P224 | Decisions for you | - Oneiro's autonomous films are mastered with ffmpeg and held; you can start their AE finish when you approve one. | open | |
| P225 | Decisions for you | - Building scenes in AE needs AE's "Allow Scripts to Write Files and Access Network" pref, which you turned on on 2026-09-30. Slate never changes it, and its prefs tool refuses to touch it. | open | |
| P226 | Decisions for you | 3. **Oneiro: capped and held for you.** | open | |
| P227 | Decisions for you | - Reels only for brands you've approved, within a monthly cap: its own ElevenLabs key with a credit limit, and no generated footage by default. | open | |
| P228 | Decisions for you | - No AE. Every film is held until you approve that exact file, and nothing posts without your yes. | open | |
| P229 | Decisions for you | - Proposed starting cap: 20,000 ElevenLabs credits a month (about ten reels). Every Higgsfield generation waits for your yes. Change it any time. | open | |
| P230 | Decisions for you | 4. **Who it's for: yours first, shareable later.** The core lives in a private GitHub repo on your personal account, free of company material and secrets. The TrueSpace and EOS packs stay on PERSONAL only; Inner Echo's pack, being yours, can sit in a privat... | open | |
| P231 | Decisions for you | 5. **Remotion licence: free for now** (revised after your note on 2026-09-30). | open | |
| P232 | Decisions for you | - You're the only person operating Remotion, and these films are unpublished pilots. Remotion's free licence covers individuals and evaluation, so there's nothing to buy now. | open | |
| P233 | Decisions for you | - Revisit when a TrueSpace or EOS film is about to be published. Remotion's coming v5 terms count the people operating it, and you alone is under four; receiving finished media doesn't count as use. Under today's terms, work for a company of four or more is... | open | |
| P234 | Decisions for you | - If Slate goes public, most people using it are individuals or small teams, who are free. The README tells companies of four or more to get their own licence, or to use the open web renderer or After Effects. | open | |
| P235 | Decisions for you | - Slate doesn't depend on Remotion alone. Your library list (three.js, R3F, drei, GSAP, puppeteer, FFmpeg) makes an open web renderer, and AE is a full engine. | open | |
| P236 | Decisions for you | - "A video for EOS" means EOS Worldwide's, so onboarding names an approver at EOS for claims and brand use. | open | |
| P237 | Decisions for you | 6. **Higgsfield: the Cloud API stays the one lane.** | open | |
| P238 | Decisions for you | - You're keeping its balance funded, so Slate generates only through it. The app-credit lane stays off. | open | |
| P239 | Decisions for you | - Best quality wins, with the best models by default and overridable per film. The recommended agent is Opus 5.5 at max, also configurable. | open | |
| P240 | Decisions for you | - Generation is rare by default, and the requester decides how much. Code builds anything designed; generation is for real people, real scenes and stock-style shots code can't make, or where it genuinely adds something. Every take is judged, and ugly takes ... | open | |
| P241 | Decisions for you | - Oneiro asks your yes for each generation. | open | |
| P242 | Decisions for you | 7. **Sound for every brand.** | open | |
| P243 | Decisions for you | - Your palette is a starting point; each brand gets its own palette during onboarding, and Inner Echo's comes from its own world. | open | |
| P244 | Decisions for you | - A composed score is part of most films. | open | |
| P245 | Decisions for you | - The effects engine waits for your approval of proof 3. The Oct 8 reel uses the score, the voice and its onboarding sound reference. | open | |
| P246 | Decisions for you | 8. **Voices.** | open | |
| P247 | Decisions for you | - Your own voice as an ElevenLabs professional clone, which you verify yourself. | open | |
| P248 | Decisions for you | - Anyone else only as a real recording, never a clone. | open | |
| P249 | Decisions for you | - Oneiro may use your clone. | open | |
| P250 | Decisions for you | - No AI labels unless the law requires them. Platform rules are your call per platform. | open | |
| P251 | Decisions for you | 9. **Fast variants.** | open | |
| P252 | Decisions for you | - Variants sometimes, with quality depending on the channel: hero placements at master quality, social cut-downs fast. | open | |
| P253 | Decisions for you | - A second engine is fine if it's faster. Higgsedit gets benchmarked in phase 8 and is kept only if it wins. | open | |
| P254 | Decisions for you | - Channels: Instagram and TikTok, YouTube, LinkedIn, website and App Store. | open | |
| P255 | Decisions for you | 10. **visionary and goal-fidelity.** | open | |
| P256 | Decisions for you | - One small repo of their own, separate from Slate and shareable, symlinked into every personal agent. | open | |
| P257 | Decisions for you | - Claude maintains them. The TrueSpace agent stays its own thing. | open | |
| P258 | Decisions for you | - Your other skills stay exactly as they are. | open | |
| P259 | Decisions for you | 11. **Higgsfield's skills.** | open | |
| P260 | Decisions for you | - They stay global (you use them directly sometimes) and get installed for Codex and OpenClaw too. | open | |
| P261 | Decisions for you | - An open "make a video" gets Slate's question first: a full production or a quick clip? | open | |
| P262 | Decisions for you | - One yes from you covers these setup changes. | open | |
| P263 | Decisions for you | - Outside Slate they need app credits to generate. | open | |
| P264 | Decisions for you | 12. **The first Inner Echo reel.** | open | |
| P265 | Decisions for you | - By Oct 8, on a topic Oneiro chooses from its campaign. | open | |
| P266 | Decisions for you | - Held for you by iMessage with the cut, plus a push notification. | open | |
| P267 | Decisions for you | - Oneiro posts the exact file you approve. | open | |
| P268 | Your asks, and where each is answered | \| "take everything that we just did and all the pipelines we created, and create it as a concrete, reusable pipeline for dynamic video creation" \| §2 engine, §3 film folder, §7 method, Appendix A \| | open | |
| P269 | Your asks, and where each is answered | \| "Maybe it needs to be a collection of skills and a package" \| Both: §1 skills; the engine, CLI, job queue and MCP server; Appendix B \| | open | |
| P270 | Your asks, and where each is answered | \| "download / connect to Claude / connect to Higgsfield / connect to Eleven Labs / connect to After Effects… Then you're done" \| The promise, including "What you need first"; §5; phase 1 and the clean-machine test \| | open | |
| P271 | Your asks, and where each is answered | \| "and Claude can do anything at once" \| The promise ("Anything at once"); §2 jobs; §7 modes \| | open | |
| P272 | Your asks, and where each is answered | \| "an understanding of all of the code… all of the coding libraries, all of the motion design MCPs and skills that we've been using" \| "What we're packaging"; Appendix A, the dependency ledger \| | open | |
| P273 | Your asks, and where each is answered | \| "somebody can just use my skill or just talk about making a video with Claude" \| §1 front door and routing; negative evals \| | open | |
| P274 | Your asks, and where each is answered | \| "Claude will understand everything, know best motion design practices" \| §1 slate-motion; §4 look and motion development; the knowledge ledger \| | open | |
| P275 | Your asks, and where each is answered | \| "can create any video possible with beautiful voice and beautiful visuals" \| §8 formats; §4 onboarding; §1 slate-voice; "Quality" \| | open | |
| P276 | Your asks, and where each is answered | \| "it has to include everything: sound design, visual design… a full package" \| §2 modules: kit, looks, capture, voice, sound, generate, QA, finish, AE \| | open | |
| P277 | Your asks, and where each is answered | \| "if I wanted to make a video for EOS… I would just open a new chat, just start prompting" \| The promise ("a new Claude Code session"); §4 onboarding; proof 2 \| | open | |
| P278 | Your asks, and where each is answered | \| "in Oniro, it has a pursuit for Inner Echo… OpenClaw and even GPT Astra and Codex can use it as well… it should just be a global thing" \| §6; autonomous mode in §7; phase 2's tracer bullet; proof 3 \| | open | |
| P279 | Your asks, and where each is answered | \| "Let's start planning this out" \| This page, and the decisions above \| | open | |
| P280 | Appendix A: dependency ledger | \| remotion, @remotion/bundler, renderer, cli \| 4.0.521 \| Keep, pinned exactly \| The engine. Upgrade within 4.0.x only after the parity check; never 5.0 unplanned \| | open | |
| P281 | Appendix A: dependency ledger | \| @remotion/google-fonts \| 4.0.521 \| Keep \| Open fonts \| | open | |
| P282 | Appendix A: dependency ledger | \| @remotion/motion-blur \| Declared, unused \| Learn from \| Test HtmlInCanvasMotionBlur (≥ 4.0.529) against render_mb; render_mb stays the default \| | open | |
| P283 | Appendix A: dependency ledger | \| react, react-dom \| 19.2.8 \| Keep \| \| | open | |
| P284 | Appendix A: dependency ledger | \| typescript \| 5.9.3 \| Keep \| The type-check gate \| | open | |
| P285 | Appendix A: dependency ledger | \| esbuild \| Used, not declared \| Keep, declared \| Every event emitter uses it \| | open | |
| P286 | Appendix A: dependency ledger | \| Chrome headless shell \| 149 (Remotion-managed) \| Keep \| Doctor runs `remotion browser ensure` \| | open | |
| P287 | Appendix A: dependency ledger | \| three, @react-three/fiber, drei, @remotion/three \| 0.180 / 9.7 / 10.7 / 4.0.521 (your list) \| Keep \| 3D for the open web renderer, and inside Remotion scenes through @remotion/three \| | open | |
| P288 | Appendix A: dependency ledger | \| gsap \| 3.15.0 (your list) \| Keep, for the open web renderer \| Timelines seeked frame by frame. Check its licence terms before a public release \| | open | |
| P289 | Appendix A: dependency ledger | \| puppeteer \| 25.10.0 (your list) \| Keep, for the open web renderer \| Captures frames of our own local pages only; browsing stays in Aside \| | open | |
| P290 | Appendix A: dependency ledger | \| postprocessing, @react-three/postprocessing, framer-motion \| Declared, unused \| Drop \| Blank under Remotion; unused \| | open | |
| P291 | Appendix A: dependency ledger | \| numpy, scipy, Pillow \| 2.4 / 1.18 / 12.2 \| Keep \| DSP, QA, sheets \| | open | |
| P292 | Appendix A: dependency ledger | \| openai-whisper (turbo, large-v3-turbo) \| Homebrew \| Keep \| Default aligner; the model is recorded in evidence \| | open | |
| P293 | Appendix A: dependency ledger | \| ElevenLabs Forced Alignment \| Not used \| Learn from \| Evaluate against Whisper on the three films' takes \| | open | |
| P294 | Appendix A: dependency ledger | \| whisper.cpp \| Test model only \| Drop \| \| | open | |
| P295 | Appendix A: dependency ledger | \| demucs, librosa \| uvx, analysis only \| Keep, in the analyze extra \| Reference analysis \| | open | |
| P296 | Appendix A: dependency ledger | \| ocrmac (Apple Vision) \| uv cache \| Keep \| The one OCR engine \| | open | |
| P297 | Appendix A: dependency ledger | \| ocr_vision.swift \| Compiled binary \| Drop \| One engine, and no swiftc build \| | open | |
| P298 | Appendix A: dependency ledger | \| tesseract \| Installed \| Drop for gates \| A second engine would silently change results \| | open | |
| P299 | Appendix A: dependency ledger | \| bs4, html2text.py \| Missing, project-specific \| Drop \| \| | open | |
| P300 | Appendix A: dependency ledger | \| fonttools, uharfbuzz \| Higgsedit backend \| Drop with Track A \| \| | open | |
| P301 | Appendix A: dependency ledger | \| ffmpeg / ffprobe 9 (slim) \| Keep \| QA and encoding; captions are burned in by Remotion. If the full build is ever needed, doctor records its keg path \| | open | |
| P302 | Appendix A: dependency ledger | \| Rubber Band 4 (x86 under Rosetta) \| Replace with arm64 \| Pacing \| | open | |
| P303 | Appendix A: dependency ledger | \| uv, node, git, gh \| Keep \| Runtime and updates \| | open | |
| P304 | Appendix A: dependency ledger | \| aerender, osascript \| Keep \| The AE lane \| | open | |
| P305 | Appendix A: dependency ledger | \| xcrun simctl \| Keep \| iOS captures \| | open | |
| P306 | Appendix A: dependency ledger | \| caffeinate, security \| Keep \| Keep-awake; Keychain reads \| | open | |
| P307 | Appendix A: dependency ledger | \| rsvg-convert, potrace \| Keep, optional \| Tracing logos during onboarding \| | open | |
| P308 | Appendix A: dependency ledger | \| higgsfield CLI 1.1.26 \| Depend \| Its sign-in is run by you, never by an agent \| | open | |
| P309 | Appendix A: dependency ledger | \| aside \| Depend \| The only browser on your Macs \| | open | |
| P310 | Appendix A: dependency ledger | \| mcporter, sag, peekaboo, songsee, ImageMagick, exiftool \| Drop \| Not needed \| | open | |
| P311 | Appendix A: dependency ledger | \| Slate MCP (new) \| Keep \| Thin wrapper over the CLI and job queue \| | open | |
| P312 | Appendix A: dependency ledger | \| Higgsfield hosted MCP \| Depend, chat lane only \| Outside the ledger; reconciled by balance snapshots \| | open | |
| P313 | Appendix A: dependency ledger | \| ElevenLabs hosted MCP \| Depend, chat lane only \| Same \| | open | |
| P314 | Appendix A: dependency ledger | \| fnf-after-effects-mcp 0.1.3 (Higgsfield's AE bridge) \| Depend, pinned; a core lane (decision 2) \| Builds scenes in AE. It shares Slate's AE lock, needs the file-write pref only you can turn on, and gets acceptance tests on every version bump \| | open | |
| P315 | Appendix A: dependency ledger | \| Aside MCP \| Depend \| Web capture and research \| | open | |
| P316 | Appendix A: dependency ledger | \| mac-cua \| Optional \| Dismissing AE dialogs and background app control; its browser half is forbidden \| | open | |
| P317 | Appendix A: dependency ledger | \| 21st.dev MCP \| Optional, project-level \| Design inspiration, paid \| | open | |
| P318 | Appendix A: dependency ledger | \| ElevenLabs local MCP \| Drop \| Archived, and it includes an outbound-call tool \| | open | |
| P319 | Appendix A: dependency ledger | \| Remotion docs MCP \| Drop \| Deprecated \| | open | |
| P320 | Appendix A: dependency ledger | \| logic-pro-mcp \| Drop \| Evaluated, not used \| | open | |
| P321 | Appendix A: dependency ledger | \| Framer, Linear, truespace-sidecar, oneiro-memory, apple \| Not part of Slate \| \| | open | |
| P322 | Appendix A: dependency ledger | \| visionary, goal-fidelity \| You \| Depend (their own shareable repo, in every personal agent) \| Decision 10 \| | open | |
| P323 | Appendix A: dependency ledger | \| ponder \| You \| Depend, optional \| A QA loop over every shot \| | open | |
| P324 | Appendix A: dependency ledger | \| video-verifier \| Third party \| Depend, for UI recordings only \| Its web path is overridden to Aside \| | open | |
| P325 | Appendix A: dependency ledger | \| higgsfield-* (8) \| Higgsfield (MIT) \| Depend; global, and installed for Codex and OpenClaw too \| Decision 11. They generate on app credits, which aren't funded \| | open | |
| P326 | Appendix A: dependency ledger | \| remotion-dev/skills (12) \| Remotion (no licence file) \| Depend, inside the engine folder \| Overrides its old voice model and meme sounds; Studio never auto-opens \| | open | |
| P327 | Appendix A: dependency ledger | \| elevenlabs/skills (10) \| ElevenLabs (MIT) \| Depend, optional, inside the engine folder \| \| | open | |
| P328 | Appendix A: dependency ledger | \| AE bridge skills (13) \| Higgsfield \| Depend, served by the bridge; used as AE looks \| No redistribution grant, so they're referenced, never copied \| | open | |
| P329 | Appendix A: dependency ledger | \| asset-creator, canvas-design, frontend-design, avoid-ai-writing \| Third party, Anthropic \| Optional \| Vector drawing, frames, component looks, script polish \| | open | |
| P330 | Appendix A: dependency ledger | \| truespace-frontend-taste \| You \| Moves to the TrueSpace pack \| Brand-specific \| | open | |
| P331 | Appendix A: dependency ledger | \| HyperFrames skills \| HeyGen (Apache-2.0) \| Learn from \| Router, media ledger and audio patterns \| | open | |
| P332 | Appendix A: dependency ledger | \| OpenMontage \| AGPL \| Never copied \| Its licence would force AGPL on Slate \| | open | |
| P333 | Appendix A: dependency ledger | \| ElevenLabs: eleven_v4, eleven_ttv_v3, Remix, Isolator \| Keep \| Voice \| | open | |
| P334 | Appendix A: dependency ledger | \| Eleven Music \| Keep, on its best model \| A score is part of most films (decision 7) \| | open | |
| P335 | Appendix A: dependency ledger | \| ElevenLabs Sound Effects \| Learn from \| Filling a gap only; your sound is designed \| | open | |
| P336 | Appendix A: dependency ledger | \| Higgsfield Cloud API \| Keep \| Plates, per generation \| | open | |
| P337 | Appendix A: dependency ledger | \| Higgsfield video analysis (0 credits) \| Keep \| `slate analyze` \| | open | |
| P338 | Appendix A: dependency ledger | \| Higgsfield CLI and app credits \| Off; not funded \| A separate billing pool (decision 6) \| | open | |
| P339 | Appendix A: dependency ledger | \| Higgsedit (Track A) \| Benchmark in phase 8 \| Kept as the fast route for social cut-downs only if it wins (decision 9) \| | open | |
| P340 | Appendix A: dependency ledger | \| Apple sound content (GarageBand, Logic, Final Cut) \| Keep, local only \| Never redistributed \| | open | |
| P341 | Appendix A: dependency ledger | \| The WORK render farm (`work.sh`) \| Drop \| WORK is never used for renders \| | open | |
| P342 | Appendix C: sources | - The inventory: `research/video-package-inventory-2026-09-30/` in the film project (`visual-engine.md`, `sound-voice.md`, `ae-qa-delivery.md`, `process-lessons.md`, `agents-wiring.md`, `packaging-research.md`, `media-research.md`, `coverage.md`, `inventory... | open | |
| P343 | Appendix C: sources | - The three reviews of the first draft, merged into this version: fidelity (14 findings), engineering (15) and craft and operations (15). | open | |
| P344 | Appendix C: sources | - The film project's own records: `SOUND.md` (the return to proof 2), `sound/v2/HANDOVER.md`, `TRUDY_MOTION.md`, `V4_TAGS.md`, `PRODUCTION*.md`, and the memory notes, including "perfect, don't replace". | open | |
