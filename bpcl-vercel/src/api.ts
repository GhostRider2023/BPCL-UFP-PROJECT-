/**
 * The wire format, typed
 * ======================
 *
 * Mirrors `api/_engine/service.py`. Two rules from the Python side survive into
 * these types and matter:
 *
 *   1. `number | null`, never `number`, for anything that can be absent.
 *      `tAirC` is null at every chainage except the two terminals, because air
 *      is measured at two points and nowhere else. Typing it `number` would
 *      invite a `?? 0` somewhere and put a fabricated air temperature on
 *      screen. Null means "no measurement", and the UI renders it as absence.
 *
 *   2. The profile is columnar — one array per column, all the same length —
 *      because that is both a third of the JSON and the shape charts want.
 */

export interface Waypoint {
  id: number;
  name: string;
  lat: number;
  lon: number;
  km: number;
  elevationM: number;
  odInch: number;
  wallMm: number;
  roughnessMm: number;
  burialM: number;
  type: string;
  source: string;
}

export interface ProductInfo {
  key: string;
  name: string;
  standard: string;
  densityRef: number;
  densityMin: number;
  densityMax: number;
  cpJkgK: number;
  mu20: number;
  mu40: number;
}

export interface Limits {
  flowMinM3hr: number;
  flowMaxM3hr: number;
  flowStepM3hr: number;
  velocityCapMs: number;
  tempMinC: number;
  tempMaxC: number;
  pressureMinBar: number;
  pressureMaxBar: number;
  volumeMinKL: number;
  volumeMaxKL: number;
  densityMinKgm3: number;
  densityMaxKgm3: number;
  uScaleMin: number;
  uScaleMax: number;
  minSuctionBar: number;
}

/** The console's input vector — exactly what `/api/simulate` accepts. */
export interface Params {
  product: string;
  densityKgm3: number;
  volumeKL: number;
  tempC: number;
  pressureBar: number;
  flowM3hr: number;
  month: number;
  viscousHeating: boolean;
  elevation: boolean;
  pressureCorrection: boolean;
  uScale: number;
}

export interface Meta {
  ok: true;
  route: {
    lengthKm: number;
    linefillM3: number;
    originName: string;
    terminusName: string;
    areaMainM2: number;
    areaTailM2: number;
    tailFraction: number;
    waypoints: Waypoint[];
  };
  products: ProductInfo[];
  months: { value: number; name: string }[];
  pumpStations: { name: string; km: number; dischargeBar: number }[];
  flowSplits: { name: string; km: number; deliveredFraction: number }[];
  terminals: string[];
  limits: Limits;
  constants: { burialDepthM: number; soilVar: string; baseTempC: number };
  defaults: Params;
  presets: { name: string; values: Partial<Params> }[];
}

export interface AirStation {
  name: string;
  lat: number;
  lon: number;
  tempC: number | null;
  failure?: string | null;
}

export interface AirPayload {
  provider: string;
  providerLabel: string;
  ageSeconds: number;
  observedAt: string | null;
  complete: boolean;
  anyData: boolean;
  stations: AirStation[];
  failures: Record<string, string>;
}

/** Columnar state table. Every array has one entry per chainage. */
export interface Profile {
  km: number[];
  lat: number[];
  lon: number[];
  elevation_m: number[];
  T_C: number[];
  T_env_C: number[];
  T_soil_C: number[];
  /** Live air. Null everywhere except the two terminals — see the header note. */
  T_air_C: (number | null)[];
  P_bar: number[];
  P_vapour_bar: number[];
  rho_kgm3: number[];
  mu_cP: number[];
  velocity_ms: number[];
  Re: number[];
  friction_factor: number[];
  V_gross_KL: number[];
  V_std_KL: number[];
  U_Wm2K: number[];
  L_star_km: number[];
  slack_flow: boolean[];
}

export interface StationRow {
  km: number;
  waypoint_name: string;
  elevation_m: number;
  T_air_C: number | null;
  T_soil_C: number;
  T_env_C: number;
  T_env_basis: string;
  T_C: number;
  P_bar: number;
  rho_kgm3: number;
  mu_cP: number;
  Re: number;
  V_gross_KL: number;
  V_std_KL: number;
  U_Wm2K: number;
  L_star_km: number;
  k_soil_WmK: number;
  CTL: number;
  CPL: number;
}

export interface PumpStationResult {
  name: string;
  km: number;
  P_suction_bar: number;
  P_discharge_bar: number;
  boost_bar: number;
  idle: boolean;
  suction_ok: boolean;
}

export interface SimResult {
  ok: true;
  elapsedMs: number;
  inputs: {
    product: string;
    productName: string;
    productStandard: string;
    tempC: number;
    volumeKL: number;
    flowM3hr: number;
    pressureBar: number;
    densityKgm3: number;
    month: number;
    monthName: string;
    uScale: number;
    viscousHeating: boolean;
    elevation: boolean;
    pressureCorrection: boolean;
    ablated: string[];
    velocityMainMs: number;
    velocityTailMs: number;
    massTonnes: number;
  };
  profile: Profile;
  stations: StationRow[];
  kpi: {
    receiptTempC: number;
    deltaTempC: number;
    receiptEnvC: number;
    receiptSoilC: number;
    receiptAirC: number | null;
    receiptEnvBasis: string;
    receiptPressureBar: number;
    deltaPressureBar: number;
    grossVolumeKL: number;
    deltaGrossKL: number;
    deltaGrossPct: number;
    stdVolumeKL: number;
    stdVolumeDriftPct: number;
    transitHours: number;
    meanVelocityMs: number;
    minPressureBar: number;
  };
  physics: {
    feasible: boolean;
    slackOnsetKm: number | null;
    warnings: string[];
    rho60Kgm3: number;
    massKg: number;
    mDotKgs: number;
    vStdDispatchKL: number;
    vStdReceiptKL: number;
    vStdDeliveredKL: number;
    vStdBalanceErrorKL: number;
    pumpStations: PumpStationResult[];
    cavitatingStations: string[];
    flowSplits: {
      name: string;
      km: number;
      delivered_pct: number;
      V_std_delivered_KL: number;
      mass_delivered_kg: number;
      m_dot_delivered_kgs: number;
    }[];
    airTemperatureUsed: boolean;
    stationsWithAir: number;
  };
  air: AirPayload | null;
  airError: string | null;
}

export interface ApiError {
  ok: false;
  error: string;
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const text = await response.text();

  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    // A non-JSON body means the platform answered, not the function — a 504
    // gateway page, an HTML error, a cold-start crash. Say what actually
    // arrived rather than "unexpected token < in JSON".
    throw new Error(
      `${url} returned ${response.status} ${response.statusText} with a non-JSON body`,
    );
  }

  const payload = parsed as T & Partial<ApiError>;
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error ?? `${url} failed with ${response.status}`);
  }
  return payload as T;
}

export const api = {
  meta: () => json<Meta>('/api/meta'),

  simulate: (params: Params, options?: { refreshAir?: boolean; signal?: AbortSignal }) =>
    json<SimResult>('/api/simulate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...params, refreshAir: options?.refreshAir ?? false }),
      signal: options?.signal,
    }),

  /**
   * Download the full state table. Goes through a blob rather than a plain
   * link because the endpoint is a POST — the input vector is the request body,
   * so the file cannot be addressed by a URL.
   */
  async exportCsv(params: Params): Promise<void> {
    const response = await fetch('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    });
    if (!response.ok) {
      throw new Error(`Export failed with ${response.status}`);
    }

    const disposition = response.headers.get('Content-Disposition') ?? '';
    const match = /filename="([^"]+)"/.exec(disposition);
    const blob = await response.blob();
    const href = URL.createObjectURL(blob);

    const link = document.createElement('a');
    link.href = href;
    link.download = match?.[1] ?? 'mmbl_state_table.csv';
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(href);
  },
};
