/*  slate_math.jsx: Slate's ExtendScript maths (ES3). No After Effects objects live in this file, so the same file
    runs inside AE (through #include) and in node, where engine/ae/tests/run_vectors.mjs checks it against the
    cross-language vectors that pytest and vitest also check (tests/vectors/eases.json).

    What's here, and why it matches the Remotion kit:
      - Eases come from data (the brand pack's motion.eases), never from names baked into code. The kit's kinds:
        "bezier" (CSS cubic-bezier control points), "sine" ((1 - cos πu) / 2), "linear" and "spring0" (a critically
        damped spring normalised to land on 1). Endpoints are exact: every ease is 0 at u <= 0 and 1 at u >= 1.
      - T90 is the fraction of an ease's duration at which it covers 90% of its travel (the timeline's "land").
      - aeEase/segEase turn a bezier ease into After Effects' temporal ease (speed + influence per key), the
        conversion the film's smoke test proved in pixels (max error 4.2 px at the fastest, blurred frame; under
        1.5 px from frame 6) and probe_path_ease.jsx calibrated for shape paths (normalised progress, speed = 1/T).
      - monoSpline: the kit's monotone cubic through timed keys, for camera drifts.
      - A Clock that turns the film's resolved frames into seconds. Python resolves every frame; this only converts.

    ES3 rules: var only, no JSON object, no Array.indexOf/forEach/map/filter, no String.trim, no Object.keys,
    no trailing commas, no getters or setters. Arrays are recognised with Object.prototype.toString, which also works
    for arrays handed in from another script context.
*/

var SL = SL || {};

SL.isArray = function (v) { return Object.prototype.toString.call(v) === "[object Array]"; };

// ------------------------------------------------------------------ JSON (ES3 has none)

// Parse JSON that Slate itself wrote. The RFC 4627 check (only JSON's own characters may remain once strings are
// removed) guards the eval; ExtendScript's regex engine can overflow on very long strings, and then the text is
// still Slate's own file, so it is evaluated as the film pipeline always did.
SL.parseJSON = function (text) {
    var t = String(text);
    var suspicious = false;
    try {
        suspicious = /[^,:{}\[\]0-9.\-+Eaeflnr-u \n\r\t]/.test(t.replace(/"(\\.|[^"\\])*"/g, ""));
    } catch (eRegex) { suspicious = false; }
    if (suspicious) { throw new Error("not JSON: refusing to evaluate it"); }
    return eval("(" + t + ")");
};

SL._quote = function (s) {
    var out = "\"", i, c, code, hex;
    for (i = 0; i < s.length; i++) {
        c = s.charAt(i);
        code = s.charCodeAt(i);
        if (c === "\"") { out += "\\\""; }
        else if (c === "\\") { out += "\\\\"; }
        else if (c === "\n") { out += "\\n"; }
        else if (c === "\r") { out += "\\r"; }
        else if (c === "\t") { out += "\\t"; }
        else if (code < 32 || code === 0x2028 || code === 0x2029) {
            hex = code.toString(16);
            while (hex.length < 4) { hex = "0" + hex; }
            out += "\\u" + hex;
        } else { out += c; }
    }
    return out + "\"";
};

// Serialise plain data (objects, arrays, strings, numbers, booleans, null). Functions and undefined are left out.
SL.toJSON = function (v) {
    var t = typeof v, parts, i, k;
    if (v === null || v === undefined) { return "null"; }
    if (t === "number") { return isFinite(v) ? String(v) : "null"; }
    if (t === "boolean") { return v ? "true" : "false"; }
    if (t === "string") { return SL._quote(v); }
    if (t === "function") { return "null"; }
    if (SL.isArray(v)) {
        parts = [];
        for (i = 0; i < v.length; i++) { parts.push(SL.toJSON(v[i])); }
        return "[" + parts.join(",") + "]";
    }
    parts = [];
    for (k in v) {
        if (v.hasOwnProperty(k) && typeof v[k] !== "function" && v[k] !== undefined) {
            parts.push(SL._quote(k) + ":" + SL.toJSON(v[k]));
        }
    }
    return "{" + parts.join(",") + "}";
};

// ------------------------------------------------------------------ eases (the motion law, step for step)
//
// A port of the Remotion kit's law (engine/remotion/kit, mirrored in Python as slate.motion.eases): the same parser,
// the same solver (Newton from s = u, then bisection), the same T90 scan, the same After Effects handle rule. The
// shared vectors (tests/vectors/eases.json at 1e-12, t90.json at 1e-9) hold all three runners together.

SL.EASES = {};
SL.SPRING0_STIFFNESS = 7;

SL.clamp01 = function (u) { return u < 0 ? 0 : (u > 1 ? 1 : u); };

SL._bez = function (a, b, s) { var r = 1 - s; return 3 * r * r * s * a + 3 * r * s * s * b + s * s * s; };
SL._bezSlope = function (a, b, s) { var r = 1 - s; return 3 * r * r * a + 6 * r * s * (b - a) + 3 * s * s * (1 - b); };

// The curve parameter s where x(s) = u: Newton from s = u, then bisection when Newton stalls.
SL.solveBezierX = function (x1, x2, u) {
    var s = u, i, err, d, lo = 0, hi = 1, x;
    for (i = 0; i < 8; i++) {
        err = SL._bez(x1, x2, s) - u;
        if (Math.abs(err) < 1e-14) { return s; }
        d = SL._bezSlope(x1, x2, s);
        if (Math.abs(d) < 1e-9) { break; }
        s -= err / d;
        if (s < 0 || s > 1) { break; }
    }
    s = u;
    for (i = 0; i < 64; i++) {
        s = (lo + hi) / 2;
        x = SL._bez(x1, x2, s);
        if (Math.abs(x - u) < 1e-15) { return s; }
        if (x < u) { lo = s; } else { hi = s; }
    }
    return (lo + hi) / 2;
};

// A CSS cubic-bezier ease at progress x, with exact endpoints.
SL.bezierY = function (p, x) {
    if (!(x > 0)) { return 0; }
    if (x >= 1) { return 1; }
    if (p[0] === p[1] && p[2] === p[3]) { return x; }
    return SL._bez(p[1], p[3], SL.solveBezierX(p[0], p[2], x));
};

SL._bezierDef = function (values) {
    var v = [], i;
    if (!values || values.length !== 4) { throw new Error("a cubic-bezier ease has four numbers"); }
    for (i = 0; i < 4; i++) {
        v.push(Number(values[i]));
        if (!isFinite(v[i])) { throw new Error("a cubic-bezier ease has four finite numbers"); }
    }
    if (!(v[0] >= 0 && v[0] <= 1 && v[2] >= 0 && v[2] <= 1)) {
        throw new Error("cubic-bezier x values must lie in [0, 1] (otherwise it isn't a function of time)");
    }
    return { kind: "bezier", points: v };
};

SL._strip = function (s) { return String(s).replace(/^\s+/, "").replace(/\s+$/, ""); };

// Read an ease the way the kit does: [x1, y1, x2, y2], "cubic-bezier(...)", {bezier: [...]}, {cubic_bezier: ...},
// "linear", "sine" (also "sine-in-out", "sway"), "spring0" (also "spring"), {kind: "spring0", stiffness}. "hold" is
// After Effects' own interpolation for a key, not a curve. Throws on anything else.
SL.normEase = function (raw) {
    var s, m, parts, kind, k;
    if (raw === null || raw === undefined) { throw new Error("an ease needs a definition"); }
    if (typeof raw === "string") {
        s = SL._strip(raw).toLowerCase();
        if (s === "linear" || s === "hold") { return { kind: s }; }
        if (s === "sine" || s === "sine-in-out" || s === "sway") { return { kind: "sine" }; }
        if (s === "spring0" || s === "spring") { return { kind: "spring0", stiffness: SL.SPRING0_STIFFNESS }; }
        m = /^cubic-bezier\(([^)]*)\)$/.exec(s);
        if (m) {
            parts = m[1].split(",");
            for (k = 0; k < parts.length; k++) { parts[k] = SL._strip(parts[k]); }
            return SL._bezierDef(parts);
        }
        throw new Error("unknown ease " + raw);
    }
    if (SL.isArray(raw)) { return SL._bezierDef(raw); }
    if (raw.kind === "bezier" && raw.points && !raw.bezier) { return SL._bezierDef(raw.points); }
    if (raw.bezier !== undefined && raw.bezier !== null) { return SL.normEase(raw.bezier); }
    if (raw.cubic_bezier !== undefined && raw.cubic_bezier !== null) {
        return SL.normEase(SL.isArray(raw.cubic_bezier) ? raw.cubic_bezier : "cubic-bezier(" + raw.cubic_bezier + ")");
    }
    kind = String(raw.kind || raw.type || "").toLowerCase();
    if (kind === "bezier" || kind === "cubic-bezier") { return SL.normEase(raw.points || raw.values || raw.value); }
    if (kind === "spring0" || kind === "spring") {
        k = Number(raw.stiffness === undefined ? SL.SPRING0_STIFFNESS : raw.stiffness);
        return { kind: "spring0", stiffness: (isFinite(k) && k > 0) ? k : SL.SPRING0_STIFFNESS };
    }
    if (kind) { return SL.normEase(kind); }
    throw new Error("unknown ease definition");
};

// Register a table of named eases {name: definition}: the brand pack's motion.eases.
SL.defineEases = function (table) {
    var k;
    for (k in table) {
        if (table.hasOwnProperty(k)) { SL.EASES[k] = SL.normEase(table[k]); }
    }
    return SL.EASES;
};

// A name from the brand's eases (or a built-in: linear, hold, sine, spring0), or a definition.
SL.ease = function (nameOrDef) {
    if (typeof nameOrDef === "string") {
        if (SL.EASES.hasOwnProperty(nameOrDef)) { return SL.EASES[nameOrDef]; }
        try { return SL.normEase(nameOrDef); }
        catch (e) { throw new Error("unknown ease " + nameOrDef + " (the brand's motion.eases defines the names)"); }
    }
    return SL.normEase(nameOrDef);
};

SL.spring0At = function (u, stiffness) {
    var a = stiffness;
    if (!(u > 0)) { return 0; }
    if (u >= 1) { return 1; }
    return (1 - (1 + a * u) * Math.exp(-a * u)) / (1 - (1 + a) * Math.exp(-a));
};

// Eased progress at u: exactly 0 at or before 0 (and for NaN), exactly 1 at or after 1.
SL.easeValue = function (nameOrDef, u) {
    var e;
    if (!(u > 0)) { return 0; }
    if (u >= 1) { return 1; }
    e = SL.ease(nameOrDef);
    if (e.kind === "linear") { return u; }
    if (e.kind === "hold") { return 0; }
    if (e.kind === "sine") { return (1 - Math.cos(Math.PI * u)) / 2; }
    if (e.kind === "spring0") { return SL.spring0At(u, e.stiffness); }
    if (e.kind === "bezier") { return SL.bezierY(e.points, u); }
    throw new Error("unknown ease kind " + e.kind);
};

SL._bisectLevel = function (e, lo, hi, level) {
    var i, mid;
    for (i = 0; i < 80; i++) {
        mid = (lo + hi) / 2;
        if (SL.easeValue(e, mid) >= level) { hi = mid; } else { lo = mid; }
    }
    return (lo + hi) / 2;
};

// The fraction of a move's duration at which an ease first covers `level` of its travel (scan, then bisect).
SL.tAt = function (nameOrDef, level) {
    var e = SL.ease(nameOrDef), n = 4096, i, u, prev = 0;
    if (level === undefined) { level = 0.9; }
    if (level <= 0) { return 0; }
    if (level >= 1) {
        for (i = 1; i <= n; i++) {
            if (SL.easeValue(e, i / n) >= 1) { return SL._bisectLevel(e, (i - 1) / n, i / n, 1); }
        }
        return 1;
    }
    for (i = 1; i <= n; i++) {
        u = i / n;
        if (SL.easeValue(e, u) >= level) { return SL._bisectLevel(e, prev, u, level); }
        prev = u;
    }
    return 1;
};
SL.timeToReach = SL.tAt;

// T90: where an ease reaches 90% of its travel. "Land" means 90% of travel everywhere in Slate.
SL.t90 = function (nameOrDef) { return SL.tAt(nameOrDef, 0.9); };

// The start time that lands a move (90% of its travel) on `land` seconds, for a move lasting `dur` seconds.
SL.startFor = function (land, dur, nameOrDef) { return land - SL.t90(nameOrDef) * dur; };

// ------------------------------------------------------------------ After Effects temporal ease

SL.MIN_INFLUENCE = 0.1;  // percent: AE's floor

// A cubic-bezier as AE keyframe eases: {out: {influence (%), speed (x the segment's average speed)}, in: {...}}.
// AE draws a segment as a cubic in (time, value) whose control point sits influence% of the way along in time and
// speed x that time up in value, so influence = x1 and speed = y1 / x1 reproduce the CSS curve exactly. A handle
// collapsed onto its keyframe takes AE's smallest influence and the curve's real end tangent, which points at the
// other control point; a vertical handle takes the smallest influence and the steepest speed that reaches it.
SL.aeEase = function (points) {
    var x1 = Number(points[0]), y1 = Number(points[1]), x2 = Number(points[2]), y2 = Number(points[3]);
    function handle(dx, dy, odx, ody) {
        if (dx > 0) { return { influence: Math.max(dx * 100, SL.MIN_INFLUENCE), speed: dy / dx }; }
        if (dy === 0) { return { influence: SL.MIN_INFLUENCE, speed: odx > 0 ? ody / odx : 1 }; }
        return { influence: SL.MIN_INFLUENCE, speed: dy / (SL.MIN_INFLUENCE / 100) };
    }
    return { out: handle(x1, y1, x2, y2), "in": handle(1 - x2, 1 - y2, 1 - x1, 1 - y1) };
};

// The same for one segment with a signed average velocity dv (value units per second): AE's speeds are in value
// units per second. Returns {out: [speed, influence%], inn: [speed, influence%]}.
SL.segEase = function (points, dv) {
    var a = SL.aeEase(points);
    return { out: [a.out.speed * dv, Math.min(100, a.out.influence)],
             inn: [a["in"].speed * dv, Math.min(100, a["in"].influence)] };
};

// Whether an ease leaves 0..1 on the way (anticipation or overshoot). AE's spatial speed can't go backwards, so such
// a move on a position is keyed per dimension (see SL.anim).
SL.leavesRange = function (nameOrDef) {
    var e = SL.ease(nameOrDef), p;
    if (e.kind !== "bezier") { return false; }
    p = e.points;
    return p[1] < 0 || p[1] > 1 || p[3] < 0 || p[3] > 1;
};

// How After Effects evaluates one eased segment, normalised to 0..1 in time and value: the check that segEase's
// speeds and influences give back the ease they came from (the vectors runner uses it; AE does this itself).
SL.aeSegmentValue = function (seg, dv, u) {
    var p1x = seg.out[1] / 100, p2x = 1 - seg.inn[1] / 100;
    var p1y = dv !== 0 ? seg.out[0] * p1x / dv : 0;
    var p2y = dv !== 0 ? 1 - seg.inn[0] * (seg.inn[1] / 100) / dv : 1;
    if (!(u > 0)) { return 0; }
    if (u >= 1) { return 1; }
    return SL._bez(p1y, p2y, SL.solveBezierX(p1x, p2x, u));
};

// ------------------------------------------------------------------ monotone splines (camera drifts)

SL._sortedKeys = function (keys) {
    var ks = [], i, out = [];
    for (i = 0; i < keys.length; i++) {
        if (isFinite(Number(keys[i][0]))) { ks.push([Number(keys[i][0]), Number(keys[i][1])]); }
    }
    ks.sort(function (a, b) { return a[0] - b[0]; });
    for (i = 0; i < ks.length; i++) {
        if (out.length && out[out.length - 1][0] === ks[i][0]) { out[out.length - 1] = ks[i]; }  // the later one wins
        else { out.push(ks[i]); }
    }
    return out;
};

// A monotone cubic through timed keys [t, v] (Fritsch-Carlson tangents, Butland's mean): it never leaves the range
// of the two keys it's between, rests at the first and last key, and is exact at every key.
SL.monoSpline = function (t, keys) {
    var k = SL._sortedKeys(keys), n = k.length, ts = [], vs = [], h = [], d = [], m = [], i, w1, w2, hh, u;
    var h00, h10, h01, h11;
    if (!n) { return 0; }
    if (n === 1 || t <= k[0][0]) { return k[0][1]; }
    if (t >= k[n - 1][0]) { return k[n - 1][1]; }
    for (i = 0; i < n; i++) { ts.push(k[i][0]); vs.push(k[i][1]); m.push(0); }
    for (i = 0; i < n - 1; i++) { h.push(ts[i + 1] - ts[i]); d.push((vs[i + 1] - vs[i]) / h[i]); }
    for (i = 1; i < n - 1; i++) {
        if (d[i - 1] * d[i] <= 0) { continue; }
        w1 = 2 * h[i] + h[i - 1];
        w2 = h[i] + 2 * h[i - 1];
        m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i]);
    }
    i = 0;
    while (i < n - 2 && t >= ts[i + 1]) { i++; }
    hh = ts[i + 1] - ts[i];
    u = (t - ts[i]) / hh;
    h00 = 2 * u * u * u - 3 * u * u + 1;
    h10 = u * u * u - 2 * u * u + u;
    h01 = -2 * u * u * u + 3 * u * u;
    h11 = u * u * u - u * u;
    return h00 * vs[i] + h10 * hh * m[i] + h01 * vs[i + 1] + h11 * hh * m[i + 1];
};

// ------------------------------------------------------------------ the film clock

// Frames -> seconds on the film's rational frame rate (fps_num / fps_den from film.json). Python has already
// resolved every frame; this only converts, so AE, Remotion and the web engine share one clock.
SL.Clock = function (fpsNum, fpsDen) {
    this.num = Number(fpsNum);
    this.den = Number(fpsDen);
    this.fps = this.num / this.den;
    this.fd = this.den / this.num;
};
SL.Clock.prototype.secs = function (frame) { return frame * this.den / this.num; };
SL.Clock.prototype.frameOf = function (sec) { return Math.round(sec * this.fps); };
SL.Clock.prototype.snap = function (sec) { return this.secs(this.frameOf(sec)); };
SL.Clock.prototype.frames = function (sec) { return Math.round(sec * this.fps); };

// ------------------------------------------------------------------ geometry

// Catmull-Rom tangents for a polyline, so few vertices still make a clean curve (ends at rest).
SL.smooth = function (pts) {
    var inT = [], outT = [], i, n = pts.length, p0, p2, tx, ty;
    for (i = 0; i < n; i++) {
        p0 = pts[Math.max(0, i - 1)];
        p2 = pts[Math.min(n - 1, i + 1)];
        tx = (p2[0] - p0[0]) / 6;
        ty = (p2[1] - p0[1]) / 6;
        inT.push([-tx, -ty]);
        outT.push([tx, ty]);
    }
    if (n > 0) { inT[0] = [0, 0]; outT[n - 1] = [0, 0]; }
    return { inT: inT, outT: outT };
};

// Resample a polyline to exactly n vertices by arc length, first vertex left-most (morph targets must agree).
SL.resample = function (pts, n) {
    var d = [0], i, total = 0, out = [], k = 1, target, seg, u;
    for (i = 1; i < pts.length; i++) {
        total += Math.sqrt(Math.pow(pts[i][0] - pts[i - 1][0], 2) + Math.pow(pts[i][1] - pts[i - 1][1], 2));
        d.push(total);
    }
    for (i = 0; i < n; i++) {
        target = n > 1 ? total * i / (n - 1) : 0;
        while (k < pts.length - 1 && d[k] < target) { k++; }
        seg = d[k] - d[k - 1];
        u = seg > 0 ? (target - d[k - 1]) / seg : 0;
        out.push([pts[k - 1][0] + (pts[k][0] - pts[k - 1][0]) * u, pts[k - 1][1] + (pts[k][1] - pts[k - 1][1]) * u]);
    }
    if (out.length > 1 && out[0][0] > out[n - 1][0]) { out.reverse(); }
    return out;
};

// #RRGGBB -> [r, g, b] in 0..1 (AE's colour format).
SL.hex = function (h) {
    var s = String(h).replace("#", "");
    if (s.length === 3) { s = s.charAt(0) + s.charAt(0) + s.charAt(1) + s.charAt(1) + s.charAt(2) + s.charAt(2); }
    return [parseInt(s.substr(0, 2), 16) / 255, parseInt(s.substr(2, 2), 16) / 255, parseInt(s.substr(4, 2), 16) / 255];
};
