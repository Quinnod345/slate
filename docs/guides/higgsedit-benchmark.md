# Higgsedit benchmark

Slate keeps Higgsedit as a social cut-down route only after the same-film comparison proves it faster at acceptable
quality. Preparing and measuring files is local. The paid Higgsedit run still needs Quinn's authorization and funded
app credits; the harness refuses to start it without that decision.

## Freeze the comparison

Use `slate bench higgsedit plan --film <film> --master <file> --out <folder>`. Protocol v2 binds the exact master
bytes, master frame clock, chosen frame ranges, channel aspect and dimensions, and decision rule. Both arms use
that same comparison. A later master, channel or range choice needs a new `--out` folder. Repeating an identical
plan preserves its protocol, measurements and reviews.

Existing version-1 records are preserved as historical evidence. Their missing bindings cannot be supplied after
the fact, so they cannot establish a winning verdict. Plan and measure a new comparison in another folder.
Updated recipes also belong to new plans; an earlier frozen protocol is not rewritten.

## Prepare the Remotion cut

An exact full-length source comparison on the same clock and frame size uses a channel-only child:
`slate variant <parent> <child> --channel <channel> --aspect <aspect> --format <format>`. The generated recipe chooses
this path only when its one range covers the source timeline completely and the source, master and channel sizes
agree. The child reuses the unchanged script, timing and captions by reference. It owns its scenes, staged audio,
renders, masters and gate results. Do not rebuild an imported source clock from historical take paths that no longer
exist. `--cut` still means a shorter film and refuses the parent's full duration.

Other selections follow the cut recipe below. Partial or reordered ranges never become channel-only children merely
because their total duration equals the source. Preserve earlier frozen protocols and generate the corrected recipe
in a new `--out` folder; no old measurement becomes a timed run through this update.

The plan is a recipe, not a built cut. `slate variant <parent> <child> --channel <channel> --aspect <aspect>
--format <format> --cut <seconds>` creates a child skeleton. Its duration comes from the frozen total frame count:
`protocol.cut.frames * protocol.master_media.fps_den / protocol.master_media.fps_num`. The child keeps its parent's
clock even when the channel format declares another frame rate. `--channel` selects the existing social render route.

Apply `protocol.cut.ranges` explicitly to the picture and sound, in their recorded order. Ranges are `[in, out)`
master frames; `--cut` only sets a duration. For complete parent shots in parent order, fill the child's
`script/script.json` `cut.keep` with their IDs. Partial or reordered ranges need authored child acts and explicit
scene/audio trims. The plan does not infer those choices.

Build the selected script with `slate script build --film <child>`, then run `slate timing --film <child>`. Use
`--from-takes` on the script build when unchanged approved takes should determine shot lengths; it does not trim
arbitrary ranges. Stage or re-stage the child's Remotion scenes and their film-owned imports, required public
assets/fonts, and events for the selected ranges and channel layout. Derivation references eligible assets; it does
not copy the complete scene/resource dependency closure or the finished parent mix.

Align and stage the child's `sound/mix/final_mix.wav` for the cut. Use `slate mix --film <child>` when rebuilding it;
a valid authored or imported candidate mix can be retained. Then run `slate render --film <child>` and
`slate master --ffmpeg --film <child>`.

## Measure and review the cuts

Record each arm with `slate bench higgsedit measure --arm <arm> --cut <file> --wall-s <seconds> --out <folder>`.
Wall time starts at the first arm-specific preparation action, manual or commanded, and ends at the finished local
file. Include cut authoring, source/resource/audio staging, builds, queue waits, rendering, mastering and downloading
where they apply. Both arms start from the same frozen master/ranges and existing shared parent inputs; describe reuse,
cache and queue conditions with `--note`. A render-only interval from a prebuilt child is not an end-to-end run.

File hashes are checked before and after the quality comparison. Measurements must retain the planned clock,
channel dimensions and complete comparison coverage; short or long cuts cannot satisfy the frame-count rule.
Timing, credits and quality values must be finite.

A person watches each distinct measured Higgsedit cut beside the measured Remotion cuts and master, then records
their actual judgment with `slate bench higgsedit review --arm higgsedit --cut <file> --verdict acceptable --by <name>
--out <folder>`. Use `not-acceptable` for a rejection. Omitting `--cut` selects the arm's latest measured cut.
Selecting an older cut does not add another timed run or change the median.

Reviews bind exact cut bytes and the current Remotion comparison. A newer unreviewed cut cannot join a winning
median; a new distinct Remotion comparison needs a renewed review. A later rejection supersedes acceptance of the
same bytes. Changed or missing files invalidate their evidence when the report is recomputed.

## Read the decision

`slate bench higgsedit report --out <folder>` recomputes the verdict from the bound evidence. Higgsedit must be at
least 1.5 times faster by median wall time, clear the SSIM and frame-count floors, and have acceptable reviews for
every distinct measured cut. Invalid, stale, incomplete or unreviewed evidence leaves the decision incomplete.
A coherent comparison that misses the speed, quality or review requirement drops Higgsedit. The report rounds
values for display; the decision uses their retained precision.
