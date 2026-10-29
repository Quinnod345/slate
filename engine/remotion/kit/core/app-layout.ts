/** Native app geometry is authored in product points, independently of the film and camera. */
export type AppPoint = {x: number; y: number};
export type AppRect = AppPoint & {width: number; height: number};
export type AppInsets = {top: number; right: number; bottom: number; left: number};
export type AppProfile = {
  width: number;
  height: number;
  /** Source capture pixels per product point. This controls explicit grid rounding, not camera scale. */
  pixelRatio?: number;
  safeArea?: Partial<AppInsets>;
};
export type AppAnchor = 'topLeft' | 'top' | 'topRight' | 'left' | 'center' | 'right' | 'bottomLeft' | 'bottom' | 'bottomRight';

const finite = (value: number, name: string): number => {
  if (!Number.isFinite(value)) throw new Error(`${name} must be finite.`);
  return value;
};
const positive = (value: number, name: string): number => {
  if (finite(value, name) <= 0) throw new Error(`${name} must be greater than zero.`);
  return value;
};
const nonnegative = (value: number, name: string): number => {
  if (finite(value, name) < 0) throw new Error(`${name} cannot be negative.`);
  return value;
};

/** Validate a caller-owned device profile; no product dimensions or platform defaults live in the kit. */
export function appProfile(profile: AppProfile): Required<AppProfile> & {safeArea: AppInsets} {
  const width = positive(profile.width, 'App width');
  const height = positive(profile.height, 'App height');
  const pixelRatio = positive(profile.pixelRatio ?? 1, 'App pixel ratio');
  const safeArea = {
    top: nonnegative(profile.safeArea?.top ?? 0, 'App safe-area top'),
    right: nonnegative(profile.safeArea?.right ?? 0, 'App safe-area right'),
    bottom: nonnegative(profile.safeArea?.bottom ?? 0, 'App safe-area bottom'),
    left: nonnegative(profile.safeArea?.left ?? 0, 'App safe-area left'),
  };
  if (safeArea.left + safeArea.right >= width || safeArea.top + safeArea.bottom >= height) {
    throw new Error('App safe areas must leave a positive content area.');
  }
  return {width, height, pixelRatio, safeArea};
}

/** Round a native coordinate only when the product's source or capture requires pixel-grid alignment. */
export function snapAppPoint(value: number, profile: AppProfile): number {
  const ratio = appProfile(profile).pixelRatio;
  return Math.round(finite(value, 'App coordinate') * ratio) / ratio;
}

const anchorParts: Record<AppAnchor, [number, number]> = {
  topLeft: [0, 0], top: [0.5, 0], topRight: [1, 0],
  left: [0, 0.5], center: [0.5, 0.5], right: [1, 0.5],
  bottomLeft: [0, 1], bottom: [0.5, 1], bottomRight: [1, 1],
};

function validRect(rect: AppRect): AppRect {
  finite(rect.x, 'App rectangle x');
  finite(rect.y, 'App rectangle y');
  nonnegative(rect.width, 'App rectangle width');
  nonnegative(rect.height, 'App rectangle height');
  return rect;
}

export type AppLayout = {
  profile: ReturnType<typeof appProfile>;
  width: number;
  height: number;
  scale: number;
  /** The displayed screen within the viewport. Supplying both dimensions uses a centered contain fit. */
  screen: AppRect;
  /** This local frame's origin in the full screen's native points. */
  origin: AppPoint;
  /** Safe content bounds remain in native points for layout and wrapping. */
  content: AppRect;
  point: (point: AppPoint) => AppPoint;
  rect: (rect: AppRect) => AppRect;
  anchor: (rect: AppRect, at?: AppAnchor) => AppPoint;
  /** Enter a child rectangle's local coordinate frame without changing viewport scale. */
  within: (rect: AppRect) => AppLayout;
};

/** Map native rectangles into viewport pixels. The same rectangles can draw controls and target camera moves. */
export function appLayout(profile: AppProfile, display: {width?: number; height?: number} = {}): AppLayout {
  const p = appProfile(profile);
  const requestedWidth = display.width === undefined ? undefined : positive(display.width, 'App display width');
  const requestedHeight = display.height === undefined ? undefined : positive(display.height, 'App display height');
  const scale = requestedWidth !== undefined && requestedHeight !== undefined
    ? Math.min(requestedWidth / p.width, requestedHeight / p.height)
    : requestedWidth !== undefined ? requestedWidth / p.width : requestedHeight !== undefined ? requestedHeight / p.height : 1;
  const width = requestedWidth ?? p.width * scale;
  const height = requestedHeight ?? p.height * scale;
  const screen = {x: (width - p.width * scale) / 2, y: (height - p.height * scale) / 2, width: p.width * scale, height: p.height * scale};
  const frame = (origin: AppPoint): AppLayout => {
    const point = (value: AppPoint): AppPoint => ({x: screen.x + (origin.x + finite(value.x, 'App point x')) * scale, y: screen.y + (origin.y + finite(value.y, 'App point y')) * scale});
    const rect = (value: AppRect): AppRect => ({...point(validRect(value)), width: value.width * scale, height: value.height * scale});
    const anchor = (value: AppRect, at: AppAnchor = 'center'): AppPoint => {
      validRect(value);
      const pair = anchorParts[at];
      if (!pair) throw new Error(`Unknown app anchor: ${at}.`);
      return point({x: value.x + value.width * pair[0], y: value.y + value.height * pair[1]});
    };
    return {
      profile: p, width, height, scale, screen, origin,
      content: {x: p.safeArea.left - origin.x, y: p.safeArea.top - origin.y, width: p.width - p.safeArea.left - p.safeArea.right, height: p.height - p.safeArea.top - p.safeArea.bottom},
      point, rect, anchor,
      within: (value: AppRect) => {
        validRect(value);
        return frame({x: origin.x + value.x, y: origin.y + value.y});
      },
    };
  };
  return frame({x: 0, y: 0});
}

export type AppTypography = {
  family: string;
  size: number;
  lineHeight: number;
  weight?: number;
  style?: 'normal' | 'italic' | 'oblique';
  tracking?: number;
  /** An explicit native optical size stays in product points when the viewport is scaled. */
  opticalSize?: number;
  opticalSizing?: 'auto' | 'none';
  variationSettings?: Record<string, number>;
  /** A measured baseline adjustment, in native points; it does not change the text box or wrapping width. */
  baselineOffset?: number;
};

/** CSS for native text. Font loading and licence declarations remain in the caller's brand pack and font gate. */
export function appTypeStyle(type: AppTypography) {
  if (!type.family.trim()) throw new Error('App typography needs a font family.');
  positive(type.size, 'App font size');
  positive(type.lineHeight, 'App line height');
  if (type.weight !== undefined && (finite(type.weight, 'App font weight') < 1 || type.weight > 1000)) {
    throw new Error('App font weight must be between 1 and 1000.');
  }
  const settings = {...type.variationSettings};
  if (type.opticalSize !== undefined) settings.opsz = positive(type.opticalSize, 'App optical size');
  const fontVariationSettings = Object.keys(settings).sort().map((tag) => {
    if (!/^[A-Za-z0-9 ]{4}$/.test(tag)) throw new Error(`Invalid app font variation tag: ${tag}.`);
    return `"${tag}" ${finite(settings[tag], `App font variation ${tag}`)}`;
  }).join(', ') || 'normal';
  return {
    fontFamily: type.family,
    fontSize: type.size,
    lineHeight: `${type.lineHeight}px`,
    fontWeight: type.weight ?? 400,
    fontStyle: type.style ?? 'normal',
    letterSpacing: finite(type.tracking ?? 0, 'App tracking'),
    fontOpticalSizing: type.opticalSize !== undefined ? 'none' as const : type.opticalSizing ?? 'none' as const,
    fontVariationSettings,
    fontSynthesis: 'none',
    baselineOffset: finite(type.baselineOffset ?? 0, 'App baseline offset'),
  };
}
