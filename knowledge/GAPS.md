# Knowledge gaps

Lessons from the films that nothing in Slate reflects yet, grouped by the module that closes them, and lessons being built (pending) with their planned file. Each entry is the exact change and the lesson ids it closes; `slate knowledge show <id>` gives a lesson's text and sources. `slate knowledge check` fails (knowledge.gaps) until every one is closed and the lesson is re-mapped in knowledge/lessons.json.

0 gaps (0 changes) and 10 pending, across 1 module (generated from the ledger by `slate knowledge gaps --write`; edit the ledger, not this file).

## Pending

Being built, or waiting on something named; each counts against the check like a gap until it's re-mapped, and the check says when its planned file has landed.

### sound (10 lessons)

- **L-sound-042** (src/slate/sound/effects/engine.py::shared_hall): render(): one shared synthetic hall for every effect (2.4 s of decorrelated noise, 20 ms predelay, low-passed at 6.5 kHz), split into effects and tonal returns; a test that two effects share the hall's impulse response. Waits for proof 3 (`slate approve proof3 <file> --by NAME`): the effects engine port.
- **L-sound-057** (src/slate/sound/effects/engine.py::write_cues): spot() and render(): every placed cue written to sound/sound_cues.json (schema sound_cues), which the repeats and sync gates already read; a test that the repeats gate reads a spotted film's cues. Waits for proof 3 (`slate approve proof3 <file> --by NAME`): the effects engine port.
- **L-sound-S007** (src/slate/sound/mix.py::family_buses): Mix by family: each cue to its family's bus with one clip gain per hit from the speech key, and sound/mix/family_stems/<family>.wav summing to the mix; tests: a duck doesn't move a hit's anchor, and the family stems sum to the mix. Waits for proof 3 (`slate approve proof3 <file> --by NAME`): the effects engine port; family stems come from its per-cue renders (NEEDS 213).
- **L-sound-S008** (src/slate/sound/mix.py::speech_key): The speech key: a 20 ms RMS follower on the voice against its loud level (0 at 40 dB under, 1 at 25 dB under), held 150 ms, read 40 ms ahead, 8 ms attack and 250 ms release; one clip gain per hit from its maximum over the hit's audible part. Waits for proof 3 (`slate approve proof3 <file> --by NAME`): the effects engine port; family stems come from its per-cue renders (NEEDS 213).
- **L-sound-S010** (src/slate/sound/mix.py::solve_guards): The clarity, air and never-over guards solved together, the voice-as-delivered loop, approved spans frozen, anchors re-measured, and sound/mix/rides.json written (the balance gates, sound.balance.approved_spans and the listening file already read it). Waits for proof 3 (`slate approve proof3 <file> --by NAME`): the effects engine port; the guards ride hits the effects engine renders one by one.
- **L-sound-S011** (src/slate/sound/mix.py::solve_guards): An accent landing on a word is ridden at most half the guard's depth and listed, never failed (the owner's 'in between'). Waits for proof 3 (`slate approve proof3 <file> --by NAME`): the effects engine port; the guards ride hits the effects engine renders one by one.
- **L-sound-S013** (src/slate/sound/mix.py::solve_guards): The never-over guard runs last, 0.5 dB to spare against the voice as delivered, the hits making it loud ridden down whole (at most 12 dB more), an impact keeping its first 80 ms. Waits for proof 3 (`slate approve proof3 <file> --by NAME`): the effects engine port; the guards ride hits the effects engine renders one by one.
- **L-sound-S015** (src/slate/sound/mix.py::solve_guards): Close the loop against the voice as the master delivers it: raise a short word's target by its shortfall, at most two more passes. Waits for proof 3 (`slate approve proof3 <file> --by NAME`): the effects engine port; the guards ride hits the effects engine renders one by one.
- **L-sound-S024** (src/slate/sound/effects/engine.py::place_cue): spot(): write-ons aligned on their audible start, up to ±4% rate on library whooshes, same-chord pads handed off over 0.3 s (equal power); tests on synthetic cues. Waits for proof 3 (`slate approve proof3 <file> --by NAME`): the effects engine port.
- **L-sound-S025** (src/slate/sound/effects/engine.py::pad_bed): With a composed score, no continuous pad bed and no pad bloom at the landing. Waits for proof 3 (`slate approve proof3 <file> --by NAME`): the effects engine port.

## Appendix: partly covered

These lessons land somewhere real (a code default, a gate or a skill rule), so they aren't gaps, but a part of each isn't enforced yet. Each note says what is and what isn't.

- **L-qa-011**: The script gate plans the 2 s number hold; the label and voice-matched rules are skill rules, and the master's readtime gate adds neither.
- **L-packaging-060**: The manifest carries top-level keys only (no userConfig, channels, lspServers or monitors) and is tested in CI.
- **L-packaging-062**: The plugin's skills are generated copies inside the plugin folder, never symlinks out of it.
- **L-packaging-070**: Enforced in CI.
- **L-packaging-076**: The marketplace entry and the manifest are both "slate".
- **L-packaging-085**: Held by `slate skills build --check` in CI.
- **L-packaging-087**: Held by `slate skills build --check`.
- **L-packaging-102**: CI fails on any drift between the skills and every client's generated copy and manifest (tests/packaging/test_packaging_manifests.py::test_ci_runs_the_tests_and_the_drift_checks).
- **L-packaging-107**: Client placeholders and non-portable paths are refused in skill text (tests/packaging/test_packaging_skills.py::test_references_are_checked_for_portability_and_links_too).
- **L-packaging-112**: "slate" is kebab-case and not reserved.
- **L-qa-D013**: QA reads only registered renders, so a scratch preview never reaches a gate; a draft film's renders are registered, but a draft film is never delivered as final.
- **L-generate-D013**: Enforced by construction: no submit goes out without a quote for that exact body (tests/gencap/test_gencap_client.py::test_estimate_comes_before_every_submit). No gate is needed.
- **L-generate-D028**: Enforced by construction and tested (tests/jobs/test_jobs_resume.py::test_a_job_killed_mid_wait_resumes_and_polls_instead_of_paying_twice, tests/gencap/test_gencap_flow.py::test_a_request_id_that_only_reached_the_ledger_is_adopted_not_paid_again).
- **L-ae-D016**: Enforced by construction and tested (tests/ae/test_ae_prefs.py::test_set_changes_exactly_one_value_and_keeps_cr, test_set_refuses_a_damaged_lf_file, test_the_scripting_value_survives_every_edit).
- **L-qa-D046**: Contract frames are rendered straight from the bundle on the software renderer and compared bit for bit, so the ~2-level encode tolerance never enters; encoded comparisons use PSNR and the joins' slack.
