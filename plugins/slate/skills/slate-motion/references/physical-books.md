# Physical books: a carrier with memory

A book gives a sequence somewhere to live. Its cover establishes the object; each turn changes what the viewer can
see while keeping what they have already seen physically behind it. Use that continuity when the idea benefits from
accumulation, comparison or a choice the viewer can carry away.

## Stage the object, then the idea

- **Let a familiar mark become part of the cover.** A seal, token or other established object can resolve into the
  cover's emblem. Match its silhouette, position, scale and material on the hand-over frame. One visible mark travels
  through the hand-over; the cover takes ownership when it lands. The film owns that morph and its meaning.
- **Keep one book through the sequence.** An impression can establish an action, a spread can make a comparison, and a
  later spread can connect the result to its audience. Keep the same cover, hinge, paper dimensions, page order and
  marks. The object gathers evidence as the idea develops.
- **Give each idea a useful view.** An oblique cover view establishes an object; a closer view makes a contact felt; a
  flatter spread makes two sides comparable; a wider view can reveal who the result reaches. Choose the view for what
  it explains. A page change alone does not earn an arbitrary orbit or another push and pull.
- **Separate travel from reading.** Land the camera and the leaf, then let the statement read. A stamp's contact and
  an unfolding comparison can be motion beats; their settled result needs its own hold. Apply
  [read time](read-time.md) from the moment both the content and its view are legible.
- **Leave the result on the object.** Ink, a chosen box or a comparison stays registered to its paper through every
  later move. It carries the same claim and label when it returns.

This is a staging pattern. Choose the carrier, emblem, page content and destination for the brief. The shared book
primitive supplies paper mechanics; the film supplies the authored sequence and brand artwork. slate-story's
structure.md explains how the object becomes a payoff.

## Make the paper behave like paper

- The hinge stays on the spine. A leaf is a chain of connected strips; its free edge leads as it bends, and it lies
  exactly flat at either rest.
- The recto and verso belong to one physical leaf. The back face is mirrored locally before the turned leaf mirrors
  it again. Reverse-page artwork must arrive at the same page coordinates as the flat resting artwork, including
  fractional page widths and the small overlap that closes strip seams.
- Lighting follows the strip angles. The board has thickness, its lining clears the closed page stack, and settled
  leaves remain above the surface beneath them.
- A leaf's cast shadow follows its projected footprint. It fades to zero at upright, then grows on the receiving
  page. Keep the fade continuous near the rests and the spine; a visibility threshold can introduce a pop.
- Settled paper draws its content once, as one sharp flat face. Hold that rest rather than leaving an almost-flat
  strip assembly swimming under small type.

The kit's `HingedBook` and `PageLeaf` implement these mechanics. Their geometry lives in the React-free core;
slate-build's hinged-books.md gives the API and coordinate spaces.

## Keep the material and type stable

- Use the brand's page, cover, edge and ink roles. A film-specific grain, deboss, emblem or stamp is caller-owned face
  content, kept in that face's local coordinates.
- Keep opacity, blur and filters off the book's `preserve-3d` ancestors. They flatten the depth stack. Put a treatment
  on the face that needs it, or carry the book out of frame when it leaves.
- In tilt raster mode, put `TiltSpan` directly around type. Keep ruled lines, stamps and page shapes outside the type
  tilt. Shapes and their content share one page position in either raster mode.
- Drive angles from film seconds and the brand's named rest-to-rest ease. The book has no clock, invented timing or
  per-page idle loop. Register the film's turns, camera moves and contact events with the existing motion and event
  helpers.

## Verify the continuity

Render the uninterrupted cover opening and every leaf turn, including a short settled hold on either side. Check:

1. The carried emblem is drawn once and agrees with the cover on its hand-over frame.
2. The spine holds, strips meet, and the free edge bends without a seam or depth inversion.
3. Reverse artwork, fine rules and labels reach their flat positions without sliding at the rest switch.
4. Shadows fade through upright and the rests; no dark patch changes sides at full strength.
5. A comparison reads as a held spread, and each later view still contains the same book and accumulated marks.
6. The camera settles before small text asks to be read; the final statement gets its full read hold.

Check both raster modes when the film supports both, and fractional exposure times at the film's actual rate. The
kit's neutral `Kit-Book` and `Kit-Book-Hint` compositions exercise the mechanism. Their short turn proof supports
geometry review; the film's complete sequence still needs a recording, frame analysis and its own craft review.
