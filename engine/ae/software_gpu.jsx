/*  software_gpu.jsx: switches a project to Mercury Software Only and saves it. ES3.

    For projects whose comps are footage from code renders, GPU acceleration buys nothing, and aerender crashed twice
    inside the GPU compositing engine under memory pressure on the film Mac. Unsaved work in whatever project was open
    is preserved first. Args: {aep, preserve_dir}. */
#include "lib/slate_math.jsx"
#include "lib/slate_ae.jsx"

SL.main(function () {
    var args = SL.args(), kept;
    if (!args.aep) { return "ERROR no project path was passed"; }
    kept = SL.openProject(args.aep, args.preserve_dir);
    if (kept) { SL.put("preserved_project", kept); }
    SL.put("before", String(app.project.gpuAccelType));
    app.project.gpuAccelType = GpuAccelType.SOFTWARE;
    app.project.save();
    SL.put("after", String(app.project.gpuAccelType));
    return "OK";
});
