import React, {createContext, useContext} from 'react';
import {appLayout, appTypeStyle, type AppLayout, type AppProfile, type AppRect, type AppTypography} from './core/app-layout';

const AppContext = createContext<AppLayout | null>(null);
const InlineTextContext = createContext(false);

/** Native screen layout for this viewport. Film time and camera transforms remain in the ordinary kit context. */
export function useAppLayout(): AppLayout {
  const value = useContext(AppContext);
  if (!value) throw new Error('Native app components need an AppViewport with a product profile.');
  return value;
}

/**
 * Lay out the app at its native point size, then scale the completed screen. Text wrapping, optical size and anchors
 * therefore stay unchanged when the film or camera changes. Source safe areas are available through useAppLayout.
 */
export function AppViewport({profile, width, height, style, children}: {
  profile: AppProfile;
  width?: number;
  height?: number;
  style?: React.CSSProperties;
  children?: React.ReactNode;
}) {
  if (useContext(InlineTextContext)) throw new Error('AppViewport cannot be positioned inside AppText; place native geometry in a sibling AppBox.');
  const layout = appLayout(profile, {width, height});
  return (
    <AppContext.Provider value={layout}>
      <div style={{position: 'relative', ...style, width: layout.width, height: layout.height, overflow: 'hidden'}}>
        <div style={{position: 'absolute', left: layout.screen.x, top: layout.screen.y, width: layout.profile.width, height: layout.profile.height, transform: `scale(${layout.scale})`, transformOrigin: 'top left'}}>
          {children}
        </div>
      </div>
    </AppContext.Provider>
  );
}

/** Draw the exact native rectangle also used by appLayout().rect() and .anchor(). Camera staging stays outside it. */
export function AppBox({rect, style, children}: {rect: AppRect; style?: React.CSSProperties; children?: React.ReactNode}) {
  if (useContext(InlineTextContext)) throw new Error('AppBox cannot be positioned inside AppText; place native geometry in a sibling AppBox.');
  const layout = useAppLayout().within(rect);
  const visual: React.CSSProperties = {};
  const border: React.CSSProperties = {};
  for (const [key, value] of Object.entries(style ?? {})) {
    if (/^(padding|margin)/.test(key) && value !== undefined && value !== 0 && value !== '0' && value !== '0px') {
      throw new Error('AppBox content insets belong in its native child rectangles, not CSS padding or margins.');
    }
    const isBorder = key.startsWith('border') && !key.toLowerCase().includes('radius');
    Object.assign(isBorder ? border : visual, {[key]: value});
    if (key.startsWith('border') && key.toLowerCase().includes('radius')) Object.assign(border, {[key]: value});
  }
  return <AppContext.Provider value={layout}>
    <div style={{...visual, position: 'absolute', left: rect.x, top: rect.y, width: rect.width, height: rect.height, boxSizing: 'border-box'}}>
      {/* Paint borders in a separate layer so they cannot shift the children's native coordinate origin. */}
      {Object.keys(border).length > 0 && <div aria-hidden style={{...border, position: 'absolute', inset: 0, boxSizing: 'border-box', pointerEvents: 'none'}} />}
      {children}
    </div>
  </AppContext.Provider>;
}

/** Native product text with calibrated metrics and inline children. Positioned geometry belongs in sibling AppBox elements. */
export function AppText({typography, rect, style, children}: {
  typography: AppTypography;
  rect?: AppRect;
  style?: React.CSSProperties;
  children?: React.ReactNode;
}) {
  if (useContext(InlineTextContext)) throw new Error('AppText accepts text and inline content; place another native text box in a sibling AppBox.');
  const layout = useAppLayout();
  if (rect) layout.rect(rect);
  const {baselineOffset, ...typeStyle} = appTypeStyle(typography);
  const box: React.CSSProperties = rect ? {position: 'absolute', left: rect.x, top: rect.y, width: rect.width, height: rect.height} : {};
  const text = (
    <div style={{margin: 0, whiteSpace: 'pre-wrap', ...style, ...typeStyle, ...box, boxSizing: 'border-box'}}>
      <InlineTextContext.Provider value={true}><span style={{position: 'relative', top: baselineOffset}}>{children}</span></InlineTextContext.Provider>
    </div>
  );
  return rect ? <AppContext.Provider value={layout.within(rect)}>{text}</AppContext.Provider> : text;
}
