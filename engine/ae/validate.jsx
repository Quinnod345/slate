/*  validate.jsx: audits a film's After Effects project against its build spec. ES3.

    Run it through Slate: `slate ae audit <aep> --go`. It opens the project (unsaved work in whatever was open is
    preserved first), checks it, and reports one result per check: PASS, FAIL or UNCHECKED (a check that couldn't
    run is never reported as passed). slate.ae.audit turns each into a gate result.

    Checks (generalised from the film pipeline's 14):
      project.bpc            the spec's bit depth (16)
      project.workingSpace   the spec's working space (the renders' own profile, so AE converts nothing)
      project.linearBlending off
      shots.comps            one comp per shot of every rendered act: the film's size, square pixels (or the spec's),
                             the film's rate (to 1e-3), frames equal to shots.json; a rendered slice starts on the
                             right film frame, a tenth of a frame early, to 0.02 frame, and doesn't end early
      shots.picture          no placeholder comps
      acts.comps             one comp per act with the act's frames; rendered acts hold their shots in order at the
                             biased start (0.02 frame); AE-built acts hold their scene's layers
      footage.missing        no missing footage
      renders.format         every code render at the film's size and rate
      master.frames          the master equals the film's total frames
      master.audio           an audio layer covers the film (0.05 s), when the film has a mix
      master.markers         at least one marker per shot
      queue.chunks           the render queue matches the masters plan: spans in frames, two outputs each
      fonts                  nothing missing or substituted
      expressions            no expression errors anywhere
      motionblur.native      every animated layer that isn't footage has motion blur on
*/
#include "lib/slate_math.jsx"
#include "lib/slate_ae.jsx"

SL.main(function () {
    var args = SL.args(), spec, film, P, R = [], comps = {}, byId = {}, i, j, k, it, sh, c, frames, probs, bad, ph;
    var rendered, nativeComps, lay, src, expect, r, missing, badRender, actProbs, act, ac, want, M, aud, mf, exErr, mbOff;
    var cn, cc, L, stack, pr, q, fps, tol, rq, items, kept;

    function check(name, ok, evidence) { R.push({ check: name, status: ok ? "PASS" : "FAIL", evidence: evidence || "" }); }
    function unchecked(name, why) { R.push({ check: name, status: "UNCHECKED", evidence: why }); }

    if (!args.spec) { return "ERROR no build spec was passed (run it through `slate ae audit`)"; }
    spec = SL.readJSON(args.spec);
    film = SL.film(spec);
    fps = film.clock.fps;
    tol = 0.02 / fps;
    kept = SL.openProject(args.aep || spec.paths.aep, spec.paths.preserve_dir);
    if (kept) { SL.put("preserved_project", kept); }
    P = app.project;
    for (i = 0; i < spec.shots.length; i++) { byId[spec.shots[i].id] = spec.shots[i]; }

    // project
    check("project.bpc", P.bitsPerChannel === spec.project.bpc, "bpc=" + P.bitsPerChannel);
    check("project.workingSpace", String(P.workingSpace) === spec.project.working_space,
          String(P.workingSpace) + " (want " + spec.project.working_space + ")");
    check("project.linearBlending", P.linearBlending === !!spec.project.linear_blending, String(P.linearBlending));

    for (i = 1; i <= P.numItems; i++) {
        it = P.item(i);
        if (it instanceof CompItem) { comps[it.name] = it; }
    }

    function renderFor(s) {
        var q2;
        for (q2 = 0; q2 < spec.renders.length; q2++) {
            if (s.frame_in >= spec.renders[q2].frame_in && s.frame_out <= spec.renders[q2].frame_out) {
                return spec.renders[q2];
            }
        }
        return null;
    }

    // shots
    bad = []; ph = []; rendered = 0; nativeComps = 0;
    for (i = 0; i < spec.acts.length; i++) {
        if (spec.acts[i].engine === "ae") { continue; }
        for (j = 0; j < spec.acts[i].shots.length; j++) {
            sh = byId[spec.acts[i].shots[j]];
            c = comps[sh.id];
            if (!c) { bad.push(sh.id + " missing"); continue; }
            frames = Math.round(c.duration * fps);
            probs = [];
            if (c.width !== film.w || c.height !== film.h) { probs.push(c.width + "x" + c.height); }
            if (Math.abs(c.pixelAspect - film.par) > 1e-6) { probs.push("par " + c.pixelAspect); }
            if (Math.abs(c.frameRate - fps) > 1e-3) { probs.push("fps " + c.frameRate); }
            if (frames !== sh.frames) { probs.push("frames " + frames + "!=" + sh.frames); }
            if (c.comment && c.comment.indexOf("PLACEHOLDER") === 0) { ph.push(sh.id); }
            if (c.comment && c.comment.indexOf("RENDERED") === 0) {
                rendered++;
                lay = c.layer(c.numLayers);
                src = lay.source;
                r = renderFor(sh);
                expect = r ? (r.frame_in - sh.frame_in) / fps - film.bias : null;
                if (expect === null || Math.abs(lay.startTime - expect) > tol) {
                    probs.push("slice starts at " + lay.startTime.toFixed(5) + " (want " +
                               (expect === null ? "?" : expect.toFixed(5)) + ")");
                }
                if (src && (src.duration + lay.startTime) < c.duration - 0.5 / fps) { probs.push("render ends early"); }
            } else if (c.numLayers > 0) { nativeComps++; }
            if (probs.length) { bad.push(sh.id + ": " + probs.join(", ")); }
        }
    }
    check("shots.comps", bad.length === 0, bad.length ? bad.join("; ") :
          "every shot comp at " + film.w + "x" + film.h + " and the film's rate, frames match shots.json");
    check("shots.picture", ph.length === 0, ph.length ? "placeholders: " + ph.join(",") :
          rendered + " from code renders, " + nativeComps + " native");

    // acts
    actProbs = [];
    for (i = 0; i < spec.acts.length; i++) {
        act = spec.acts[i];
        ac = comps[act.comp];
        if (!ac) { actProbs.push("act " + act.index + " missing"); continue; }
        if (Math.round(ac.duration * fps) !== act.frame_out - act.frame_in) {
            actProbs.push("act " + act.index + " frames " + Math.round(ac.duration * fps));
        }
        if (act.engine === "ae") {
            if (ac.numLayers < act.scene.layers.length) {
                actProbs.push("act " + act.index + " has " + ac.numLayers + " layers, its scene has " + act.scene.layers.length);
            }
            continue;
        }
        for (j = 0; j < act.shots.length; j++) {
            sh = byId[act.shots[j]];
            lay = null;
            for (k = 1; k <= ac.numLayers; k++) {
                if (ac.layer(k).source && ac.layer(k).source.name === sh.id) { lay = ac.layer(k); }
            }
            if (!lay) { actProbs.push(sh.id + " not in act " + act.index); continue; }
            want = (sh.frame_in - act.frame_in) / fps - film.bias;
            if (Math.abs(lay.startTime - want) > tol) {
                actProbs.push(sh.id + " at " + lay.startTime.toFixed(4) + " (want " + want.toFixed(4) + ")");
            }
        }
    }
    check("acts.comps", actProbs.length === 0, actProbs.length ? actProbs.join("; ") :
          spec.acts.length + " act comps, frame-exact");

    // footage and renders
    missing = []; badRender = [];
    for (i = 1; i <= P.numItems; i++) {
        it = P.item(i);
        if (it instanceof FootageItem) {
            if (it.footageMissing) { missing.push(it.name); continue; }
            if (it.parentFolder && it.parentFolder.name === spec.names.folders.renders) {
                if (it.width !== film.w || it.height !== film.h || Math.abs(it.frameRate - fps) > 1e-3) {
                    badRender.push(it.name + " " + it.width + "x" + it.height + "@" + it.frameRate);
                }
            }
        }
    }
    check("footage.missing", missing.length === 0, missing.length ? missing.join(", ") : "none");
    check("renders.format", badRender.length === 0, badRender.length ? badRender.join("; ") :
          "every code render at the film's size and rate");

    // master
    M = comps[spec.names.master];
    if (!M) {
        check("master.frames", false, "no comp called " + spec.names.master);
    } else {
        check("master.frames", Math.round(M.duration * fps) === spec.film.total_frames,
              Math.round(M.duration * fps) + " frames (want " + spec.film.total_frames + ")");
        if (spec.audio && spec.audio.file) {
            aud = null;
            for (i = 1; i <= M.numLayers; i++) {
                L = M.layer(i);
                if (L.hasAudio && !L.hasVideo && L.source && L.source instanceof FootageItem) { aud = L; }
            }
            check("master.audio", aud !== null && aud.source.duration >= M.duration - 0.05,
                  aud ? aud.source.name + " " + aud.source.duration.toFixed(3) + " s" : "no audio layer");
        } else {
            unchecked("master.audio", "the film has no mix yet");
        }
        check("master.markers", M.markerProperty.numKeys >= spec.shots.length, M.markerProperty.numKeys + " markers");
    }

    // queue
    if (spec.masters) {
        rq = P.renderQueue.items;
        items = [];
        if (rq.length !== spec.masters.chunks.length) {
            items.push(rq.length + " items (want " + spec.masters.chunks.length + ")");
        } else {
            for (i = 0; i < spec.masters.chunks.length; i++) {
                q = rq[i + 1];
                if (Math.round(q.timeSpanStart * fps) !== spec.masters.chunks[i].start ||
                    Math.round(q.timeSpanDuration * fps) !== spec.masters.chunks[i].frames || q.numOutputModules !== 2) {
                    items.push("item " + (i + 1) + " spans " + Math.round(q.timeSpanStart * fps) + "+" +
                               Math.round(q.timeSpanDuration * fps) + " with " + q.numOutputModules + " outputs");
                }
            }
        }
        check("queue.chunks", items.length === 0, items.length ? items.join("; ") :
              spec.masters.chunks.length + " chunks, two outputs each");
    } else {
        unchecked("queue.chunks", "no masters plan in the spec");
    }

    // fonts
    try {
        mf = app.fonts.missingOrSubstitutedFonts;
        check("fonts", mf.length === 0, mf.length + " missing or substituted");
    } catch (eF) { unchecked("fonts", "this AE can't list missing fonts: " + eF.toString()); }

    // expressions and motion blur
    exErr = []; mbOff = [];
    for (cn in comps) {
        if (!comps.hasOwnProperty(cn)) { continue; }
        cc = comps[cn];
        for (i = 1; i <= cc.numLayers; i++) {
            L = cc.layer(i);
            stack = [L];
            while (stack.length) {
                pr = stack.pop();
                for (j = 1; j <= pr.numProperties; j++) {
                    q = pr.property(j);
                    if (q.propertyType === PropertyType.PROPERTY) {
                        if (q.canSetExpression && q.expressionEnabled && q.expressionError) {
                            exErr.push(cn + "/" + L.name + "/" + q.name);
                        }
                    } else { stack.push(q); }
                }
            }
            if (!(L.source instanceof FootageItem) && L.transform && SL.positionKeyed(L.transform.position) &&
                !L.motionBlur) {
                mbOff.push(cn + "/" + L.name);
            }
        }
    }
    check("expressions", exErr.length === 0, exErr.length ? exErr.slice(0, 10).join("; ") : "no expression errors");
    check("motionblur.native", mbOff.length === 0, mbOff.length ? mbOff.slice(0, 10).join("; ") :
          "every animated native layer has motion blur on");

    SL.put("checks", R);
    SL.put("aep", P.file ? P.file.fsName : null);
    return "OK";
});
