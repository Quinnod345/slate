// The token adapter and font gate must agree on every weight a role can draw.
const NAMED_WEIGHTS: Record<string, number> = {regular: 400, normal: 400, medium: 500, semibold: 600, bold: 700, black: 900};

/** Normalize declared weights once, including the token adapter's default regular and bold faces. */
export function fontWeights(raw?: readonly (number | string)[]): number[] {
  const weights = (raw ?? [400, 700]).map((value) => {
    if (typeof value !== 'string') return value;
    const name = value.trim().toLowerCase();
    return NAMED_WEIGHTS[name] ?? Number(name);
  });
  const valid = weights.filter((weight) => Number.isFinite(weight) && weight >= 1 && weight <= 1000);
  return valid.length ? [...new Set(valid)].sort((a, b) => a - b) : [400];
}
