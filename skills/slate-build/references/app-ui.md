# Product UI on screen

When a film shows a product, the product is the truth. Its copy, its states, its order and its hierarchy come from its
code at a pinned commit (slate-story's truth pack), and the film shows it the way it really behaves, only bigger and
better staged.

## Show the product as it is

- **At the size and form factor its code lays it out.** Don't invent a device hand-off (a phone) for a product that
  ships at desktop size.
- **Ground UI in the product's real code**, read in place, never copied into Slate. Captions, chips and wording are the
  product's own.
- **Show only statuses the product really has**: no invented intermediate state before the real one.
- **Show UI in the state the data implies**: a feature that needs two readings isn't visible on the first.
- **Simplify only by choosing states it really has** (a real toggle turned off), never by deleting parts arbitrarily.
- **Keep the product's typographic hierarchy**: an eyebrow stays an eyebrow, not a big heading.
- **Show UI state changes with the product's own state styles** (a disabled button waking from its disabled opacity on
  the word) and highlight items on their spoken word with the product's own hover or selection state.
- **Load surfaces the way the product loads them**: the curve draws, the numeral counts, the marker settles, the
  statement lands.

## Interactions

- **Show interactions as the product behaves.** A chat never types the user's question: questions arrive as chosen
  chips or already-sent messages, and only the assistant's answers stream.
- **Never show a password being typed or a sign-in link being sent**; show sign-in the way the product really does it.
- **Keep a whole conversation in view**: a chosen follow-up becomes the next message in its own place, and the thread
  doesn't scroll.
- **UI belongs to the card that owns it**: print a button on its card so it moves and clips with the card; never float
  it in front at a fixed position (a floating button was once left behind on a bare page).

## Film copy and product copy

- The script's on-screen lines and the product's copy are separate layers. Show the product's copy as the product
  writes it (emphasizing the script's words), and set the script's line as film type beside it rather than rewriting
  the product.
- Don't dress plain concepts as UI chips: chips read as invented UI. Use plain type.
- The on-screen word budget can justify leaving product elements out of a shot; record the omission in NOTES.md.

## Restaging for film

- When a product surface would vanish on the film's ground (a light-grey panel on a light-grey page), set it as its own
  card and note the restaging.
- On a fidelity pass, fix anything retired or wrong on the product's core surfaces to match its code; secondary
  surfaces (an assistant chat, partner widgets) only need to feel right unless something is plainly wrong. Ask whether
  a fix is the pressing stuff before widening scope, and record the ruling.

## Capture or rebuild?

Real app captures (`slate capture ios`, `slate capture web`) are the fastest truth. Rebuild a surface in code when the
shot needs camera moves, restaging, motion blur, exact type or a state the demo account can't reach. Either way, the
same truth rules apply (captures.md).
