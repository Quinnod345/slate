# Safe areas, legibility and accessibility

A frame that looks right on the editing screen can lose its text under a platform's buttons, flash too fast for some
viewers, or be unreadable on a phone. These rules keep every frame safe wherever it's shown.

## Title-safe and action-safe

- **Action-safe** is the central 93% of the frame; **title-safe** the central 90% (SMPTE ST 2046-1 and EBU R 95). At a
  1920 × 1080 frame, title-safe is 1728 × 972, 96 px in from each side and 54 px from the top and bottom; scale with the
  film's own size from film.json.
- Keep text and anything that must be read inside title-safe; keep essential action inside action-safe.

## Platform safe zones

Vertical platforms put buttons, captions and profile details over the frame. Each format file carries its platforms'
safe zones (fractions of the frame from each edge), and film.json inherits them.
- Keep text and faces inside the safe zones for every platform the film is going to.
- Burned-in captions sit inside them too, above the lower overlays.
- `slate qa access` checks 9:16 films' text against the platforms' zones by OCR (UNCHECKED without OCR), and the contact
  sheet shows the zones for review.
- A derived film (a new aspect) re-stages its layout for its own safe zones; it never just crops (slate-finish).

## Legibility

- Body text at least 28 px at a 1080-line frame (the kit's type scale does this, as fractions of the short side).
- Text contrast from the brand's colour pairs: at least 4.5:1 for body text and 3:1 for large text. The kit derives
  missing roles with contrast guaranteed, and `slate qa access` checks the brand's pairs.
- Type over footage stays legible with a soft brand-colour grade behind it, never a box.
- Tabular figures on every number that changes, so digits don't jump sideways.
- Close-ups never crop text mid-line.

## Flashes and motion sensitivity

- At most three flashes a second (WCAG 2.3.1, general and red flashes). `slate qa access` checks it.
- A film embedded on a web page and playing longer than 5 seconds needs a way to pause or stop it (WCAG 2.2.2): that's
  the page's job, but say so when delivering a hero loop.
- Avoid sustained oscillation and fast motion at the edges of the frame.

## Captions

At most 2 lines of 42 characters, 17 characters a second, at least 1 s on screen, never overlapping, word for word the
script (slate-voice builds them; `slate qa captions` checks them).

## Checking

Review the contact sheet with the platform zones overlaid, at the smallest size the film will be shown. What's readable
on a laptop at full size can vanish on a phone.
