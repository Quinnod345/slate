"""Benchmarks that decide whether a second engine earns a place in Slate.

- `slate.bench.higgsedit`: phase 8. Higgsedit against a Remotion cut-down for a social channel, on the same film:
  wall time, PSNR and SSIM against the master, and a person's review, with the decision rule in every report. The
  Higgsedit arm needs Quinn (it bills unfunded Higgsfield app credits), so `run` refuses and says so.
- `slate.bench.quality`: PSNR and SSIM of a cut against its master's frames, frame for frame.
"""
