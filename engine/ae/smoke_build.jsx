/*  smoke_build.jsx: the one-second toolchain smoke test, built with Slate's ES3 library. ES3.

    Run it through Slate: `slate ae smoke --go` (or `slate connect aftereffects`). It builds one comp: a dot on a
    dark ground that travels across the frame on a cubic-bezier ease written as AE speed and influence (SL.anim),
    with the film's motion-blur settings. It reads back what AE stored, samples AE's own curve at every frame against
    the ease, lists the output-module and render-settings templates aerender can use, reports how the "Allow Scripts
    to Write Files and Access Network" setting reads (it never changes it), and saves smoke.aep in the out folder.
    slate.ae.smoke then renders it with aerender and measures the dot in the pixels.

    Safety: a project with unsaved changes stops the test (ABORT) rather than being replaced; nothing is discarded.
    It never writes a file itself (with the file-write setting off, AE can raise a modal that stalls the call);
    the project save is AE's own.

    Args: {out_dir, comp_name, width, height, fps_num, fps_den, frames, start_frame, end_frame, from_x, to_x, y,
           diameter, ease: [x1, y1, x2, y2], bg, dot}
*/
#include "lib/slate_math.jsx"
#include "lib/slate_ae.jsx"

SL.main(function () {
    var a = SL.args(), proj = app.project, film, comp, dot, pos, e1, e2, f, got, want, u, worst = 0, curve = [];
    var sections = ["Main Pref Section v2", "Main Pref Section"], pv = "unread", s, rqi, fromX, toX;

    for (s = 0; s < sections.length && pv === "unread"; s++) {
        try {
            pv = app.preferences.getPrefAsLong(sections[s], "Pref_SCRIPTING_FILE_NETWORK_SECURITY",
                                               PREFType.PREF_Type_MACHINE_SPECIFIC);
        } catch (ePref) { pv = "unread"; }
    }
    SL.put("pref_scripting_file_network_security", pv);

    if (proj && proj.numItems > 0 && proj.dirty) { return "ABORT open project has unsaved changes"; }
    if (!proj || proj.numItems > 0 || proj.file !== null) { proj = app.newProject(); }

    film = SL.film({ film: { width: a.width, height: a.height, fps_num: a.fps_num, fps_den: a.fps_den, pixel_aspect: 1 },
                     project: {}, brand: { eases: { smoke: a.ease } } });
    comp = SL.comp(film, a.comp_name, a.frames);
    comp.bgColor = SL.hex(a.bg);
    SL.solid(comp, film, "BG", a.bg).motionBlur = false;
    dot = SL.shapeLayer(comp, "Dot");
    SL.group(dot, "Dot");
    SL.ellipse(SL.contents(dot).property("Dot"), a.diameter, a.diameter);
    SL.fill(SL.contents(dot).property("Dot"), SL.hex(a.dot));
    pos = SL.xf(dot, "position");
    fromX = a.from_x;
    toX = a.to_x;
    SL.anim(pos, film, [[a.start_frame, [fromX, a.y], "smoke"], [a.end_frame, [toX, a.y], "linear"]]);

    e1 = SL.readEase(pos, 1);
    e2 = SL.readEase(pos, 2);
    SL.put("readback", { key1_out: e1.out, key2_in: e2.inn,
                         interp: pos.keyOutInterpolationType(1) === KeyframeInterpolationType.BEZIER ? "BEZIER" : "other" });
    SL.put("expected_ease", SL.segEase(SL.ease("smoke").points, Math.abs(toX - fromX) / film.clock.secs(a.end_frame - a.start_frame)));

    for (f = 0; f < a.frames; f++) {
        got = pos.valueAtTime(film.clock.secs(f), false)[0];
        u = (f - a.start_frame) / (a.end_frame - a.start_frame);
        want = fromX + (toX - fromX) * SL.easeValue("smoke", u);
        curve.push([f, got, want]);
        worst = Math.max(worst, Math.abs(got - want));
    }
    SL.put("curve", curve);
    SL.put("curve_max_error_px", worst);

    rqi = proj.renderQueue.items.add(comp);
    SL.put("om_templates", rqi.outputModule(1).templates);
    SL.put("rs_templates", rqi.templates);
    rqi.remove();

    SL.ensureFolder(a.out_dir);
    proj.save(new File(a.out_dir + "/smoke.aep"));
    SL.put("saved", a.out_dir + "/smoke.aep");
    SL.put("comp", { name: comp.name, width: comp.width, height: comp.height, fps: comp.frameRate,
                     frames: SL.compFrames(film, comp) });
    return "OK";
});
