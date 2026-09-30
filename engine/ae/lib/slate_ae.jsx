/*  slate_ae.jsx: Slate's After Effects library (ES3). A script includes slate_math.jsx first, then this file (see
    the two include lines at the top of build_film.jsx). No include directive may start a line inside a comment
    here: ExtendScript's preprocessor reads directives line by line.

    Generalised from the film pipeline's lib.jsx. Nothing here knows a brand or a film: sizes, the frame rate, colours,
    fonts, eases and logos all come from the build spec Slate writes (ae/build_spec.json), and every frame in it was
    resolved by Python on the film clock.

      - Run context: $.global.SLATE_ARGS (the run's arguments) and $.global.SLATE_RUN (run id, report channel), set by
        the wrapper slate.ae.driver writes.
      - Reporting: AE 26.5 returns "0" to AppleScript, so a run reports one JSON object: to report.json when the
        person has allowed scripts to write files, otherwise through app.settings (section "Slate", URI-encoded
        chunks) flushed with app.preferences.saveToDisk(). slate.ae.report reads either back.
      - Safety: a project with unsaved changes is saved to the preserve folder before anything replaces it; nothing
        is ever discarded.
      - Time: comps use the film's rational rate; nested layers start a tenth of a frame early (the sample bias), so
        AE samples inside each frame and never on its edge. Without it, frame times against AE's own grid could land
        a hair before a boundary and AE showed the previous frame (the film's first master came out a frame late in
        two acts; only a frame-by-frame PSNR of master against renders caught it).
      - Eases: the brand's named eases as AE speed + influence per key (SL.segEase), read back for the audit.
      - Shapes, text, logos (svg2ae data), markers, data-driven scenes, the masters queue, the audit, motion traces.

    ES3 rules: var only, no JSON object, no Array.indexOf/forEach/map, no let/const, no trailing commas.
    AE property rules: address properties by match name; re-resolve after every addProperty (AE invalidates sibling
    references when a property is added); ease arrays sized to the property's dimensions (Scale is 3 internally).
*/

var SL = SL || {};

// ------------------------------------------------------------------ run context and reporting

SL.args = function () {
    var a = $.global.SLATE_ARGS;
    return (a && typeof a === "object") ? a : {};
};

SL.runInfo = function () {
    var r = $.global.SLATE_RUN;
    if (!r || typeof r !== "object") { r = { run_id: "manual", channel: "run", section: "Slate", report_file: null }; }
    return r;
};

SL.REPORT = { data: {}, warnings: [], started: 0 };
SL.CHUNK = 1000;

SL.put = function (key, value) { SL.REPORT.data[key] = value; return value; };
SL.warn = function (msg) { SL.REPORT.warnings.push(String(msg)); };

SL._save = function (key, value) {
    var r = SL.runInfo();
    try { app.settings.saveSetting(r.section || "Slate", key, String(value).replace(/"/g, "'")); return true; }
    catch (e) { return false; }
};

SL.begin = function () {
    var r = SL.runInfo();
    SL.REPORT = { data: {}, warnings: [], started: new Date().getTime() };
    SL._save(r.channel + ".id", r.run_id);
    SL._save(r.channel + ".status", "RUNNING");
    try { app.preferences.saveToDisk(); } catch (e) { /* the final flush reports anyway */ }
};

SL.finish = function (status) {
    var r = SL.runInfo(), data = SL.REPORT.data, text, enc, n, i, wrote = false, f;
    status = String(status || "OK").replace(/"/g, "'");
    data.warnings = SL.REPORT.warnings;
    data.seconds = (new Date().getTime() - SL.REPORT.started) / 1000;
    try { data.ae_version = app.version; } catch (eV) { data.ae_version = "unknown"; }
    if (r.report_file) {
        try {
            f = new File(r.report_file);
            f.encoding = "UTF-8";
            if (f.open("w")) {
                f.write(SL.toJSON({ run_id: r.run_id, status: status, data: data }));
                f.close();
                wrote = true;
            }
        } catch (eF) { wrote = false; }
    }
    if (wrote) {
        SL._save(r.channel + ".json.n", 0);
    } else {
        text = SL.toJSON(data);
        try { enc = encodeURIComponent(text); }
        catch (eU) { enc = encodeURIComponent("{\"error\":\"the report held text that couldn't be encoded\"}"); }
        n = Math.max(1, Math.ceil(enc.length / SL.CHUNK));
        for (i = 0; i < n; i++) { SL._save(r.channel + ".json." + i, enc.substr(i * SL.CHUNK, SL.CHUNK)); }
        SL._save(r.channel + ".json.n", n);
    }
    SL._save(r.channel + ".id", r.run_id);
    SL._save(r.channel + ".status", status);
    try { app.preferences.saveToDisk(); } catch (eP) { /* nothing else to do */ }
    return r.run_id + " " + status;
};

// Run `body` inside the report protocol: begin, suppress dialogs, catch, finish. Returns the finish line.
SL.main = function (body) {
    var status = "OK", res;
    SL.begin();
    try { app.beginSuppressDialogs(); } catch (eB) { /* older AE */ }
    try {
        res = body();
        if (typeof res === "string" && res) { status = res; }
    } catch (e) {
        status = "ERROR " + e.toString() + " (line " + (e.line || "?") + ")";
        try { SL.put("error_stack", $.stack); } catch (eS) { /* no stack */ }
    } finally {
        try { app.endSuppressDialogs(false); } catch (eE) { /* older AE */ }
    }
    return SL.finish(status);
};

// ------------------------------------------------------------------ files

SL.readText = function (path) {
    var f = new File(path), s;
    if (!f.exists) { throw new Error("missing file " + path); }
    f.encoding = "UTF-8";
    f.open("r");
    s = f.read();
    f.close();
    return s;
};
SL.readJSON = function (path) { return SL.parseJSON(SL.readText(path)); };
SL.ensureFolder = function (path) {
    var d = new Folder(path);
    if (!d.exists) { d.create(); }
    return d;
};

// ------------------------------------------------------------------ the open project: never discarded

SL.projectState = function () {
    var p = app.project;
    return { file: (p && p.file) ? p.file.fsName : null, dirty: p ? !!p.dirty : false, items: p ? p.numItems : 0 };
};

// Save a project with unsaved changes to <dir>/preserved_<epoch>.aep. Returns the path, or null when clean.
SL.preserveDirty = function (dir) {
    var p = app.project, keep;
    if (!p || !p.dirty) { return null; }
    SL.ensureFolder(dir);
    keep = new File(dir + "/preserved_" + new Date().getTime() + ".aep");
    p.save(keep);
    return keep.fsName;
};

// Start a new, empty project. Unsaved work is saved to the preserve folder first; nothing is discarded.
SL.freshProject = function (preserveDir) {
    var p = app.project, kept = null;
    if (p && (p.file || p.numItems > 0)) {
        kept = SL.preserveDirty(preserveDir);
        p.close(CloseOptions.DO_NOT_SAVE_CHANGES);   // safe: any unsaved work was just saved to `kept`
    }
    app.newProject();
    return kept;
};

// Open a project (unless it's already the open one), preserving unsaved work in whatever was open.
SL.openProject = function (path, preserveDir) {
    var f = new File(path), p = app.project, kept;
    if (p && p.file && p.file.fsName === f.fsName) { return null; }
    if (!f.exists) { throw new Error("no project at " + path); }
    kept = SL.preserveDirty(preserveDir);
    app.open(f);
    return kept;
};

// ------------------------------------------------------------------ the film

SL.Film = function (spec) {
    var p = spec.project || {};
    this.spec = spec;
    this.w = spec.film.width;
    this.h = spec.film.height;
    this.par = spec.film.pixel_aspect || 1;
    this.clock = new SL.Clock(spec.film.fps_num, spec.film.fps_den);
    this.bias = (p.sample_bias_frames !== undefined ? p.sample_bias_frames : 0.1) * this.clock.fd;
    this.blur = p.motion_blur || { shutter_angle: 180, shutter_phase: -90, samples: 16, adaptive_limit: 128 };
    this.palette = {};
    this.fonts = {};
    this.fontsUsed = {};
};

SL.film = function (spec) {
    var f = new SL.Film(spec), b = spec.brand || {}, k;
    if (b.eases) { SL.defineEases(b.eases); }
    if (b.palette) {
        for (k in b.palette) { if (b.palette.hasOwnProperty(k)) { f.palette[k] = SL.color(b.palette[k]); } }
    }
    if (b.fonts) { f.fonts = b.fonts; }
    return f;
};

SL.color = function (c) {
    if (SL.isArray(c)) { return [Number(c[0]), Number(c[1]), Number(c[2])]; }
    return SL.hex(c);
};

// A palette token ("surface", "accent"...) or a literal #RRGGBB -> [r, g, b].
SL.Film.prototype.color = function (tokenOrHex) {
    if (tokenOrHex === undefined || tokenOrHex === null) { throw new Error("a colour is needed"); }
    if (SL.isArray(tokenOrHex)) { return SL.color(tokenOrHex); }
    if (this.palette.hasOwnProperty(tokenOrHex)) { return this.palette[tokenOrHex]; }
    if (String(tokenOrHex).charAt(0) === "#") { return SL.hex(tokenOrHex); }
    throw new Error("unknown colour token " + tokenOrHex + " (the brand's palette defines the tokens)");
};

// A font role ("display", "body"...) or a PostScript name -> the PostScript name AE sets on a TextDocument.
SL.Film.prototype.font = function (roleOrName) {
    var f = this.fonts[roleOrName], name;
    name = f ? (typeof f === "string" ? f : (f.postscript || f.name)) : roleOrName;
    this.fontsUsed[name] = true;
    return name;
};

SL.Film.prototype.secs = function (frame) { return this.clock.secs(frame); };

// A comp on the film clock: frames exactly, the film's size and rate, the film's motion-blur settings.
SL.comp = function (film, name, frames, opts) {
    var dur, c;
    opts = opts || {};
    dur = film.clock.secs(frames);
    c = app.project.items.addComp(name, opts.width || film.w, opts.height || film.h, film.par, dur, film.clock.fps);
    c.frameRate = film.clock.fps;
    c.duration = dur;
    if (opts.bg) { c.bgColor = film.color(opts.bg); }
    c.motionBlur = true;
    c.shutterAngle = film.blur.shutter_angle;
    c.shutterPhase = film.blur.shutter_phase;
    c.motionBlurSamplesPerFrame = film.blur.samples;
    c.motionBlurAdaptiveSampleLimit = film.blur.adaptive_limit;
    return c;
};

// Place a nested layer so it starts at `frame` of its parent comp, a tenth of a frame early (the sample bias).
SL.place = function (film, layer, frame) {
    layer.startTime = film.clock.secs(frame) - film.bias;
    return layer;
};

SL.compFrames = function (film, comp) { return Math.round(comp.duration * film.clock.fps); };

SL.findItem = function (name, type) {
    var i, it;
    for (i = 1; i <= app.project.numItems; i++) {
        it = app.project.item(i);
        if (it.name === name && (!type || it instanceof type)) { return it; }
    }
    return null;
};
SL.findComp = function (name) { return SL.findItem(name, CompItem); };
SL.folder = function (name) {
    var it = SL.findItem(name, FolderItem);
    return it || app.project.items.addFolder(name);
};
SL.importFile = function (path) {
    var f = new File(path);
    if (!f.exists) { throw new Error("missing file " + path); }
    return app.project.importFile(new ImportOptions(f));
};

// ------------------------------------------------------------------ layers

SL.solid = function (comp, film, name, color, w, h) {
    return comp.layers.addSolid(film.color(color), name, w || comp.width, h || comp.height, film.par, comp.duration);
};
SL.nullLayer = function (comp, name, pos) {
    var n = comp.layers.addNull(comp.duration);
    n.name = name;
    if (pos) { n.property("ADBE Transform Group").property("ADBE Position").setValue(pos); }
    return n;
};
SL.XF = { position: "ADBE Position", scale: "ADBE Scale", rotation: "ADBE Rotate Z", opacity: "ADBE Opacity",
          anchor: "ADBE Anchor Point", rotation_x: "ADBE Rotate X", rotation_y: "ADBE Rotate Y" };
SL.xf = function (layer, name) { return layer.property("ADBE Transform Group").property(SL.XF[name]); };
SL.span = function (film, layer, inFrame, outFrame) {
    if (inFrame !== undefined && inFrame !== null) { layer.inPoint = film.clock.secs(inFrame); }
    if (outFrame !== undefined && outFrame !== null) { layer.outPoint = film.clock.secs(outFrame); }
    return layer;
};

// A background solid at the bottom of the comp, without motion blur.
SL.background = function (comp, film, color) {
    var l = SL.solid(comp, film, "BG " + color, color);
    l.motionBlur = false;
    l.moveToEnd();
    return l;
};

// ------------------------------------------------------------------ shapes

// coords "comp": anchor = position = frame centre, so vertices are written in comp coordinates and the layer still
// scales and rotates about the centre. Without it AE puts the origin at the centre and comp-space vertices land
// half a frame off.
SL.shapeLayer = function (comp, name, coords) {
    var l = comp.layers.addShape();
    l.name = name;
    l.motionBlur = true;
    if (coords === "comp") {
        SL.xf(l, "anchor").setValue([comp.width / 2, comp.height / 2]);
        SL.xf(l, "position").setValue([comp.width / 2, comp.height / 2]);
    } else if (coords === "origin") {
        SL.xf(l, "anchor").setValue([0, 0]);
        SL.xf(l, "position").setValue([0, 0]);
    }
    return l;
};
SL.contents = function (layerOrGroup) {
    if (layerOrGroup.property("ADBE Root Vectors Group")) { return layerOrGroup.property("ADBE Root Vectors Group"); }
    return layerOrGroup.property("ADBE Vectors Group");
};
SL.group = function (parent, name) {
    var g = SL.contents(parent).addProperty("ADBE Vector Group");
    g.name = name;
    return g;
};
SL.GXF = { position: "ADBE Vector Position", anchor: "ADBE Vector Anchor", scale: "ADBE Vector Scale",
           rotation: "ADBE Vector Rotation", opacity: "ADBE Vector Group Opacity" };
SL.gxf = function (group, name) {
    return group.property("ADBE Vector Transform Group").property(SL.GXF[name]);
};
SL.rect = function (parent, w, h, r, pos) {
    var s = SL.contents(parent).addProperty("ADBE Vector Shape - Rect");
    s.property("ADBE Vector Rect Size").setValue([w, h]);
    s.property("ADBE Vector Rect Position").setValue(pos || [0, 0]);
    s.property("ADBE Vector Rect Roundness").setValue(r || 0);
    return s;
};
SL.ellipse = function (parent, w, h, pos) {
    var s = SL.contents(parent).addProperty("ADBE Vector Shape - Ellipse");
    s.property("ADBE Vector Ellipse Size").setValue([w, h]);
    s.property("ADBE Vector Ellipse Position").setValue(pos || [0, 0]);
    return s;
};
SL.shapeFrom = function (verts, closed, inT, outT) {
    var sh = new Shape(), z = [], i;
    for (i = 0; i < verts.length; i++) { z.push([0, 0]); }
    sh.vertices = verts;
    sh.inTangents = inT || z;
    sh.outTangents = outT || z;
    sh.closed = !!closed;
    return sh;
};
SL.path = function (parent, verts, closed, inT, outT) {
    var p = SL.contents(parent).addProperty("ADBE Vector Shape - Group");
    p.property("ADBE Vector Shape").setValue(SL.shapeFrom(verts, closed, inT, outT));
    return p;
};
SL.fill = function (parent, color, opacity) {
    var f = SL.contents(parent).addProperty("ADBE Vector Graphic - Fill");
    f.property("ADBE Vector Fill Color").setValue(color);
    if (opacity !== undefined) { f.property("ADBE Vector Fill Opacity").setValue(opacity); }
    return f;
};
SL.stroke = function (parent, color, width, opts) {
    var s = SL.contents(parent).addProperty("ADBE Vector Graphic - Stroke"), d;
    s.property("ADBE Vector Stroke Color").setValue(color);
    s.property("ADBE Vector Stroke Width").setValue(width);
    s.property("ADBE Vector Stroke Line Cap").setValue((opts && opts.cap) || 2);   // round
    s.property("ADBE Vector Stroke Line Join").setValue((opts && opts.join) || 2); // round
    if (opts && opts.dash) {
        d = s.property("ADBE Vector Stroke Dashes");
        d.addProperty("ADBE Vector Stroke Dash 1").setValue(opts.dash[0]);
        d.addProperty("ADBE Vector Stroke Gap 1").setValue(opts.dash[1]);
    }
    if (opts && opts.opacity !== undefined) { s.property("ADBE Vector Stroke Opacity").setValue(opts.opacity); }
    return s;
};
SL.trimPaths = function (parent) { return SL.contents(parent).addProperty("ADBE Vector Filter - Trim"); };

// ------------------------------------------------------------------ text

// Point text (or box text with opts.box [w, h]). opts: font (role or PostScript name), size, color (token or hex),
// just ("left" | "center" | "right"), tracking, leading, caps, pos [x, baselineY], name.
SL.text = function (comp, film, str, opts) {
    var l, src, td, j;
    opts = opts || {};
    l = opts.box ? comp.layers.addBoxText(opts.box, str) : comp.layers.addText(str);
    src = l.property("ADBE Text Properties").property("ADBE Text Document");
    td = src.value;
    td.resetCharStyle();
    if (opts.font) {
        try { td.font = film.font(opts.font); }
        catch (eFont) { SL.warn("font " + film.font(opts.font) + " couldn't be set: " + eFont.toString()); }
    }
    td.fontSize = opts.size || Math.round(film.h / 30);
    td.applyFill = true;
    td.fillColor = film.color(opts.color || "#000000");
    td.applyStroke = false;
    td.tracking = opts.tracking || 0;
    if (opts.leading) { td.autoLeading = false; td.leading = opts.leading; }
    j = opts.just || "left";
    td.justification = (j === "center") ? ParagraphJustification.CENTER_JUSTIFY :
        (j === "right" ? ParagraphJustification.RIGHT_JUSTIFY : ParagraphJustification.LEFT_JUSTIFY);
    if (opts.caps) { td.allCaps = true; }
    src.setValue(td);
    l.name = opts.name || ("T " + str.substr(0, 24));
    if (opts.pos) { SL.xf(l, "position").setValue(opts.pos); }
    l.motionBlur = true;
    return l;
};

// A labelled slate for an unbuilt shot: unmistakable on screen, and the audit fails it on sight.
SL.placeholder = function (comp, film, shot) {
    var t, d;
    SL.solid(comp, film, "PLACEHOLDER bg", "#101010").moveToEnd();
    t = SL.text(comp, film, shot.id + " · NOT BUILT YET", { size: Math.round(film.h / 24), color: "#FF5A3C",
        just: "center", pos: [comp.width / 2, comp.height * 0.48] });
    t.name = "PLACEHOLDER";
    d = SL.text(comp, film, String(shot.picture || "").substr(0, 110), { size: Math.round(film.h / 42),
        color: "#9A9A9A", just: "center", pos: [comp.width / 2, comp.height * 0.55] });
    d.name = "PLACEHOLDER note";
    comp.comment = "PLACEHOLDER";
};

// ------------------------------------------------------------------ logos (svg2ae data)

// A logo as native, editable shape groups: one group per SVG item, filled with its own colour or opts.color.
// data = {viewBox: [x, y, w, h], items: [{id, fill, paths: [{v, i, o, c}], bbox}]} (slate.ae.svg2ae).
// opts: width (px) or scale, pos [x, y] of the top-left, color (token or hex).
SL.logo = function (comp, film, name, data, opts) {
    var l, s, ox, oy, i, j, k, it, g, P, V, IN, OUT, cx, cy, vb = data.viewBox || [0, 0, 1, 1];
    opts = opts || {};
    s = opts.scale || (opts.width ? opts.width / (vb[2] || 1) : 1);
    ox = (opts.pos ? opts.pos[0] : 0) - vb[0] * s;
    oy = (opts.pos ? opts.pos[1] : 0) - vb[1] * s;
    l = SL.shapeLayer(comp, "LOGO " + name, "origin");
    for (i = 0; i < data.items.length; i++) {
        it = data.items[i];
        g = SL.group(l, it.id);
        for (j = 0; j < it.paths.length; j++) {
            P = it.paths[j];
            V = []; IN = []; OUT = [];
            for (k = 0; k < P.v.length; k++) {
                V.push([ox + P.v[k][0] * s, oy + P.v[k][1] * s]);
                IN.push([P.i[k][0] * s, P.i[k][1] * s]);
                OUT.push([P.o[k][0] * s, P.o[k][1] * s]);
            }
            SL.path(SL.contents(l).property(it.id), V, P.c, IN, OUT);
        }
        if (opts.color) { SL.fill(SL.contents(l).property(it.id), film.color(opts.color)); }
        else if (it.fill) { SL.fill(SL.contents(l).property(it.id), SL.hex(it.fill)); }   // fill "none": outline only
        if (it.bbox) {
            cx = ox + (it.bbox[0] + it.bbox[2]) / 2 * s;
            cy = oy + (it.bbox[1] + it.bbox[3]) / 2 * s;
            SL.gxf(SL.contents(l).property(it.id), "anchor").setValue([cx, cy]);
            SL.gxf(SL.contents(l).property(it.id), "position").setValue([cx, cy]);
        }
    }
    return l;
};

// ------------------------------------------------------------------ keyframes on the film clock

SL.isSpatial = function (prop) {
    return prop.propertyValueType === PropertyValueType.TwoD_SPATIAL ||
        prop.propertyValueType === PropertyValueType.ThreeD_SPATIAL;
};
SL.dims = function (v) { return SL.isArray(v) ? v.length : 1; };
SL.isShapeValue = function (prop) { return prop.propertyValueType === PropertyValueType.SHAPE; };
SL.isColor = function (prop) { return prop.propertyValueType === PropertyValueType.COLOR; };

// An expression that evaluates each segment with its own ease, exactly: used when a segment's ease is a sine or a
// spring, which AE's speed/influence can't express. `table` holds one normalised ease per segment. Written in the
// ES3 subset both of AE's expression engines accept, with add/mul/sub so it works for numbers and arrays alike.
SL.easeExpression = function (table) {
    return [
        "// Slate: exact eases per segment (sine and spring); the keys still hold every value",
        "var E = " + SL.toJSON(table) + ";",
        "function bz(a, b, s) { var r = 1 - s; return 3*r*r*s*a + 3*r*s*s*b + s*s*s; }",
        "function sl(a, b, s) { var r = 1 - s; return 3*r*r*a + 6*r*s*(b - a) + 3*s*s*(1 - b); }",
        "function solve(x1, x2, u) { var s = u, i, e, d, lo = 0, hi = 1, x;",
        "  for (i = 0; i < 8; i++) { e = bz(x1, x2, s) - u; if (Math.abs(e) < 1e-14) return s;",
        "    d = sl(x1, x2, s); if (Math.abs(d) < 1e-9) break; s -= e / d; if (s < 0 || s > 1) break; }",
        "  s = u; for (i = 0; i < 64; i++) { s = (lo + hi) / 2; x = bz(x1, x2, s);",
        "    if (Math.abs(x - u) < 1e-15) return s; if (x < u) lo = s; else hi = s; }",
        "  return (lo + hi) / 2; }",
        "function ev(e, u) { if (!(u > 0)) return 0; if (u >= 1) return 1;",
        "  if (e.kind == 'linear') return u; if (e.kind == 'hold') return 0;",
        "  if (e.kind == 'sine') return (1 - Math.cos(Math.PI * u)) / 2;",
        "  if (e.kind == 'spring0') { var a = e.stiffness; return (1 - (1 + a*u) * Math.exp(-a*u)) / (1 - (1 + a) * Math.exp(-a)); }",
        "  var p = e.points; if (p[0] == p[1] && p[2] == p[3]) return u; return bz(p[1], p[3], solve(p[0], p[2], u)); }",
        "if (numKeys < 2) { value; } else {",
        "  var k = 1; while (k < numKeys && key(k + 1).time <= time) k++;",
        "  if (k >= numKeys || time < key(1).time) { value; } else {",
        "    var a = key(k), b = key(k + 1), u = (time - a.time) / (b.time - a.time);",
        "    add(a.value, mul(sub(b.value, a.value), ev(E[k - 1], u)));",
        "  }",
        "}"
    ].join("\n");
};

// True when any segment's ease anticipates or overshoots (leaves 0..1 on the way).
SL._keysLeaveRange = function (keys) {
    var i;
    for (i = 0; i < keys.length - 1; i++) { if (SL.leavesRange(keys[i][2] || "linear")) { return true; } }
    return false;
};

/*  SL.anim(prop, film, keys, opts): keys = [[frame, value, easeName], ...], frames on the film clock relative to
    the comp (Python resolved them; fractions are allowed). The ease on key i shapes the segment i -> i+1: a bezier
    ease becomes AE speed + influence (editable in the graph editor), "linear" and "hold" become AE's own
    interpolation, and a sine or spring ease drives the property through SL.easeExpression. Returns prop. */
SL.anim = function (prop, film, keys, opts) {
    var i, j, idx = [], spatial = SL.isSpatial(prop), sentinel = new KeyframeEase(0, SL.MIN_INFLUENCE);
    var inE = [], outE = [], needExpr = false, exprTable = [], en, e, T, v0, v1, d2, s, o, ii, pd, a0, a1, fill, k;
    var inType, outType, d, sub;
    opts = opts || {};
    // AE's spatial speed runs along the path and can't go backwards, so a position whose ease anticipates or
    // overshoots is keyed per dimension (each a signed 1D curve, exactly as the Remotion kit interpolates it).
    if (spatial && prop.matchName === "ADBE Position" && SL._keysLeaveRange(keys)) {
        prop.dimensionsSeparated = true;
        for (d = 0; d < keys[0][1].length; d++) {
            sub = [];
            for (i = 0; i < keys.length; i++) { sub.push([keys[i][0], keys[i][1][d], keys[i][2]]); }
            SL.anim(prop.getSeparationFollower(d), film, sub, opts);
        }
        return prop;
    }
    if (spatial && SL._keysLeaveRange(keys)) {
        SL.warn(prop.name + ": an ease that leaves 0..1 can't be drawn on a spatial property here; it will stay in range");
    }
    for (i = 0; i < keys.length; i++) {
        prop.setValueAtTime(film.clock.secs(keys[i][0]), SL.keyValue(prop, keys[i][1]));
    }
    for (i = 0; i < keys.length; i++) { idx.push(prop.nearestKeyIndex(film.clock.secs(keys[i][0]))); }
    for (i = 0; i < keys.length; i++) { inE.push(null); outE.push(null); }
    for (i = 0; i < keys.length - 1; i++) {
        en = keys[i][2] || "linear";
        e = SL.ease(en);
        exprTable.push(e);
        if (e.kind === "sine" || e.kind === "spring0") { needExpr = true; }
        if (e.kind !== "bezier") { continue; }
        T = film.clock.secs(keys[i + 1][0]) - film.clock.secs(keys[i][0]);
        v0 = keys[i][1];
        v1 = keys[i + 1][1];
        o = [];
        ii = [];
        if (spatial) {
            d2 = 0;
            for (j = 0; j < v0.length; j++) { d2 += (v1[j] - v0[j]) * (v1[j] - v0[j]); }
            s = SL.segEase(e.points, Math.sqrt(d2) / T);
            o.push(new KeyframeEase(Math.abs(s.out[0]), s.out[1]));
            ii.push(new KeyframeEase(Math.abs(s.inn[0]), s.inn[1]));
        } else if (SL.isShapeValue(prop) || SL.isColor(prop)) {
            // Shape paths and colours interpolate a normalised 0..1 progress, so the average speed is 1/T
            // (calibrated by the film pipeline's probe_path_ease.jsx to three decimals).
            s = SL.segEase(e.points, 1 / T);
            o.push(new KeyframeEase(s.out[0], s.out[1]));
            ii.push(new KeyframeEase(s.inn[0], s.inn[1]));
        } else {
            pd = SL.isArray(prop.value) ? prop.value.length : 1;   // Scale is [x, y, z] inside AE
            for (j = 0; j < pd; j++) {
                if (SL.isArray(v0)) {
                    a0 = (j < v0.length) ? v0[j] : v0[v0.length - 1];
                    a1 = (j < v1.length) ? v1[j] : v1[v1.length - 1];
                    if (j >= v0.length) { a1 = a0; }   // dimensions the key doesn't move stay still
                } else { a0 = v0; a1 = v1; }
                s = SL.segEase(e.points, (a1 - a0) / T);
                o.push(new KeyframeEase(s.out[0], s.out[1]));
                ii.push(new KeyframeEase(s.inn[0], s.inn[1]));
            }
        }
        outE[i] = o;
        inE[i + 1] = ii;
    }
    pd = spatial || SL.isShapeValue(prop) || SL.isColor(prop) ? 1 : (SL.isArray(prop.value) ? prop.value.length : 1);
    fill = [];
    for (j = 0; j < pd; j++) { fill.push(sentinel); }
    for (i = 0; i < keys.length; i++) {
        k = idx[i];
        inType = SL.interpType(i > 0 ? SL.ease(keys[i - 1][2] || "linear").kind : "bezier");
        outType = SL.interpType(i < keys.length - 1 ? SL.ease(keys[i][2] || "linear").kind : "bezier");
        prop.setInterpolationTypeAtKey(k, inType, outType);
        if (inType !== KeyframeInterpolationType.BEZIER && outType !== KeyframeInterpolationType.BEZIER) { continue; }
        if (spatial) {
            try { prop.setSpatialAutoBezierAtKey(k, false); prop.setSpatialContinuousAtKey(k, false); }
            catch (eS) { /* not every spatial property allows it */ }
        }
        try { prop.setTemporalEaseAtKey(k, inE[i] || fill, outE[i] || fill); }
        catch (eE) { SL.warn("ease on " + prop.name + ": " + eE.toString()); }
        if (spatial) {
            try { prop.setSpatialTangentsAtKey(k, SL.zeros(keys[i][1].length), SL.zeros(keys[i][1].length)); }
            catch (eT) { /* straight path is the default anyway */ }
        }
    }
    if (needExpr) { prop.expression = SL.easeExpression(exprTable); }
    return prop;
};
SL.zeros = function (n) { var z = [], i; for (i = 0; i < n; i++) { z.push(0); } return z; };
// AE's interpolation for an ease kind: linear and hold are AE's own; everything else is a bezier segment.
SL.interpType = function (kind) {
    if (kind === "linear") { return KeyframeInterpolationType.LINEAR; }
    if (kind === "hold") { return KeyframeInterpolationType.HOLD; }
    return KeyframeInterpolationType.BEZIER;
};

// Values for a property: {v, i, o, c} path data becomes a Shape; colour tokens become [r, g, b].
SL.keyValue = function (prop, v) {
    if (v && typeof v === "object" && !SL.isArray(v) && v.v) { return SL.shapeFrom(v.v, v.c, v.i, v.o); }
    return v;
};

// Whether a position moves: two keys or more on it, or on one of its separated dimensions.
SL.positionKeyed = function (pos) {
    var d;
    if (!pos) { return false; }
    if (pos.numKeys > 1) { return true; }
    try {
        if (pos.dimensionsSeparated) {
            for (d = 0; d < 2; d++) { if (pos.getSeparationFollower(d).numKeys > 1) { return true; } }
        }
    } catch (e) { return false; }
    return false;
};

// The temporal ease AE stored on a key, read back: {inn: [speed, influence], out: [speed, influence]} (first dim).
SL.readEase = function (prop, k) {
    var a = prop.keyInTemporalEase(k)[0], b = prop.keyOutTemporalEase(k)[0];
    return { inn: [a.speed, a.influence], out: [b.speed, b.influence] };
};

// ------------------------------------------------------------------ markers

SL.marker = function (markerProp, film, frame, label, opts) {
    var mv = new MarkerValue(label);
    if (opts && opts.comment) { mv.comment = opts.comment; }
    markerProp.setValueAtTime(film.clock.secs(frame), mv);
    return mv;
};

// ------------------------------------------------------------------ data-driven scenes (AE-built acts)

// The property a scene animates: a transform ("position", "scale", "rotation", "opacity", "anchor") or, on a shape
// layer, its first group's "size", "roundness", "fill_color", "fill_opacity", "stroke_width", "stroke_color",
// "trim_start", "trim_end", "trim_offset" or "path". Re-resolved from the layer each time (AE invalidates
// sibling references when properties are added).
SL.sceneProp = function (layer, name) {
    var root, g, c, n, i, p, m;
    if (SL.XF[name]) { return SL.xf(layer, name); }
    root = layer.property("ADBE Root Vectors Group");
    if (!root || root.numProperties < 1) { throw new Error("layer " + layer.name + " has no " + name); }
    g = root.property(1);
    c = g.property("ADBE Vectors Group");
    m = { size: ["ADBE Vector Shape - Rect", "ADBE Vector Rect Size", "ADBE Vector Shape - Ellipse", "ADBE Vector Ellipse Size"],
          roundness: ["ADBE Vector Shape - Rect", "ADBE Vector Rect Roundness"],
          fill_color: ["ADBE Vector Graphic - Fill", "ADBE Vector Fill Color"],
          fill_opacity: ["ADBE Vector Graphic - Fill", "ADBE Vector Fill Opacity"],
          stroke_width: ["ADBE Vector Graphic - Stroke", "ADBE Vector Stroke Width"],
          stroke_color: ["ADBE Vector Graphic - Stroke", "ADBE Vector Stroke Color"],
          trim_start: ["ADBE Vector Filter - Trim", "ADBE Vector Trim Start"],
          trim_end: ["ADBE Vector Filter - Trim", "ADBE Vector Trim End"],
          trim_offset: ["ADBE Vector Filter - Trim", "ADBE Vector Trim Offset"],
          path: ["ADBE Vector Shape - Group", "ADBE Vector Shape"] }[name];
    if (!m) { throw new Error("unknown scene property " + name); }
    for (i = 1; i <= c.numProperties; i++) {
        p = c.property(i);
        for (n = 0; n + 1 < m.length; n += 2) {
            if (p.matchName === m[n]) { return p.property(m[n + 1]); }
        }
    }
    throw new Error("layer " + layer.name + " has no " + name);
};

// Build one scene layer. L: {id, type, name, color, fill, stroke, stroke_width, size, roundness, verts, closed,
// text, font, font_size, just, logo, width, pos, file, in_frame, out_frame, motion_blur, transform {...}}.
SL.sceneLayer = function (comp, film, L, ctx) {
    var l, g, t = L.type, k;
    if (t === "solid") {
        l = SL.solid(comp, film, L.name || L.id, L.color || L.fill);
    } else if (t === "null") {
        l = SL.nullLayer(comp, L.name || L.id);
    } else if (t === "rect" || t === "ellipse" || t === "path") {
        l = SL.shapeLayer(comp, L.name || L.id, "comp");
        g = SL.group(l, L.id);
        if (t === "rect") { SL.rect(SL.contents(l).property(L.id), L.size[0], L.size[1], L.roundness || 0); }
        if (t === "ellipse") { SL.ellipse(SL.contents(l).property(L.id), L.size[0], L.size[1]); }
        if (t === "path") { SL.path(SL.contents(l).property(L.id), L.verts, L.closed, L.in_tangents, L.out_tangents); }
        if (L.stroke) {
            SL.stroke(SL.contents(l).property(L.id), film.color(L.stroke), L.stroke_width || 2);
        }
        if (L.fill) { SL.fill(SL.contents(l).property(L.id), film.color(L.fill)); }
        if (L.trim) { SL.trimPaths(SL.contents(l).property(L.id)); }
        SL.gxf(SL.contents(l).property(L.id), "position").setValue([comp.width / 2, comp.height / 2]);
    } else if (t === "text") {
        l = SL.text(comp, film, L.text || "", { font: L.font, size: L.font_size, color: L.color || L.fill,
            just: L.just, tracking: L.tracking, caps: L.caps, name: L.name || L.id });
    } else if (t === "logo") {
        if (!ctx.logos || !ctx.logos[L.logo]) { throw new Error("no logo called " + L.logo + " in the brand"); }
        l = SL.logo(comp, film, L.logo, ctx.logos[L.logo], { width: L.width, pos: L.pos, color: L.color });
    } else if (t === "footage") {
        l = comp.layers.add(SL.importFile(L.file));
        l.motionBlur = false;
    } else {
        throw new Error("unknown scene layer type " + t);
    }
    l.name = L.name || L.id;
    if (L.motion_blur === false) { l.motionBlur = false; }
    if (L.transform) {
        for (k in L.transform) {
            if (L.transform.hasOwnProperty(k) && SL.XF[k]) { SL.xf(l, k).setValue(L.transform[k]); }
        }
    }
    SL.span(film, l, L.in_frame, L.out_frame);
    return l;
};

// Build a scene into a comp: layers in order (the first ends up at the bottom), parents, then every animation.
// scene = {layers: [...], anims: [{layer, prop, keys: [[frame, value, ease], ...], id}]}.
SL.buildScene = function (comp, film, scene, ctx) {
    var made = {}, i, L, A, prop, n = 0;
    ctx = ctx || {};
    for (i = 0; i < scene.layers.length; i++) {
        L = scene.layers[i];
        made[L.id] = SL.sceneLayer(comp, film, L, ctx);
    }
    for (i = 0; i < scene.layers.length; i++) {
        L = scene.layers[i];
        if (L.parent) {
            if (!made[L.parent]) { throw new Error("layer " + L.id + " has an unknown parent " + L.parent); }
            made[L.id].parent = made[L.parent];
        }
    }
    for (i = 0; i < (scene.anims || []).length; i++) {
        A = scene.anims[i];
        if (!made[A.layer]) { throw new Error("animation " + (A.id || i) + " targets an unknown layer " + A.layer); }
        prop = SL.sceneProp(made[A.layer], A.prop);
        SL.anim(prop, film, A.keys);
        n++;
    }
    return { layers: scene.layers.length, anims: n, made: made };
};

SL.keysOf = function (o) {
    var out = [], k;
    for (k in o) { if (o.hasOwnProperty(k)) { out.push(k); } }
    return out;
};

// ------------------------------------------------------------------ the brand board

// One comp that carries the brand into the project: the palette as labelled swatches, a line of type per font role
// (set by name, so a missing font shows up in the audit), and every logo as native shape groups.
SL.brandBoard = function (film, spec, folder) {
    var b = spec.brand || {}, c, keys, i, cols = 6, cellW, sw, sh, x, y, l, t, roles, logos, n = 0;
    c = SL.comp(film, spec.names.brand_board, Math.max(1, film.clock.frames(1)), { bg: "#7F7F7F" });
    c.parentFolder = folder;
    keys = SL.keysOf(film.palette);
    cellW = c.width / cols;
    sw = cellW * 0.8;
    sh = c.height * 0.1;
    for (i = 0; i < keys.length; i++) {
        x = cellW * (i % cols) + cellW / 2;
        y = c.height * 0.1 + Math.floor(i / cols) * sh * 1.6;
        l = SL.shapeLayer(c, "swatch " + keys[i], "origin");
        SL.group(l, "swatch");
        SL.rect(SL.contents(l).property("swatch"), sw, sh, 0, [x, y]);
        SL.fill(SL.contents(l).property("swatch"), film.palette[keys[i]]);
        t = SL.text(c, film, keys[i], { size: Math.round(c.height / 60), color: "#FFFFFF",
                                        just: "center", pos: [x, y + sh * 0.75], name: "label " + keys[i] });
        t.motionBlur = false;
    }
    roles = SL.keysOf(film.fonts);
    for (i = 0; i < roles.length; i++) {
        t = SL.text(c, film, roles[i] + " · " + film.font(roles[i]), { font: roles[i],
            size: Math.round(c.height / 24), color: "#FFFFFF", pos: [c.width * 0.06, c.height * (0.55 + i * 0.07)],
            name: "type " + roles[i] });
        t.motionBlur = false;
    }
    logos = b.logos || {};
    keys = SL.keysOf(logos);
    for (i = 0; i < keys.length; i++) {
        SL.logo(c, film, keys[i], logos[keys[i]], { width: c.width * 0.18,
            pos: [c.width * (0.06 + 0.22 * (i % 4)), c.height * 0.8] });
        n++;
    }
    return { palette: SL.keysOf(film.palette).length, fonts: roles.length, logos: n };
};

// ------------------------------------------------------------------ the masters queue

// Fill the render queue with one item per chunk of the master comp, each with two output modules, so one aerender
// pass writes both masters' frames. Stock output modules' codecs can't be set by script and Slate never adds
// templates to the person's preferences, so the ProRes 422 HQ master is conformed from the alpha chunks by ffmpeg.
// m = {chunks: [{index, start, end, frames}], om_primary, om_alpha, rs, dir, primary_name, alpha_name}.
// Every item's span is read back in frames, so a rounding slip shows before anything renders.
SL.queueMasters = function (project, master, film, m) {
    var RQ = project.renderQueue.items, out = [], k, c, it, om1, om2, gs, gn;
    while (RQ.length > 0) { RQ[1].remove(); }
    SL.ensureFolder(m.dir);
    for (k = 0; k < m.chunks.length; k++) {
        c = m.chunks[k];
        it = RQ.add(master);
        it.applyTemplate(m.rs);
        it.timeSpanStart = film.clock.secs(c.start);
        it.timeSpanDuration = film.clock.secs(c.frames);
        om1 = it.outputModule(1);
        om1.applyTemplate(m.om_primary);
        om1.file = new File(m.dir + "/" + m.primary_name.replace("{k}", String(c.index)));
        om2 = it.outputModules.add();
        om2.applyTemplate(m.om_alpha);
        om2.file = new File(m.dir + "/" + m.alpha_name.replace("{k}", String(c.index)));
        gs = Math.round(it.timeSpanStart * film.clock.fps);
        gn = Math.round(it.timeSpanDuration * film.clock.fps);
        out.push({ rqindex: k + 1, chunk: c.index, start: gs, frames: gn, want_start: c.start, want_frames: c.frames,
                   match: gs === c.start && gn === c.frames, outputs: it.numOutputModules });
    }
    return out;
};

// ------------------------------------------------------------------ motion traces

// Properties whose values a motion trace can hold: numbers, number arrays, colours, and shapes (first vertex).
SL.traceable = function (q) {
    var t = q.propertyValueType;
    return t === PropertyValueType.OneD || t === PropertyValueType.TwoD || t === PropertyValueType.ThreeD ||
        t === PropertyValueType.TwoD_SPATIAL || t === PropertyValueType.ThreeD_SPATIAL ||
        t === PropertyValueType.COLOR || t === PropertyValueType.SHAPE;
};

// Sample every animated property (two keys or more) of every layer in a comp, frame by frame, as AE itself
// evaluates it: [{layer, prop, match_name, frame_in, frame_out, samples: [[frame, value]]}]. Shapes report their
// first vertex. Works for any AE-built act, whoever built it (Slate's scenes or the AE bridge).
SL.sampleComp = function (comp, film, opts) {
    var out = [], i, L, stack, pr, j, q, f, f0, f1, samples, v, maxN;
    opts = opts || {};
    maxN = opts.max_samples || 2000;
    for (i = 1; i <= comp.numLayers; i++) {
        L = comp.layer(i);
        stack = [L];
        while (stack.length) {
            pr = stack.pop();
            for (j = 1; j <= pr.numProperties; j++) {
                q = pr.property(j);
                if (q.propertyType === PropertyType.PROPERTY) {
                    if (q.numKeys && q.numKeys > 1 && SL.traceable(q)) {
                        f0 = Math.round(q.keyTime(1) * film.clock.fps);
                        f1 = Math.round(q.keyTime(q.numKeys) * film.clock.fps);
                        samples = [];
                        for (f = f0; f <= f1 && samples.length < maxN; f++) {
                            v = q.valueAtTime(film.clock.secs(f), false);
                            if (v && v.vertices) { v = v.vertices.length ? v.vertices[0] : null; }
                            samples.push([f, v]);
                        }
                        out.push({ layer: L.name, prop: q.name, match_name: q.matchName, frame_in: f0, frame_out: f1,
                                   samples: samples });
                    }
                } else {
                    stack.push(q);
                }
            }
        }
    }
    return out;
};
