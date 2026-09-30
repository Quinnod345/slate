/*  open_project.jsx: reports which project After Effects has open, whether it has unsaved changes, and the render
    queue's state. Read-only; the preflight before anything touches a project. ES3. */
#include "lib/slate_math.jsx"
#include "lib/slate_ae.jsx"

SL.main(function () {
    var p = app.project, st = SL.projectState();
    SL.put("file", st.file || "(untitled)");
    SL.put("dirty", st.dirty);
    SL.put("items", st.items);
    try { SL.put("rq_items", p.renderQueue.numItems); SL.put("rendering", p.renderQueue.rendering); }
    catch (e) { SL.put("rq_items", null); }
    return "OK";
});
