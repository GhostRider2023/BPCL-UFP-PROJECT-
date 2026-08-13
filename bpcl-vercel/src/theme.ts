/**
 * Colour tokens — carried over from the Streamlit dashboard's theme.py
 * ====================================================================
 *
 * Every colour has a job, and the job is the reason it is that colour. Nothing
 * here is decorative:
 *
 *   gold    the product / the thing being tracked
 *   cyan    the ground / the boundary condition
 *   blue    pressure and hydraulics
 *   green   conserved quantities (standard volume) and healthy status
 *   violet  viscosity
 *   red     hydraulic infeasibility, vapour pressure, alarms
 *
 * Keeping the mapping identical to the Streamlit build means a reader who knows
 * one dashboard can read the other without relearning what orange means.
 */

export const C = {
  bg: '#080C15',
  panel: '#111827',
  panel2: '#161F32',
  border: '#243049',
  text: '#E8EEF9',
  muted: '#8397B8',

  gold: '#F5A623', // product
  cyan: '#2DD4BF', // soil / ground
  blue: '#3B82F6', // pressure
  green: '#22C55E', // conserved / OK
  violet: '#A78BFA', // viscosity
  red: '#EF4444', // alarm
  amber: '#F59E0B', // warning
} as const;

/**
 * Temperature colour scale for the mimic diagram, cool to hot. Same stops as
 * the Plotly version so the two renderings of the line are the same picture.
 */
export const TEMP_SCALE: ReadonlyArray<readonly [number, string]> = [
  [0.0, '#1E3A8A'],
  [0.25, '#2DD4BF'],
  [0.5, '#22C55E'],
  [0.7, '#F5A623'],
  [1.0, '#EF4444'],
];

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

/** Sample the temperature scale at `t` in [0, 1]. */
export function sampleTempScale(t: number): string {
  const x = Math.min(Math.max(t, 0), 1);
  for (let i = 0; i < TEMP_SCALE.length - 1; i += 1) {
    const [p0, c0] = TEMP_SCALE[i];
    const [p1, c1] = TEMP_SCALE[i + 1];
    if (x >= p0 && x <= p1) {
      const f = p1 === p0 ? 0 : (x - p0) / (p1 - p0);
      const a = hexToRgb(c0);
      const b = hexToRgb(c1);
      const rgb = a.map((v, k) => Math.round(v + f * (b[k] - v)));
      return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
    }
  }
  return TEMP_SCALE[TEMP_SCALE.length - 1][1];
}

/** `#RRGGBB` plus an alpha, as `rgba()`. */
export function alpha(hex: string, a: number): string {
  const [r, g, b] = hexToRgb(hex);
  return `rgba(${r}, ${g}, ${b}, ${a})`;
}
