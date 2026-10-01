# Measuring a read: inflection, breath and spectrograms

Takes, tags and polish strengths are judged by measurement as well as by ear. The analysis stage measures any take (a
WAV, or a shot in the film) and compares takes against a reference, with an A/B spectrogram sheet to look at while you
listen. `slate voice --help` lists it; its own `--help` gives every option.

It came from the first film's tag work. The owner heard whispery tags as "a little bit scary" and suggested "analyzing
the waveforms and frequencies, like a spectrogram" to gauge the emotion. The measures below are the ones that film used,
with the same meanings.

## What's measured

| Family | Measure | What it catches |
|---|---|---|
| Pitch | F0 median, mean, range (5th to 95th percentile, semitones), movement (SD), and the contour | monotone or sing-song; a smile lifts pitch |
| Breath and effort | voiced fraction, CPPS (cepstral peak prominence), HNR (harmonics-to-noise), H1-H2, spectral tilt, level | a whisper has no pitch; breath fills the harmonics; a soft voice tilts steeply |
| Brightness and smile | spectral centroid, F1 and F2 | a smile shortens the vocal tract and lifts F2 (only between takes of the same words) |
| Sibilance and edge | the 5-9 kHz and 2-5 kHz bands against the whole sound, and their loudest 5 ms moments | harsh S sounds, a glassy edge |
| Energy | BS.1770 loudness and true peak, the level contour's spread, the last word against the rest | a fading or flat delivery |
| Creak | voiced frames far under the median pitch, irregular pitch, and the words they fall on | vocal fry on a key phrase |
| Pacing | words a second over the speech span, articulation rate, pauses, the quiet at each sentence end | rushed or dragging |
| Endings | the last word's pitch across it, its contour (start, peak, end, fall, range), every question word's rise | a statement that rises like a question |
| A clean end | how far the last 20 ms sit under the speech | a take that stops mid-word (under 35 dB) |
| The Whisper checks | every word heard (WER), no repeats, no tag read aloud, every lexicon word in full, no voice after the last word | misreads, leaked tails |
| The tag's scope | all of the above from the first word an emotion tag governs to the end | a tag on the last word that whispers just that word |

Without a take's heard words the word-timed measures are left out, never guessed: in a film the alignment supplies
them (when it times that very audio), and for loose files you give a Whisper JSON or ask for a local Whisper run.

## Comparing against a reference

Name a reference and every other take is compared with it: each measure's change (pitch in semitones), the sibilance
and edge changes, and a breath verdict. With one reference take there's no spread to measure against, so the verdict
uses fixed floors; it flags a big change, and it's strongest when the voice drops out (a whisper). For the full rule,
compare against several untagged reads of the same words (the tag loop does).

## The sheet

One PNG, every take on one time scale and one colour scale (each brought to -20 LUFS first), the reference first:
- the spectrogram (0-8 kHz for takes, 0-12 kHz for polish comparisons), with 2, 5 and 9 kHz marked;
- the words under it;
- the pitch contour, with the reference's median pitch dashed across it;
- the level contour;
- a measurement column, with the breath verdict against the reference.

Look for: voicing that thins out on a tagged phrase (a whisper), harmonics fading into noise (breath), a pitch line
that hooks up at the end of a statement (a question rise), S bursts that stand out of the 5-9 kHz band (harshness),
a level line that collapses before the last word (fading).

## The long-term spectrum

Ask for the spectrum too (the analyze stage's spectrum option) and every take gets its third-octave long-term
spectrum over speech at -20 LUFS, set against natural speech's shape (ANSI S3.5's standard speech spectrum) over
500 Hz-1 kHz, in a chart beside the sheet (`spectrum.png`): the films' "where the harshness lives" picture. A curve
well over the grey line at 2-5 kHz is edge (a glassy, pushed sound); at 5-9 kHz it's sibilance. It also lists the
band levels (lows, body, the 1-5 kHz clarity band, edge, sibilance, air), the edge and sibilance bands' median and
loudest 5 ms moments, and the words those loudest moments fall in, which tell you exactly where to listen. The
polish's trial mode draws the same chart for the unpolished lines against each strength, pooled over every line.

## When to use it

- **Before the owner hears a tagged take**: against the voice's untagged reads of the same words. A tagged read that
  measures breathier is out, however good its tag looks.
- **Choosing a polish strength**: the unpolished take against each strength, on a 0-12 kHz sheet (the polish stage's
  trial mode draws these for every line). Read the sibilance, edge and spectrum there, not the breath: the room and
  the EQ move CPPS and H1-H2 without any change of breath, so those sheets leave the breath verdict out.
- **A re-voice against the approved take**: did the notes change the read, or the voice?
- **Tuning a voice's polish**: where its sibilance and edge actually sit.

## The gate it backs

The inflection gate holds every line to the no-whisper rule by measurement:
- **fails**: a whisper-like read (the voice dropping out, against the film's other lines); a tagged read breathier than
  the voice's own untagged reads of the same words (when a tag round measured it); a statement ending that rises like a
  question (past the voice's own endings plus 3 semitones); a read that stops mid-word;
- **asks for an ear**: a tagged line nobody has measured against untagged reads of the same words, until the owner
  approves the take by ear;
- **notes, not verdicts**: breathiness or creak against the film's other lines (other words differ, so these only
  point where to listen).

The film's breath and pitch measures read each take before its polish, so the polish never becomes its own baseline.

## What it can't do

These are proxies. They can tell a read is breathy, whispered, creaky, rising, rushed, harsh or misheard. They can't
hear warmth, sincerity or a smile the way a person does. The owner still picks by ear.
