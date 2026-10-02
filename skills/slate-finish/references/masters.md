# Masters

A master is the film as delivered: the act renders joined, with the final mix, at the delivery bar. Two routes make one.

| Film | Master | Who starts it |
|---|---|---|
| Supervised | After Effects: an editable project built from the film's data, and aerender masters (ProRes 422 HQ and H.264) | the owner, with `slate master --ae --go` |
| Autonomous, draft | ffmpeg: the act renders joined by stream copy, with the mix | Slate, with `slate master --ffmpeg`; the film is then held for the owner |

The owner can start the AE finish for an autonomous film themselves, once they approve it: a final-cut approval of
the held master (`slate approve final_cut <master> --by NAME`), then `slate master --ae --go` in their own Terminal. It
never runs as a queued job or with `--detach`, and an agent can't start it: Slate judges the film by its own record of
the mode it was made in, whatever film.json says. The AE route in detail: [AE finish](ae-finish.md).

## The checks every master passes

- **Master against renders, frame for frame**: every frame at least 40 dB PSNR against the act render it came from, and
  every frame there. On the first films this check caught AE converting the colour (the darkest brand colour 8 code
  values off, 33 dB) and a one-frame lag from the third act on (the picture 33 ms behind the sound). Neither showed up in
  any other check.
- **Exact frame counts**, for the master and every act.
- **The mix** is exactly as long as the picture; never trim to the shortest stream (it cut the last four frames once).
- **The AE project audit** (supervised films): the project's settings, comps, footage and render format, as gate results.
- **Loudness** of the master's audio on target.

`slate qa frames loudness psnr` runs them (slate-qa). To compare any two pictures or videos directly,
`slate qa psnr <a> <b>` prints their PSNR, frame for frame (frames pair by their place in each file, whatever
timestamps the containers kept). `--a-frames` and `--b-frames FIRST-LAST` compare a stretch of either side: one
shot's render against its frames in a whole delivered act, with no cut file.

## Formats inside a master

- ProRes 422 HQ, 10-bit, BT.709, with the mix as 48 kHz, 24-bit PCM: the archival master.
- H.264 with AAC at a high bitrate: the everyday master.
- Colour tags stamped on every stream (primaries, transfer, matrix), so the next tool doesn't guess.

## Loudness targets

The film's format sets its loudness target (−14 LUFS integrated, true peak at or under −1 dBTP, is the common web
target). Platforms differ: some normalize to −14, broadcast wants −23 (EBU R 128) or −24 (ATSC A/85), and one large
platform's −14 is reported but not documented. Choose each destination's target deliberately, verify it with a meter, and
derive a separate mix for a destination that needs a different one rather than re-mastering by hand.
