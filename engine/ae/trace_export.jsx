/*  trace_export.jsx: samples every animated property of a film's AE-built acts, frame by frame, as After Effects
    itself evaluates them. ES3.

    Works for any act built in AE, whoever built it: Slate's data-driven scenes or the AE bridge's looks. slate.ae.acts
    turns the samples into the motion-trace contract (timing/motion/actN.json) that `slate qa motion` checks, so an
    AE-built act faces the same gates as a Remotion act. Unsaved work in whatever project was open is preserved
    before the film's project is opened. Args: {spec, acts: [act index, ...], max_samples}.
*/
#include "lib/slate_math.jsx"
#include "lib/slate_ae.jsx"

SL.main(function () {
    var args = SL.args(), spec, film, kept, i, j, act, comp, traces = {}, want = args.acts || [];
    if (!args.spec) { return "ERROR no build spec was passed (run it through `slate ae trace`)"; }
    spec = SL.readJSON(args.spec);
    film = SL.film(spec);
    kept = SL.openProject(args.aep || spec.paths.aep, spec.paths.preserve_dir);
    if (kept) { SL.put("preserved_project", kept); }
    for (i = 0; i < spec.acts.length; i++) {
        act = spec.acts[i];
        for (j = 0; j < want.length; j++) {
            if (want[j] === act.index) {
                comp = SL.findComp(act.comp);
                if (!comp) { return "ERROR no comp called " + act.comp; }
                traces[String(act.index)] = SL.sampleComp(comp, film, { max_samples: args.max_samples || 2000 });
            }
        }
    }
    SL.put("traces", traces);
    return "OK";
});
