# The voice reference

The brand's voice is the third reference: one approved take, with its model, settings and seed, and a voice profile in
the pack that every film starts from.

## Making it

1. **Cast or design** (slate-voice): a shootout of library voices, or a designed voice from a brief grounded in the
   brand's own words.
2. **Direct it** with the full-voice rule (never whisper, soft, quiet or gentle tags) and a small tag vocabulary that
   fits the voice's character.
3. **Clean it** the way the owner picks by ear, and record the chain.
4. **Approve one take** by ear, the one that sounds like the brand, and record it with its SHA-256.

## The profile in the pack

`brand.json` → `voice` (the voice profile schema):
- `provider`, `source` (library, designed, clone or recording), `voice_id`, `voice_name`, `model`;
- `settings` (stability and similarity to start), `seed`, `output_format`, `language`;
- `lexicon`: the brand's own words, which every take must say in full;
- `tag_policy`: extra banned words for this brand, the allowed vocabulary, a cap on tags per line;
- for a clone: `clone_of` (`owner` for the owner's own voice), `clone_kind` (professional), `verified_by_owner`;
- for recordings: `speaker`;
- `pace` and `clean` defaults for this voice.

A film pins what it uses at first use, so the brand's profile can evolve without changing films in progress.

## Rules

- Designed or library voices by default; the owner's own verified professional clone is allowed; anyone else's voice
  only as a real recording, never a clone, never a public figure's (slate-voice, voice-policy.md).
- A voice brief that quotes the company's internal material stays with the company's material.
- Designed voice ids belong to the vendor account that made them.
- Never ship the brand's character voice as a demo outside its films.
