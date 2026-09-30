# Truth research: sources you can pin and cite

A film is only as trustworthy as the sources behind it. Truth research finds what's actually true about the product
and the brand, pins each source so it can be re-checked, and records who rules on what's claimed.

## The order of authority

1. **The owner's rulings** on what's claimed. A feature can exist in the code and still be ruled out for the film
   ("that's not true" means it doesn't appear).
2. **The product's code**, read in place at a pinned commit and cited `path:line`. It wins over everything below.
3. **The brand's website, live product pages, public demos and guidelines**, read and cited with the date.
4. **Docs, prototypes, demos and older films**: layout references only. They drift: names change, numbers move,
   features are retired. When they disagree with the code, the code wins; when a product demo's wording disagrees with
   the product's code, the code wins.

For a brand the owner doesn't own, the brand's **claim authority** (a named person there) rules on claims and brand use.

**Nothing is invented**: every UI element, word and number in the film maps to a citation in the truth pack. A concept
screen (something not built) needs the requester's explicit sign-off and a visible "concept" treatment. The rules that
follow from this are in [truth packs](truth-packs.md).

## Pinning a source

Every source in `brief/truth.json` has an id and a pin: a commit, a retrieval date or a SHA-256. A claim cites a pinned
source by id, with where in it (`path:line`, a page, a section). A number with no pinned source doesn't go in the film.

```json
{
 "claim_authority": "the person who rules on this brand's claims",
 "sources": [
  {"id": "app", "title": "The product's source", "pin": "commit 3f2a9c1", "note": "read in place, never copied"},
  {"id": "site", "title": "The public website", "url": "https://example.com", "retrieved_at": "2026-09-30", "pin": "2026-09-30"}
 ],
 "claims": [
  {"id": "c1", "text": "Plans a week in one view", "source": "app", "where": "src/views/week.tsx:40"}
 ],
 "never_claim": ["works offline"],
 "sample_data": {"company": "an invented sample company, always labelled as sample data"}
}
```

## Reading company code

- Read it in place, on the machine that holds it, with read-only commands (`git show`, `git grep` at the pinned
  commit). Never copy it to another machine, into Slate, or into a prompt for a generator.
- Quote the exact copy the product uses (labels, statuses, limits) with `path:line`, and re-verify quotes before the
  build: a first pass once had to correct line numbers on about a hundred quotes.
- A voice brief or research note that quotes company code stays with the company's material; it never travels into a
  personal or public package.

## Templates

### Product truth

For a product film, write one product-truth document the builders work from:
- the pinned commit;
- the product in one paragraph;
- the journey order (how a person really goes through it);
- every screen the film shows, with its exact copy;
- the product's agent or character, if it has one;
- secondary views (partners, admins, settings);
- the product's own motion (its CSS easing tokens or its springs);
- the palette and type the film takes;
- **live versus not built**: what's shipped, what's stubbed or off by default (never shown as working);
- numbers and words to get exactly right;
- where the product disagrees with itself.

### Capability brief (for a product character or agent)

How it was checked; sources; what it knows; how it synthesizes and explains; what it suggests next; its guardrails;
**what is not true about it**; where this film differs from the product; and what the film uses. Every line carries a
source. Describe a character only with capabilities the owner treats as true.

### Reference analysis claims

When a film remakes or answers an earlier film, list every claim in the original that conflicts with the brand's
current website or product, and decide each with the claim authority before writing.

## Checking numbers

- Recheck an original's statistics against a current public source; cut or correct any that don't match.
- A figure from an older source that can't be verified goes to the claim authority before it's used.
- Percentiles, ranges and derived numbers are computed from the product's own model, not eyeballed.
- One company, one set of numbers: every shot that shows the sample reads from one sample-data record, so the same
  company never shows two different weeks.
