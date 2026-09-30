/*  queue_masters.jsx: puts a film's master comp in its project's render queue as chunks, for aerender. ES3.

    `slate ae build --go` already queues the masters while it builds; this re-queues an existing project (after a
    change to the chunk count, say) without rebuilding it: `slate ae masters --go --requeue`.

    Each chunk is one render-queue item with "Best Settings" (or the spec's template) and two output modules, so one
    aerender pass writes both masters' frames: the primary (H.264) and the alpha (ProRes 4444) that slate.ae.masters
    conforms to ProRes 422 HQ, because a stock output module's codec can't be set by script and Slate never adds
    templates to the person's preferences. Every item's span is read back in frames. Anything already in the queue
    is removed first. Unsaved work in whatever project was open is preserved before the film's project is opened.
*/
#include "lib/slate_math.jsx"
#include "lib/slate_ae.jsx"

SL.main(function () {
    var args = SL.args(), spec, film, kept, M, q, i, bad = [];
    if (!args.spec) { return "ERROR no build spec was passed (run it through `slate ae masters`)"; }
    spec = SL.readJSON(args.spec);
    film = SL.film(spec);
    if (!spec.masters) { return "ERROR the spec has no masters plan"; }
    kept = SL.openProject(spec.paths.aep, spec.paths.preserve_dir);
    if (kept) { SL.put("preserved_project", kept); }
    M = SL.findComp(spec.names.master);
    if (!M) { return "ERROR no comp called " + spec.names.master + " in " + spec.paths.aep; }
    q = SL.queueMasters(app.project, M, film, spec.masters);
    for (i = 0; i < q.length; i++) { if (!q[i].match) { bad.push(q[i].rqindex); } }
    SL.put("queue", q);
    app.project.save();
    SL.put("saved", spec.paths.aep);
    return bad.length ? "ERROR queue items " + bad.join(",") + " don't match the plan" : "OK";
});
