# Knowledge gaps

Lessons from the films that nothing in Slate reflects yet, grouped by the module that closes them, and lessons being built (pending) with their planned file. Each entry is the exact change and the lesson ids it closes; `slate knowledge show <id>` gives a lesson's text and sources. `slate knowledge check` fails (knowledge.gaps) until every one is closed and the lesson is re-mapped in knowledge/lessons.json.

0 gaps (0 changes) and 0 pending, across 0 modules (generated from the ledger by `slate knowledge gaps --write`; edit the ledger, not this file).

## Appendix: partly covered

These lessons land somewhere real (a code default, a gate or a skill rule), so they aren't gaps, but a part of each isn't enforced yet. Each note says what is and what isn't.

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
