/*  build_film.jsx: builds a film's editable After Effects project from Slate's build spec. ES3.

    Run it through Slate, never by hand: `slate ae build --go` writes <film>/ae/build_spec.json from film.json,
    timing/shots.json, timing/timeline.json, the renders registry, the brand pack and the mix, then runs this
    script inside AE (slate.ae.driver) and reads its report back.

    What it makes, from an empty project (unsaved work in whatever was open is saved to the preserve folder first):
      - project settings: 16 bpc, the colour working space the renders already carry (Rec.709 Gamma 2.4 for
        BT.709-tagged renders, so AE converts nothing), linear blending off, Mercury Software Only (aerender crashed
        twice in the GPU compositor on a footage-only project);
      - one comp per shot holding its frame-exact slice of the act render (or a placeholder the audit fails);
      - one comp per act, its shots in order, or, for an act built in AE, the act's scene: layers and keyframes on the
        film clock with the brand's named eases as AE speed and influence;
      - the master comp: every act, the mix, and markers for each shot, each word of the voice and each timeline event;
      - a brand board: the palette as swatches, each font role as a line of type (fonts are set by name, so a missing
        font shows up in the audit), and every brand logo as native shape groups;
      - the masters queue: one render-queue item per chunk with two output modules (slate.ae.masters renders them).
    Every nested layer starts a tenth of a frame early (the sample bias). The report lists what was built and a set
    of self-checks; the full audit is validate.jsx (`slate ae audit`).
*/
#include "lib/slate_math.jsx"
#include "lib/slate_ae.jsx"

SL.main(function () {
    var args = SL.args(), spec, film, P, F, kept, i, j, k, byId = {}, shotComp = {}, actComp = {}, rItems = {};
    var built = { rendered: [], placeholders: [], ae_acts: [] }, mix = null, aud = null, M, evl, checks = [];
    var sh, act, c, r, rl, ac, lay, al, res, missing, fr, board, q, names;

    if (!args.spec) { return "ERROR no build spec was passed (run it through `slate ae build`)"; }
    spec = SL.readJSON(args.spec);
    film = SL.film(spec);
    names = spec.names;
    SL.put("film", spec.film.id);

    // 1. A fresh project; unsaved work in whatever was open is preserved, never discarded.
    kept = SL.freshProject(spec.paths.preserve_dir);
    if (kept) { SL.put("preserved_project", kept); }
    P = app.project;

    // 2. Colour and rendering: the working space is the profile AE assigns to the renders themselves, so the
    //    brand's colours reach the master unchanged (an sRGB working space once darkened a deep colour ~8 levels).
    P.bitsPerChannel = spec.project.bpc;
    try { P.workingSpace = spec.project.working_space; } catch (eW) { SL.warn("working space: " + eW.toString()); }
    P.linearBlending = !!spec.project.linear_blending;
    if (spec.project.gpu === "software") {
        try { P.gpuAccelType = GpuAccelType.SOFTWARE; } catch (eG) { SL.warn("gpu: " + eG.toString()); }
    }
    SL.put("project", { bpc: P.bitsPerChannel, working_space: String(P.workingSpace), linear_blending: P.linearBlending });

    // 3. Folders and imports.
    F = { shots: SL.folder(names.folders.shots), acts: SL.folder(names.folders.acts),
          master: SL.folder(names.folders.master), renders: SL.folder(names.folders.renders),
          audio: SL.folder(names.folders.audio), brand: SL.folder(names.folders.brand) };
    if (spec.audio && spec.audio.file) {
        mix = SL.importFile(spec.audio.file);
        mix.parentFolder = F.audio;
        SL.put("audio", { file: spec.audio.file, seconds: mix.duration });
    }
    for (i = 0; i < spec.renders.length; i++) {
        r = spec.renders[i];
        if (!rItems[r.file]) {
            rItems[r.file] = SL.importFile(r.file);
            rItems[r.file].parentFolder = F.renders;
            rItems[r.file].mainSource.conformFrameRate = 0;   // keep the render's own rate
        }
    }
    for (i = 0; i < spec.shots.length; i++) { byId[spec.shots[i].id] = spec.shots[i]; }

    function renderFor(s) {
        var q2;
        for (q2 = 0; q2 < spec.renders.length; q2++) {
            if (s.frame_in >= spec.renders[q2].frame_in && s.frame_out <= spec.renders[q2].frame_out) {
                return spec.renders[q2];
            }
        }
        return null;
    }

    // 4. Shot comps (acts rendered in code): each holds its frame-exact slice of the act render.
    for (i = 0; i < spec.acts.length; i++) {
        act = spec.acts[i];
        if (act.engine === "ae") { continue; }
        for (j = 0; j < act.shots.length; j++) {
            sh = byId[act.shots[j]];
            c = SL.comp(film, sh.id, sh.frames);
            c.parentFolder = F.shots;
            r = renderFor(sh);
            if (r) {
                rl = c.layers.add(rItems[r.file]);
                rl.name = (r.label || r.file) + " (film frames " + r.frame_in + "-" + r.frame_out + ")";
                SL.place(film, rl, r.frame_in - sh.frame_in);
                c.comment = "RENDERED · " + (r.label || r.file) + " · " + (sh.picture || "");
                built.rendered.push(sh.id);
            } else {
                SL.placeholder(c, film, sh);
                built.placeholders.push(sh.id);
            }
            shotComp[sh.id] = c;
        }
    }

    // 5. Act comps: shots in order, or the act's scene when it is built in AE.
    for (i = 0; i < spec.acts.length; i++) {
        act = spec.acts[i];
        ac = SL.comp(film, act.comp, act.frame_out - act.frame_in, { bg: act.bg || undefined });
        ac.parentFolder = F.acts;
        if (act.engine === "ae") {
            res = SL.buildScene(ac, film, act.scene, { logos: (spec.brand && spec.brand.logos) || {} });
            ac.comment = "AE-BUILT · " + res.layers + " layers, " + res.anims + " animations";
            built.ae_acts.push({ act: act.index, layers: res.layers, anims: res.anims });
        } else {
            for (j = act.shots.length - 1; j >= 0; j--) {
                sh = byId[act.shots[j]];
                lay = ac.layers.add(shotComp[sh.id]);
                SL.place(film, lay, sh.frame_in - act.frame_in);
                lay.motionBlur = true;
            }
            ac.comment = "SHOTS · " + act.shots.length;
        }
        for (j = 0; j < (act.markers || []).length; j++) {
            SL.marker(ac.markerProperty, film, act.markers[j].frame - act.frame_in, act.markers[j].label);
        }
        actComp[act.index] = ac;
    }

    // 6. The master: every act, the mix, markers per shot, word and event.
    M = SL.comp(film, names.master, spec.film.total_frames);
    M.parentFolder = F.master;
    for (i = spec.acts.length - 1; i >= 0; i--) {
        act = spec.acts[i];
        al = M.layers.add(actComp[act.index]);
        SL.place(film, al, act.frame_in);
        al.motionBlur = true;
    }
    if (mix) {
        aud = M.layers.add(mix);
        aud.name = "AUDIO " + spec.audio.file.replace(/^.*\//, "");
        aud.startTime = 0;
    }
    for (i = 0; i < spec.markers.shots.length; i++) {
        SL.marker(M.markerProperty, film, spec.markers.shots[i].frame, spec.markers.shots[i].label);
    }
    evl = SL.nullLayer(M, "EVENTS (timing/timeline.json)");
    for (i = 0; i < spec.markers.events.length; i++) {
        SL.marker(evl.property("ADBE Marker"), film, spec.markers.events[i].frame, spec.markers.events[i].label);
    }
    for (i = 0; i < spec.markers.words.length; i++) {
        SL.marker((aud || evl).property("ADBE Marker"), film, spec.markers.words[i].frame, spec.markers.words[i].label);
    }
    SL.put("markers", { shots: spec.markers.shots.length, words: spec.markers.words.length,
                        events: spec.markers.events.length });

    // 7. The brand board: palette swatches, type by role, logos as shapes.
    board = SL.brandBoard(film, spec, F.brand);
    SL.put("brand_board", board);

    // 8. The masters queue.
    if (spec.masters) {
        q = SL.queueMasters(P, M, film, spec.masters);
        SL.put("queue", q);
        for (i = 0; i < q.length; i++) {
            if (!q[i].match) { SL.warn("queue item " + q[i].rqindex + " spans " + q[i].start + "+" + q[i].frames); }
        }
    }

    // 9. Self-checks (the full audit is validate.jsx).
    for (k in shotComp) {
        if (shotComp.hasOwnProperty(k)) {
            fr = SL.compFrames(film, shotComp[k]);
            if (fr !== byId[k].frames) { checks.push(k + " frames " + fr + " != " + byId[k].frames); }
        }
    }
    if (SL.compFrames(film, M) !== spec.film.total_frames) { checks.push("master frames " + SL.compFrames(film, M)); }
    missing = 0;
    for (i = 1; i <= P.numItems; i++) {
        if (P.item(i) instanceof FootageItem && P.item(i).footageMissing) { missing++; }
    }
    SL.put("check", { frames: checks, missing_footage: missing });
    try { SL.put("fonts_missing_or_substituted", app.fonts.missingOrSubstitutedFonts.length); }
    catch (eF) { SL.put("fonts_missing_or_substituted", null); }
    SL.put("fonts_used", SL.keysOf(film.fontsUsed));
    SL.put("built", built);

    // 10. Save.
    SL.ensureFolder(new File(spec.paths.aep).parent.fsName);
    P.save(new File(spec.paths.aep));
    SL.put("saved", spec.paths.aep);
    return checks.length ? "ERROR self-checks: " + checks.join("; ") : "OK";
});
