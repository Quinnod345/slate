// A small mock of After Effects' scripting DOM, enough to run Slate's ExtendScript files in node.
//
// It is not After Effects. It models the parts Slate's scripts touch (projects, items, comps, layers, property
// trees by match name, keyframes, the render queue, app.settings) and it evaluates keyframes the way AE draws them:
// a bezier segment is a cubic in (time, value) whose control points come from each key's speed and influence, hold
// and linear are AE's own, spatial properties move along their path, shapes and colours interpolate a normalised
// progress. That lets the tests check that SL.anim's speeds and influences give back the brand's eases, that frames
// land where the film clock says, and that nothing is ever discarded: replacing a project with unsaved changes
// throws here, as it would raise a dialog in AE.
//
// Files are real (a temp folder per test): scripts read the build spec and write reports there; a "saved" project
// is a small JSON stub. The live test of all this is the smoke render, on a person's go.

import {existsSync, mkdirSync, readFileSync, writeFileSync} from 'node:fs';
import {dirname, resolve} from 'node:path';

export const KIT = {LINEAR: 6612, BEZIER: 6613, HOLD: 6614};
export const PVT = {NO_VALUE: 6412, ThreeD_SPATIAL: 6413, ThreeD: 6414, TwoD_SPATIAL: 6415, TwoD: 6416, OneD: 6417,
  COLOR: 6418, CUSTOM_VALUE: 6419, MARKER: 6420, LAYER_INDEX: 6421, MASK_INDEX: 6422, SHAPE: 6423, TEXT_DOCUMENT: 6424};
export const PT = {PROPERTY: 6212, INDEXED_GROUP: 6213, NAMED_GROUP: 6214};

const clone = (v) => (Array.isArray(v) ? v.map(clone) : v && typeof v === 'object' && v.__shape ? v.copy() : v);

function bezierY(p, x) {
  if (x <= 0) return 0;
  if (x >= 1) return 1;
  let lo = 0;
  let hi = 1;
  for (let i = 0; i < 60; i++) {
    const t = (lo + hi) / 2;
    const s = 1 - t;
    const bx = 3 * s * s * t * p[0] + 3 * s * t * t * p[2] + t * t * t;
    if (bx < x) lo = t;
    else hi = t;
  }
  const t = (lo + hi) / 2;
  const s = 1 - t;
  return 3 * s * s * t * p[1] + 3 * s * t * t * p[3] + t * t * t;
}

export class KeyframeEase {
  constructor(speed, influence) {
    if (!(influence >= 0.1 && influence <= 100)) throw new Error(`KeyframeEase influence ${influence} out of range`);
    this.speed = speed;
    this.influence = influence;
  }
}

export class Shape {
  constructor() {
    this.__shape = true;
    this.vertices = [];
    this.inTangents = [];
    this.outTangents = [];
    this.closed = false;
  }
  copy() {
    const s = new Shape();
    s.vertices = clone(this.vertices);
    s.inTangents = clone(this.inTangents);
    s.outTangents = clone(this.outTangents);
    s.closed = this.closed;
    return s;
  }
}

export class MarkerValue {
  constructor(comment) {
    this.comment = comment;
  }
}

export class TextDocument {
  constructor(text) {
    this.text = text;
    this.font = 'ArialMT';
    this.fontSize = 36;
    this.fillColor = [1, 1, 1];
    this.applyFill = true;
    this.applyStroke = false;
    this.tracking = 0;
    this.autoLeading = true;
    this.leading = 0;
    this.justification = 7413;
    this.allCaps = false;
  }
  resetCharStyle() {}
}

// ------------------------------------------------------------------ properties

export class Property {
  constructor(matchName, name, pvt, value, owner) {
    this.matchName = matchName;
    this.name = name;
    this.propertyType = PT.PROPERTY;
    this.propertyValueType = pvt;
    this._value = clone(value);
    this.keys = [];
    this.expression = '';
    this.expressionEnabled = true;
    this.expressionError = '';
    this.canSetExpression = pvt !== PVT.MARKER;
    this.owner = owner;
    this._followers = null;
    this._separated = false;
  }
  get dimensionsSeparated() {
    return this._separated;
  }
  set dimensionsSeparated(on) {
    if (!this._followers) throw new Error(`${this.matchName} can't separate dimensions`);
    if (on && !this._separated) {
      const v = this.valueAtTime(0);
      this._followers.forEach((f, d) => {
        f._value = v[d] ?? 0;
      });
    }
    this._separated = !!on;
  }
  getSeparationFollower(d) {
    if (!this._followers || !this._separated) throw new Error(`${this.matchName} has no separated dimension ${d}`);
    return this._followers[d];
  }
  get value() {
    return this.keys.length ? this.valueAtTime(0, true) : clone(this._value);
  }
  get numKeys() {
    return this.keys.length;
  }
  get isSpatial() {
    return this.propertyValueType === PVT.TwoD_SPATIAL || this.propertyValueType === PVT.ThreeD_SPATIAL;
  }
  dims() {
    if (this.isSpatial || [PVT.OneD, PVT.SHAPE, PVT.COLOR, PVT.MARKER, PVT.TEXT_DOCUMENT].includes(this.propertyValueType)) return 1;
    return this.propertyValueType === PVT.ThreeD ? 3 : 2;
  }
  _check(v) {
    if (this.propertyValueType === PVT.SHAPE && !(v && v.__shape)) throw new Error(`${this.matchName} needs a Shape`);
    if ([PVT.TwoD, PVT.TwoD_SPATIAL].includes(this.propertyValueType) && !(Array.isArray(v) && v.length >= 2)) {
      throw new Error(`${this.matchName} needs [x, y], got ${JSON.stringify(v)}`);
    }
    if (this.propertyValueType === PVT.OneD && typeof v !== 'number') throw new Error(`${this.matchName} needs a number`);
    if (this.propertyValueType === PVT.COLOR && !(Array.isArray(v) && v.length >= 3)) throw new Error(`${this.matchName} needs a colour`);
  }
  _norm(v) {
    if (this.propertyValueType === PVT.ThreeD && Array.isArray(v) && v.length === 2) return [v[0], v[1], 100];
    if (this.propertyValueType === PVT.COLOR && Array.isArray(v) && v.length === 3) return [v[0], v[1], v[2], 1];
    return v;
  }
  setValue(v) {
    if (this.keys.length) throw new Error(`${this.matchName} has keyframes; setValue would throw in AE`);
    this._check(v);
    this._value = clone(this._norm(v));
    this.owner?.touch();
  }
  setValueAtTime(t, v) {
    if (this.propertyValueType !== PVT.MARKER) this._check(v);
    const key = {time: t, value: clone(this._norm(v)), inInterp: KIT.LINEAR, outInterp: KIT.LINEAR,
      inEase: null, outEase: null};
    const i = this.keys.findIndex((k) => Math.abs(k.time - t) < 1e-9);
    if (i >= 0) this.keys[i] = key;
    else {
      this.keys.push(key);
      this.keys.sort((a, b) => a.time - b.time);
    }
    this.owner?.touch();
  }
  _k(k) {
    if (!(k >= 1 && k <= this.keys.length)) throw new Error(`${this.matchName}: no key ${k}`);
    return this.keys[k - 1];
  }
  keyTime(k) {
    return this._k(k).time;
  }
  keyValue(k) {
    return clone(this._k(k).value);
  }
  nearestKeyIndex(t) {
    let best = 1;
    let bd = Infinity;
    this.keys.forEach((k, i) => {
      const d = Math.abs(k.time - t);
      if (d < bd) {
        bd = d;
        best = i + 1;
      }
    });
    return best;
  }
  setInterpolationTypeAtKey(k, inT, outT) {
    const key = this._k(k);
    for (const v of [inT, outT]) if (![KIT.LINEAR, KIT.BEZIER, KIT.HOLD].includes(v)) throw new Error('bad interpolation');
    key.inInterp = inT;
    key.outInterp = outT === undefined ? inT : outT;
  }
  keyInInterpolationType(k) {
    return this._k(k).inInterp;
  }
  keyOutInterpolationType(k) {
    return this._k(k).outInterp;
  }
  setTemporalEaseAtKey(k, inE, outE) {
    const key = this._k(k);
    const n = this.dims();
    if (!Array.isArray(inE) || inE.length !== n || !Array.isArray(outE) || outE.length !== n) {
      throw new Error(`${this.matchName}: temporal ease arrays must have ${n} entries`);
    }
    key.inEase = inE;
    key.outEase = outE;
  }
  keyInTemporalEase(k) {
    return this._k(k).inEase || [new KeyframeEase(0, 33.33)];
  }
  keyOutTemporalEase(k) {
    return this._k(k).outEase || [new KeyframeEase(0, 33.33)];
  }
  setSpatialAutoBezierAtKey(k) {
    if (!this.isSpatial) throw new Error('not spatial');
    this._k(k);
  }
  setSpatialContinuousAtKey(k) {
    if (!this.isSpatial) throw new Error('not spatial');
    this._k(k);
  }
  setSpatialTangentsAtKey(k, a, b) {
    if (!this.isSpatial) throw new Error('not spatial');
    const v = this._k(k).value;
    if (a.length !== v.length || b.length !== v.length) throw new Error('spatial tangent size');
  }
  // AE's segment model: 1D cubic in (time, value) from speed and influence.
  static _seg(ta, va, tb, vb, out, inn, outInterp, inInterp) {
    const T = tb - ta;
    const dv = (vb - va) / T;
    const outS = outInterp === KIT.LINEAR ? dv : out.speed;
    const outI = outInterp === KIT.LINEAR ? 1 / 3 : out.influence / 100;
    const inS = inInterp === KIT.LINEAR ? dv : inn.speed;
    const inI = inInterp === KIT.LINEAR ? 1 / 3 : inn.influence / 100;
    return (t) => {
      const u = (t - ta) / T;
      if (vb === va && outS === 0 && inS === 0) return va;
      const span = vb - va || 1;
      const p1y = (outS * outI * T) / span;
      const p2y = 1 - (inS * inI * T) / span;
      return va + (vb - va) * bezierY([outI, p1y, 1 - inI, p2y], u);
    };
  }
  valueAtTime(t, _preExpression) {
    if (this._separated) return this._followers.slice(0, 2).map((f) => f.valueAtTime(t));
    const ks = this.keys;
    if (!ks.length) return clone(this._value);
    if (t <= ks[0].time) return clone(ks[0].value);
    if (t >= ks[ks.length - 1].time) return clone(ks[ks.length - 1].value);
    let i = 0;
    while (i < ks.length - 2 && t >= ks[i + 1].time) i++;
    const a = ks[i];
    const b = ks[i + 1];
    if (a.outInterp === KIT.HOLD) return clone(a.value);
    const oe = a.outEase || [new KeyframeEase(0, 33.33)];
    const ie = b.inEase || [new KeyframeEase(0, 33.33)];
    const vt = this.propertyValueType;
    if (vt === PVT.SHAPE || vt === PVT.COLOR || this.isSpatial) {
      let span = 1;
      if (this.isSpatial) span = Math.hypot(...a.value.map((x, j) => b.value[j] - x)) || 1;
      const f = Property._seg(a.time, 0, b.time, span, oe[0], ie[0], a.outInterp, b.inInterp);
      const u = f(t) / span;
      if (vt === PVT.SHAPE) {
        const s = a.value.copy();
        s.vertices = a.value.vertices.map((p, j) => p.map((x, d) => x + (b.value.vertices[j][d] - x) * u));
        return s;
      }
      return a.value.map((x, j) => x + (b.value[j] - x) * u);
    }
    if (Array.isArray(a.value)) {
      return a.value.map((x, j) =>
        Property._seg(a.time, x, b.time, b.value[j], oe[j] || oe[0], ie[j] || ie[0], a.outInterp, b.inInterp)(t));
    }
    return Property._seg(a.time, a.value, b.time, b.value, oe[0], ie[0], a.outInterp, b.inInterp)(t);
  }
  addKey(t) {
    this.setValueAtTime(t, this.valueAtTime(t));
    return this.nearestKeyIndex(t);
  }
}

export class MarkerProperty extends Property {
  constructor(owner) {
    super('ADBE Marker', 'Marker', PVT.MARKER, null, owner);
  }
}

// ------------------------------------------------------------------ property groups

// What addProperty(matchName) creates: [name, type, children] (children built lazily from this table).
const TEMPLATES = {
  'ADBE Vector Group': ['Group', PT.NAMED_GROUP, () => [
    group('ADBE Vectors Group', 'Contents', PT.INDEXED_GROUP, []),
    group('ADBE Vector Transform Group', 'Transform', PT.NAMED_GROUP, [
      prop('ADBE Vector Anchor', 'Anchor Point', PVT.TwoD, [0, 0]),
      prop('ADBE Vector Position', 'Position', PVT.TwoD, [0, 0]),
      prop('ADBE Vector Scale', 'Scale', PVT.TwoD, [100, 100]),
      prop('ADBE Vector Rotation', 'Rotation', PVT.OneD, 0),
      prop('ADBE Vector Group Opacity', 'Opacity', PVT.OneD, 100),
    ]),
  ]],
  'ADBE Vector Shape - Rect': ['Rectangle Path 1', PT.NAMED_GROUP, () => [
    prop('ADBE Vector Rect Size', 'Size', PVT.TwoD, [100, 100]),
    prop('ADBE Vector Rect Position', 'Position', PVT.TwoD, [0, 0]),
    prop('ADBE Vector Rect Roundness', 'Roundness', PVT.OneD, 0),
  ]],
  'ADBE Vector Shape - Ellipse': ['Ellipse Path 1', PT.NAMED_GROUP, () => [
    prop('ADBE Vector Ellipse Size', 'Size', PVT.TwoD, [100, 100]),
    prop('ADBE Vector Ellipse Position', 'Position', PVT.TwoD, [0, 0]),
  ]],
  'ADBE Vector Shape - Group': ['Path 1', PT.NAMED_GROUP, () => [
    prop('ADBE Vector Shape', 'Path', PVT.SHAPE, new Shape()),
  ]],
  'ADBE Vector Graphic - Fill': ['Fill 1', PT.NAMED_GROUP, () => [
    prop('ADBE Vector Fill Color', 'Color', PVT.COLOR, [1, 1, 1, 1]),
    prop('ADBE Vector Fill Opacity', 'Opacity', PVT.OneD, 100),
  ]],
  'ADBE Vector Graphic - Stroke': ['Stroke 1', PT.NAMED_GROUP, () => [
    prop('ADBE Vector Stroke Color', 'Color', PVT.COLOR, [1, 1, 1, 1]),
    prop('ADBE Vector Stroke Width', 'Stroke Width', PVT.OneD, 2),
    prop('ADBE Vector Stroke Line Cap', 'Line Cap', PVT.OneD, 1),
    prop('ADBE Vector Stroke Line Join', 'Line Join', PVT.OneD, 1),
    prop('ADBE Vector Stroke Opacity', 'Opacity', PVT.OneD, 100),
    group('ADBE Vector Stroke Dashes', 'Dashes', PT.INDEXED_GROUP, []),
  ]],
  'ADBE Vector Stroke Dash 1': ['Dash', PT.PROPERTY],
  'ADBE Vector Stroke Gap 1': ['Gap', PT.PROPERTY],
  'ADBE Vector Filter - Trim': ['Trim Paths 1', PT.NAMED_GROUP, () => [
    prop('ADBE Vector Trim Start', 'Start', PVT.OneD, 0),
    prop('ADBE Vector Trim End', 'End', PVT.OneD, 100),
    prop('ADBE Vector Trim Offset', 'Offset', PVT.OneD, 0),
  ]],
  'ADBE Drop Shadow': ['Drop Shadow', PT.NAMED_GROUP, () => []],
  'ADBE Slider Control': ['Slider Control', PT.NAMED_GROUP, () => [prop('ADBE Slider Control-0001', 'Slider', PVT.OneD, 0)]],
};

function prop(matchName, name, pvt, value) {
  return (owner) => new Property(matchName, name, pvt, value, owner);
}
function group(matchName, name, type, children) {
  return (owner) => new PropertyGroup(matchName, name, type, children, owner);
}

export class PropertyGroup {
  constructor(matchName, name, type, children, owner) {
    this.matchName = matchName;
    this.name = name;
    this.propertyType = type;
    this.owner = owner;
    this.children = (children || []).map((make) => make(owner));
  }
  get numProperties() {
    return this.children.length;
  }
  property(key) {
    if (typeof key === 'number') return this.children[key - 1] ?? null;
    return this.children.find((c) => c.matchName === key) ?? this.children.find((c) => c.name === key) ?? null;
  }
  addProperty(matchName) {
    const tpl = TEMPLATES[matchName];
    if (!tpl) throw new Error(`mock AE can't add ${matchName} to ${this.matchName}`);
    const [name, type, kids] = tpl;
    const made = type === PT.PROPERTY ? new Property(matchName, name, PVT.OneD, 0, this.owner)
      : new PropertyGroup(matchName, `${name.replace(/ 1$/, '')} ${this.children.length + 1}`.trim(), type, kids ? kids() : [], this.owner);
    this.children.push(made);
    this.owner?.touch();
    return made;
  }
}

// ------------------------------------------------------------------ items and layers

let nextId = 1;

class Item {
  constructor(project, name) {
    this.project = project;
    this.name = name;
    this.id = nextId++;
    this.parentFolder = null;
    this.comment = '';
  }
  remove() {
    this.project._items = this.project._items.filter((i) => i !== this);
    this.project.touch();
  }
  touch() {
    this.project.touch();
  }
}

export class FolderItem extends Item {}

export class FootageItem extends Item {
  constructor(project, name, meta) {
    super(project, name);
    Object.assign(this, {width: 0, height: 0, frameRate: 0, duration: 0, hasAudio: false, hasVideo: true,
      footageMissing: false, pixelAspect: 1}, meta);
    this.mainSource = {conformFrameRate: 0, isSolid: !!meta.solid};
  }
}

class Layer {
  constructor(comp, name, source, kind) {
    this.containingComp = comp;
    this.name = name;
    this.source = source;
    this.kind = kind;
    this.startTime = 0;
    this.inPoint = 0;
    this.outPoint = comp.duration;
    this.motionBlur = false;
    this.parent = null;
    this.enabled = true;
    this.hasVideo = source ? source.hasVideo !== false : kind !== 'audio';
    this.hasAudio = source ? !!source.hasAudio : false;
    const owner = comp.project;
    this.root = new PropertyGroup('ADBE Root', name, PT.INDEXED_GROUP, [
      group('ADBE Marker Holder', 'Marker', PT.NAMED_GROUP, []),
      group('ADBE Transform Group', 'Transform', PT.NAMED_GROUP, [
        prop('ADBE Anchor Point', 'Anchor Point', PVT.ThreeD_SPATIAL, [0, 0, 0]),
        prop('ADBE Position', 'Position', PVT.ThreeD_SPATIAL, [comp.width / 2, comp.height / 2, 0]),
        prop('ADBE Scale', 'Scale', PVT.ThreeD, [100, 100, 100]),
        prop('ADBE Rotate X', 'X Rotation', PVT.OneD, 0),
        prop('ADBE Rotate Y', 'Y Rotation', PVT.OneD, 0),
        prop('ADBE Rotate Z', 'Rotation', PVT.OneD, 0),
        prop('ADBE Opacity', 'Opacity', PVT.OneD, 100),
      ]),
      group('ADBE Effect Parade', 'Effects', PT.INDEXED_GROUP, []),
    ], owner);
    this.root.children[0] = new MarkerProperty(owner);
    // 2D layers take and give 2D positions, like AE
    const xg = this.root.property('ADBE Transform Group');
    for (const m of ['ADBE Anchor Point', 'ADBE Position']) {
      const p = xg.property(m);
      p.propertyValueType = PVT.TwoD_SPATIAL;
      p._value = p._value.slice(0, 2);
    }
    // X/Y/Z Position: always in the group, used once Position's dimensions are separated
    const pos = xg.property('ADBE Position');
    pos._followers = ['X Position', 'Y Position', 'Z Position'].map((n, d) => {
      const f = new Property(`ADBE Position_${d}`, n, PVT.OneD, d < 2 ? pos._value[d] : 0, owner);
      xg.children.push(f);
      return f;
    });
  }
  property(key) {
    if (key === 'ADBE Marker' || key === 'Marker') return this.root.children[0];
    return this.root.property(key);
  }
  get numProperties() {
    return this.root.numProperties;
  }
  get transform() {
    const g = this.property('ADBE Transform Group');
    return {position: g.property('ADBE Position'), opacity: g.property('ADBE Opacity'), scale: g.property('ADBE Scale')};
  }
  get marker() {
    return this.root.children[0];
  }
  get index() {
    return this.containingComp._layers.indexOf(this) + 1;
  }
  moveToEnd() {
    const L = this.containingComp._layers;
    L.splice(L.indexOf(this), 1);
    L.push(this);
  }
  moveBefore(other) {
    const L = this.containingComp._layers;
    L.splice(L.indexOf(this), 1);
    L.splice(L.indexOf(other), 0, this);
  }
  remove() {
    const L = this.containingComp._layers;
    L.splice(L.indexOf(this), 1);
  }
}

export class AVLayer extends Layer {}
export class ShapeLayer extends Layer {
  constructor(comp, name) {
    super(comp, name, null, 'shape');
    this.root.children.splice(2, 0, new PropertyGroup('ADBE Root Vectors Group', 'Contents', PT.INDEXED_GROUP, [], comp.project));
  }
}
export class TextLayer extends Layer {
  constructor(comp, text, box) {
    super(comp, text, null, 'text');
    const doc = new Property('ADBE Text Document', 'Source Text', PVT.TEXT_DOCUMENT, new TextDocument(text), comp.project);
    const textGroup = new PropertyGroup('ADBE Text Properties', 'Text', PT.NAMED_GROUP, [], comp.project);
    textGroup.children.push(doc, new PropertyGroup('ADBE Text Animators', 'Animators', PT.INDEXED_GROUP, [], comp.project));
    this.root.children.splice(2, 0, textGroup);
    this.box = box || null;
  }
}

class LayerCollection {
  constructor(comp) {
    this.comp = comp;
  }
  _push(layer) {
    this.comp._layers.unshift(layer);
    this.comp.project.touch();
    return layer;
  }
  add(item) {
    if (!item) throw new Error('layers.add needs an item');
    return this._push(new AVLayer(this.comp, item.name, item, 'av'));
  }
  addSolid(color, name, w, h, par, dur) {
    if (!(Array.isArray(color) && color.length === 3)) throw new Error('addSolid needs [r, g, b]');
    const src = new FootageItem(this.comp.project, name, {width: w, height: h, duration: dur, solid: true});
    return this._push(new AVLayer(this.comp, name, src, 'solid'));
  }
  addNull(dur) {
    const src = new FootageItem(this.comp.project, 'Null', {width: 100, height: 100, duration: dur, solid: true});
    return this._push(new AVLayer(this.comp, 'Null 1', src, 'null'));
  }
  addShape() {
    return this._push(new ShapeLayer(this.comp, 'Shape Layer 1'));
  }
  addText(text) {
    return this._push(new TextLayer(this.comp, text));
  }
  addBoxText(box, text) {
    return this._push(new TextLayer(this.comp, text, box));
  }
}

export class CompItem extends Item {
  constructor(project, name, w, h, par, dur, fps) {
    super(project, name);
    for (const [k, v] of Object.entries({w, h, par, dur, fps})) {
      if (typeof v !== 'number' || !isFinite(v) || v <= 0) throw new Error(`addComp: bad ${k} ${v}`);
    }
    if (dur > 10800) throw new Error('addComp: duration over 3 hours');
    this.width = w;
    this.height = h;
    this.pixelAspect = par;
    this.duration = dur;
    this.frameRate = fps;
    this.bgColor = [0, 0, 0];
    this.motionBlur = false;
    this.shutterAngle = 180;
    this.shutterPhase = -90;
    this.motionBlurSamplesPerFrame = 16;
    this.motionBlurAdaptiveSampleLimit = 128;
    this._layers = [];
    this.layers = new LayerCollection(this);
    this.markerProperty = new MarkerProperty(project);
  }
  get numLayers() {
    return this._layers.length;
  }
  layer(i) {
    if (typeof i === 'string') return this._layers.find((l) => l.name === i) ?? null;
    return this._layers[i - 1] ?? null;
  }
  openInViewer() {}
}

// ------------------------------------------------------------------ render queue

class OutputModule {
  constructor(templates) {
    this.templates = templates;
    this.template = null;
    this.file = null;
  }
  applyTemplate(name) {
    if (!this.templates.includes(name)) throw new Error(`no output-module template called ${name}`);
    this.template = name;
  }
}

class RQItem {
  constructor(queue, comp, env) {
    this.queue = queue;
    this.comp = comp;
    this.env = env;
    this.templates = env.rsTemplates;
    this.template = null;
    this.timeSpanStart = 0;
    this.timeSpanDuration = comp.duration;
    this.status = 3015;
    this._oms = [new OutputModule(env.omTemplates)];
    this.outputModules = {add: () => {
      const om = new OutputModule(env.omTemplates);
      this._oms.push(om);
      return om;
    }};
  }
  get numOutputModules() {
    return this._oms.length;
  }
  outputModule(i) {
    return this._oms[i - 1];
  }
  applyTemplate(name) {
    if (!this.templates.includes(name)) throw new Error(`no render-settings template called ${name}`);
    this.template = name;
  }
  remove() {
    this.queue._items.splice(this.queue._items.indexOf(this), 1);
  }
}

function renderQueue(env) {
  const q = {_items: [], rendering: false};
  const items = new Proxy({}, {
    get(_t, key) {
      if (key === 'length') return q._items.length;
      if (key === 'add') return (comp) => {
        const it = new RQItem(q, comp, env);
        q._items.push(it);
        return it;
      };
      if (typeof key === 'string' && /^\d+$/.test(key)) return q._items[Number(key) - 1];
      return undefined;
    },
  });
  return {items, get numItems() {
    return q._items.length;
  }, get rendering() {
    return q.rendering;
  }, _q: q};
}

// ------------------------------------------------------------------ project and app

export class Project {
  constructor(env, file = null) {
    this.env = env;
    this._items = [];
    this.file = file;
    this.dirty = false;
    this.bitsPerChannel = 8;
    this.workingSpace = '';
    this.linearBlending = true;
    this.gpuAccelType = 1;
    this.renderQueue = renderQueue(env);
    this.items = {
      addComp: (name, w, h, par, dur, fps) => this._add(new CompItem(this, name, w, h, par, dur, fps)),
      addFolder: (name) => this._add(new FolderItem(this, name)),
    };
    this.closed = false;
  }
  _add(item) {
    this._items.push(item);
    this.touch();
    return item;
  }
  touch() {
    this.dirty = true;
  }
  get numItems() {
    return this._items.length;
  }
  item(i) {
    return this._items[i - 1] ?? null;
  }
  importFile(io) {
    const path = io.file.fsName;
    if (!existsSync(path)) throw new Error(`import: no file ${path}`);
    const meta = this.env.media[path] || {width: 0, height: 0, frameRate: 0, duration: 1, hasVideo: true};
    return this._add(new FootageItem(this, path.replace(/^.*\//, ''), meta));
  }
  save(file) {
    const f = file || this.file;
    if (!f) throw new Error('save: untitled project needs a file');
    mkdirSync(dirname(f.fsName), {recursive: true});
    writeFileSync(f.fsName, JSON.stringify({mockAEP: true, items: this._items.length}));
    this.file = f;
    this.dirty = false;
    this.env.saves.push(f.fsName);
  }
  close(option) {
    if (this.dirty && option !== CLOSE.DO_NOT_SAVE_CHANGES && option !== CLOSE.SAVE_CHANGES) {
      throw new Error('close would prompt about unsaved changes');
    }
    if (this.dirty) this.env.discarded.push(this.file ? this.file.fsName : '(untitled)');
    this.closed = true;
  }
}

export const CLOSE = {DO_NOT_SAVE_CHANGES: 1212, PROMPT_TO_SAVE_CHANGES: 1213, SAVE_CHANGES: 1214};

export class MockFile {
  constructor(path) {
    this.fsName = resolve(String(path));
    this.encoding = 'UTF-8';
    this._mode = null;
    this._buf = '';
  }
  get exists() {
    return existsSync(this.fsName);
  }
  get parent() {
    return new MockFolder(dirname(this.fsName));
  }
  get name() {
    return this.fsName.replace(/^.*\//, '');
  }
  open(mode) {
    this._mode = mode;
    if (mode === 'r' && !this.exists) return false;
    this._buf = '';
    return true;
  }
  read() {
    return readFileSync(this.fsName, 'utf8');
  }
  write(s) {
    this._buf += s;
  }
  close() {
    if (this._mode === 'w') {
      mkdirSync(dirname(this.fsName), {recursive: true});
      writeFileSync(this.fsName, this._buf);
    }
    this._mode = null;
  }
}

export class MockFolder {
  constructor(path) {
    this.fsName = resolve(String(path));
  }
  get exists() {
    return existsSync(this.fsName);
  }
  create() {
    mkdirSync(this.fsName, {recursive: true});
    return true;
  }
  get parent() {
    return new MockFolder(dirname(this.fsName));
  }
}

/**
 * Build the globals an ExtendScript file sees. opts: media {path: {width, height, frameRate, duration, hasAudio,
 * hasVideo}}, fonts [[{familyName, styleName, postScriptName}]], missingFonts [...], version, prefs {section: {key:
 * long}}, dirtyProject (start with an open project that has unsaved changes).
 */
export function makeAE(opts = {}) {
  const env = {
    media: opts.media || {},
    saves: [],
    discarded: [],
    settings: {},
    flushes: 0,
    omTemplates: opts.omTemplates || ['H.264 - Match Render Settings - 40 Mbps', 'High Quality', 'High Quality with Alpha', 'Lossless'],
    rsTemplates: opts.rsTemplates || ['Best Settings', 'Current Settings', 'Draft Settings'],
    purged: 0,
  };
  let project = new Project(env, opts.projectFile ? new MockFile(opts.projectFile) : null);
  if (opts.dirtyProject) {
    project.items.addComp('Their work', 64, 64, 1, 1, 24);
    project.dirty = true;
  }
  const app = {
    version: opts.version || '26.5x89 (mock)',
    get project() {
      return project;
    },
    settings: {saveSetting: (section, key, value) => {
      (env.settings[section] ||= {})[key] = String(value);
    }},
    preferences: {
      saveToDisk: () => {
        env.flushes++;
      },
      getPrefAsLong: (section, key) => {
        const v = opts.prefs?.[section]?.[key];
        if (v === undefined) throw new Error('no such pref');
        return v;
      },
    },
    newProject: () => {
      if (project.dirty) throw new Error('newProject over a project with unsaved changes (AE would ask to save)');
      if (!project.closed && (project.file || project.numItems)) project.close(CLOSE.DO_NOT_SAVE_CHANGES);
      project = new Project(env);
      return project;
    },
    open: (file) => {
      if (project.dirty) throw new Error('open over a project with unsaved changes (AE would ask to save)');
      if (!file.exists) throw new Error(`open: no project at ${file.fsName}`);
      project = opts.onOpen ? opts.onOpen(env, file) : new Project(env, file);
      return project;
    },
    beginSuppressDialogs: () => {},
    endSuppressDialogs: () => {},
    purge: () => {
      env.purged++;
    },
    fonts: {
      allFonts: opts.fonts || [[{familyName: 'Mock Sans', styleName: 'Regular', postScriptName: 'MockSans-Regular'}]],
      get missingOrSubstitutedFonts() {
        return opts.missingFonts || [];
      },
    },
    effects: [],
    findMenuCommandId: () => 0,
    executeCommand: () => {},
  };
  const globals = {
    app,
    File: MockFile,
    Folder: MockFolder,
    ImportOptions: class {
      constructor(file) {
        this.file = file;
      }
    },
    CompItem,
    FootageItem,
    FolderItem,
    AVLayer,
    ShapeLayer,
    TextLayer,
    KeyframeEase,
    Shape,
    MarkerValue,
    KeyframeInterpolationType: KIT,
    PropertyValueType: PVT,
    PropertyType: PT,
    CloseOptions: CLOSE,
    GpuAccelType: {SOFTWARE: 1813, CUDA: 1812, METAL: 1815, OPENCL: 1814},
    ParagraphJustification: {LEFT_JUSTIFY: 7413, RIGHT_JUSTIFY: 7414, CENTER_JUSTIFY: 7415},
    FrameBlendingType: {NO_FRAME_BLEND: 4012, FRAME_MIX: 4013, PIXEL_MOTION: 4014},
    PurgeTarget: {ALL_CACHES: 1, UNDO_CACHES: 2, SNAPSHOT_CACHES: 3, IMAGE_CACHES: 4},
    PREFType: {PREF_Type_MACHINE_SPECIFIC: 0, PREF_Type_MACHINE_INDEPENDENT: 1},
  };
  return {globals, env, getProject: () => project};
}

/** Decode a run's report from the mock's app.settings (the settings channel), as slate.ae.report does. */
export function settingsReport(env, channel = 'run', section = 'Slate') {
  const s = env.settings[section] || {};
  const n = Number(s[`${channel}.json.n`] || 0);
  let text = '';
  for (let i = 0; i < n; i++) text += s[`${channel}.json.${i}`];
  return {id: s[`${channel}.id`], status: s[`${channel}.status`], data: text ? JSON.parse(decodeURIComponent(text)) : {}};
}
