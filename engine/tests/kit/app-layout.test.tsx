import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {describe, expect, it} from 'vitest';
import {AppBox, AppText, AppViewport, KitProvider, appLayout, appTypeStyle, snapAppPoint, useAppLayout, type AppProfile} from '@slate/kit';

const PROFILE: AppProfile = {width: 360, height: 780, pixelRatio: 3, safeArea: {top: 48, bottom: 30}};
const CONTROL = {x: 24, y: 120, width: 312, height: 44};
const TYPE = {family: 'Fixture Sans', size: 17, lineHeight: 20, weight: 590, tracking: -0.4, opticalSize: 17, baselineOffset: 0.5};

describe('native geometry and camera targets', () => {
  it('scales controls and anchors together while keeping layout in native points', () => {
    const layout = appLayout(PROFILE, {width: 540});
    expect(layout.scale).toBe(1.5);
    expect(layout.rect(CONTROL)).toEqual({x: 36, y: 180, width: 468, height: 66});
    expect(layout.anchor(CONTROL)).toEqual({x: 270, y: 213});
    expect(layout.content).toEqual({x: 0, y: 48, width: 360, height: 702});
  });

  it('keeps the source aspect when both display dimensions are supplied and includes the letterbox in anchors', () => {
    const layout = appLayout(PROFILE, {width: 720, height: 780});
    expect(layout.screen).toEqual({x: 180, y: 0, width: 360, height: 780});
    expect(layout.anchor(CONTROL, 'topLeft')).toEqual({x: 204, y: 120});
    expect(layout.anchor(CONTROL, 'bottomRight')).toEqual({x: 516, y: 164});
  });

  it('rounds explicitly to the source grid instead of the camera scale', () => {
    expect(snapAppPoint(10.2, PROFILE)).toBeCloseTo(10 + 1 / 3);
    expect(appLayout(PROFILE, {width: 540}).point({x: 10.2, y: -3})).toEqual({x: 15.299999999999999, y: -4.5});
  });

  it('maps a nested control from its parent frame, including letterboxing', () => {
    const root = appLayout(PROFILE, {width: 1080, height: 1170});
    const parent = root.within(CONTROL);
    const child = {x: 8, y: 10, width: 20, height: 12};
    expect(parent.rect(child)).toEqual({x: 318, y: 195, width: 30, height: 18});
    expect(parent.within(child).point({x: 0, y: 0})).toEqual({x: 318, y: 195});
    expect(parent.origin).toEqual({x: 24, y: 120});
    expect(root.origin).toEqual({x: 0, y: 0});
  });

  it('rejects impossible profiles and rectangles before they can disagree with drawn geometry', () => {
    expect(() => appLayout({...PROFILE, safeArea: {top: 780}})).toThrow(/positive content area/);
    expect(() => appLayout(PROFILE, {width: NaN})).toThrow(/finite/);
    expect(() => appLayout(PROFILE).rect({...CONTROL, width: -1})).toThrow(/negative/);
    expect(() => snapAppPoint(0, {...PROFILE, pixelRatio: 0})).toThrow(/greater than zero/);
  });
});

describe('native text independent of film typography', () => {
  it('gives nested React controls the same local frame as their camera anchors', () => {
    function Target() {
      const point = useAppLayout().point({x: 8, y: 10});
      return <span data-target={`${point.x},${point.y}`} />;
    }
    const box = renderToStaticMarkup(<AppViewport profile={PROFILE} width={540}><AppBox rect={CONTROL}><Target /></AppBox></AppViewport>);
    const text = renderToStaticMarkup(<AppViewport profile={PROFILE} width={540}><AppText rect={CONTROL} typography={TYPE}><Target /></AppText></AppViewport>);
    expect(box).toContain('data-target="48,195"');
    expect(text).toContain('data-target="48,195"');
  });

  it('paints a control border without changing its child origin and requires explicit native insets', () => {
    const html = renderToStaticMarkup(<AppViewport profile={PROFILE}><AppBox rect={CONTROL} style={{border: '2px solid currentColor', borderRadius: 8}}><AppBox rect={{x: 8, y: 10, width: 20, height: 12}} /></AppBox></AppViewport>);
    expect(html).toMatch(/left:24px;top:120px;width:312px;height:44px;box-sizing:border-box"><div aria-hidden="true" style="border:2px solid currentColor/);
    expect(html).toContain('left:8px;top:10px;width:20px;height:12px');
    expect(() => renderToStaticMarkup(<AppViewport profile={PROFILE}><AppBox rect={CONTROL} style={{padding: 8}} /></AppViewport>)).toThrow(/native child rectangles/);
  });
  it('rejects positioned native children even when an inline component hides them inside shifted text', () => {
    function InlineIcon() {
      return <AppBox rect={{x: 8, y: 10, width: 20, height: 12}} />;
    }
    const render = (children: React.ReactNode) => renderToStaticMarkup(<AppViewport profile={PROFILE}><AppText typography={TYPE} rect={CONTROL}>{children}</AppText></AppViewport>);
    expect(() => render(<InlineIcon />)).toThrow(/cannot be positioned inside AppText/);
    expect(() => render(<AppViewport profile={PROFILE} />)).toThrow(/cannot be positioned inside AppText/);
    expect(() => render(<AppText typography={TYPE}>Another box</AppText>)).toThrow(/text and inline content/);
    expect(render(<strong>Inline emphasis</strong>)).toContain('top:0.5px"><strong>Inline emphasis</strong>');
  });
  it('keeps wrapping width and calibrated type unchanged across film aspect and size', () => {
    const screen = <AppViewport profile={PROFILE} width={540}><AppBox rect={CONTROL}><AppText typography={TYPE}>A native control</AppText></AppBox></AppViewport>;
    const render = (width: number, height: number) => renderToStaticMarkup(<KitProvider width={width} height={height}>{screen}</KitProvider>);
    const portrait = render(1080, 1920);
    expect(portrait).toBe(render(1920, 1080));
    expect(portrait).toBe(render(640, 640));
    expect(portrait).toContain('width:312px;height:44px');
    expect(portrait).toContain('font-size:17px;line-height:20px');
    expect(portrait).toContain('transform:scale(1.5)');
    expect(portrait).toContain('font-synthesis:none');
    expect(portrait).toContain('&quot;opsz&quot; 17');
  });

  it('preserves explicit variable-font axes and measured baseline without deriving them from film scale', () => {
    const style = appTypeStyle({...TYPE, variationSettings: {wght: 590, opsz: 99}});
    expect(style.fontVariationSettings).toBe('"opsz" 17, "wght" 590');
    expect(style.baselineOffset).toBe(0.5);
    expect(style.letterSpacing).toBe(-0.4);
    expect(style.fontOpticalSizing).toBe('none');
    expect(() => appTypeStyle({...TYPE, variationSettings: {'bad"': 1}})).toThrow(/variation tag/);
  });

  it('requires an explicit product profile instead of falling back to film dimensions', () => {
    expect(() => renderToStaticMarkup(<AppText typography={TYPE}>A control</AppText>)).toThrow(/AppViewport/);
  });
});
