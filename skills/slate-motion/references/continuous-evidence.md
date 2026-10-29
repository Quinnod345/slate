# Carry one idea through changing forms

A carried object becomes useful when its changes explain the idea. Start with one recognizable object, let it gain
meaning, and keep enough of its shape, label or position for the viewer to follow it. A smooth transition alone is
not a reason to make the transition.

These construction recipes combine Slate's existing kit and motion rules. The dataset, product artwork, paths and
story remain the film's. They are guidance, not prebuilt scenes or newly verified components.

## A crowd becomes an explanation

Use this when the story moves from individual observations to a visible pattern.

1. Give every observation a stable identity. Keep a record of its initial pose, final pose and group; do not rebuild
   the population with new identifiers when the layout changes.
2. Match each destination to a nearby source in the relevant group. Resolve ties deterministically. Keep that
   assignment fixed across frames, so neighbors do not trade places in the middle of a move.
3. Make the cause visible: a reading line, selection or other meaningful action reaches each object before it sorts.
   Derive the local start from that contact and the film clock, rather than staggering the whole crowd arbitrarily.
4. Keep local travel small. Move the world with `cam2DAt` or the named camera track when necessary; compute a screen
   pin in the same coordinate system as the camera so a displayed detail stays fixed while its surroundings move.
5. When the pieces form a continuous band, render the band's exact final union. Transfer drawing ownership at the
   common endpoint; do not fade another band over pieces that are still visible.
6. Hold the pattern before introducing its explanation. Then reuse the resulting band, line or point inside the
   next chart instead of resetting the visual argument.

`Card`, `chainObj`, `monoSpline`, the cameras and chart primitives provide the parts. The source-to-destination
assignment and contact timing are authored film data. Check the smallest delivered size: tight regular grids can
shimmer even when their full-size render looks steady. See
[shared elements](morphs-and-shared-elements.md),
[camera](camera.md) and
[anti-glitch rules](anti-glitch.md).

## Weighted objects become navigation

Use this when several contributions must remain legible while one opens into detail.

Normalize the dataset once and derive cumulative intervals from it. For example, weights 2, 3 and 5 occupy
`[0, .2]`, `[.2, .5]` and `[.5, 1]`. That one partition record defines each outgoing object's destination width,
its segment of the shared line and its later place in the navigation bar. Keep identifiers and label ownership
with the partition.

Flatten the objects into those intervals on one coordinated motion track. Draw the continuous destination at the
common landing state, then use the same intervals to expand into navigation. A selected interval can open its
panel while the other intervals remain visible as context. Keep the selected anchor still and reveal the panel's
content in its final layout, clipped to the changing container.

Position, size and contents share the same timing; geometric travel uses the named ease once. Each value counts
once, from its previous displayed value when it changes. Arrange the shared labels and figures on routes that do
not cross each other. New detail arrives after the carried parts have almost landed. See
[morphs and counting](morphs-and-shared-elements.md). Apply the act contracts from slate-build.

## A curved flight keeps one pace

Use this for a continuous return from a data endpoint to a meaningful mark or destination. A curve's raw parameter
is not distance: easing that parameter can create an unwanted acceleration or pause as curvature changes.

Build a cumulative distance table from samples of the authored path. Apply the brand's motion ease to the fraction
of total distance travelled. Find the surrounding distance samples and interpolate the corresponding path
parameter; then evaluate the original curve at that parameter. Skip zero-length sample intervals, handle a
zero-length path explicitly, and return the exact authored endpoints at progress zero and one. Sampling density
must be sufficient at the final output scale; compare finer tables before trusting it for a large hero move.

Keep the path, distance table and timing deterministic and evaluate them at sub-frame times. Land on the meaning's
word using `startFor` or `landOn`; orient to the path's tangent only when the object permits rotation. A fixed
brand mark keeps its orientation. End with the exact destination composition and a read hold.

Slate's `monoSpline` controls values through timed keys; it does not supply a general point-at-distance path API.
This recipe remains film-owned math until a reusable helper has its own tests and visual proof. See
[the motion law](motion-law.md) and
[shared elements](morphs-and-shared-elements.md).

## Let the payoff retain its history

The closing object should contain evidence of the journey: a changed value, earned mark, filled slot or meaningful
page. A physical book can accumulate those changes with `HingedBook` and `PageLeaf`, while its page faces remain
caller-owned. Return to a shape or composition introduced early so the ending resolves something the viewer already
recognizes. Preserve generous reading time around contact, comparison and audience changes. See
[physical books](physical-books.md). Use slate-story's structure rules to earn the payoff.

Before hand-in, inspect the first moving frame, ownership transfers, collisions, the smallest delivery and the
settled endpoint. A construction guide is not a film gate: the finished sequence still needs its own motion trace,
frame review, sound alignment and owner review.
