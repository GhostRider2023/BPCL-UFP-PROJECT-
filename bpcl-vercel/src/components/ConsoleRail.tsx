/**
 * Dispatch console
 * ================
 *
 * The simulator's entire input vector as a horizontal instrument cluster. There
 * is no sidebar, on purpose.
 *
 * Each slot READS BACK the value currently loaded into the model, and opens into
 * the controls that set it. A sidebar shows you widgets and never shows you
 * state; a control room does the opposite — the panel always reads back the
 * state of the plant, and you reach for a control only when you intend to change
 * something.
 *
 * The practical payoff is that the input vector stays legible on the same screen
 * as its output, which is what you actually need when comparing runs.
 */

import type { Meta, Params } from '../api';
import { C } from '../theme';
import { Field, Hot, NumberField, Popover, Readout, Select, Slider, Toggle, num } from './ui';

export function ConsoleRail({
  params,
  meta,
  activePreset,
  onChange,
  onPreset,
}: {
  params: Params;
  meta: Meta;
  activePreset: string | null;
  onChange: (patch: Partial<Params>) => void;
  onPreset: (name: string) => void;
}) {
  const product = meta.products.find((p) => p.key === params.product) ?? meta.products[0];
  const monthName = meta.months.find((m) => m.value === params.month)?.name ?? '—';
  const { limits, route } = meta;

  const velocityTail = (params.flowM3hr * route.tailFraction) / 3600 / route.areaTailM2;
  const velocityMain = params.flowM3hr / 3600 / route.areaMainM2;
  const massTonnes = (params.volumeKL * params.densityKgm3) / 1000;

  const ablated = (
    [
      ['viscous heating', params.viscousHeating],
      ['elevation', params.elevation],
      ['CPL', params.pressureCorrection],
    ] as const
  )
    .filter(([, on]) => !on)
    .map(([name]) => name);

  const uncalibrated = Math.abs(params.uScale - 1) < 1e-9;

  return (
    <section aria-label="Dispatch console">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
        <span className="rail-label">Dispatch console</span>
        <div className="flex flex-wrap gap-1.5">
          {meta.presets.map((preset) => {
            const on = preset.name === activePreset;
            return (
              <button
                key={preset.name}
                type="button"
                onClick={() => onPreset(preset.name)}
                title="Loads a scenario into the console. Every value stays editable."
                className={`cursor-pointer rounded-lg border px-2.5 py-1 text-[10.5px] font-semibold transition-colors ${
                  on
                    ? 'border-gold/55 bg-gold/13 text-gold'
                    : 'border-edge bg-panel/70 text-muted hover:border-gold/40 hover:text-ink'
                }`}
              >
                {preset.name}
              </button>
            );
          })}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
        {/* ── Batch ─────────────────────────────────────────────── */}
        <Popover
          title="Batch"
          trigger={(open) => (
            <Readout
              label="Product"
              value={product.name.split(' (')[0]}
              unit={product.standard}
              accent={C.gold}
              open={open}
              interactive
              sub={`${num(params.volumeKL, 0, { group: true })} KL · ρ ${num(params.densityKgm3, 1)} · ${num(massTonnes, 0, { group: true })} t`}
            />
          )}
        >
          <Field label="Product">
            <Select
              value={params.product}
              options={meta.products.map((p) => ({ value: p.key, label: p.name }))}
              onChange={(key) => {
                // Density is a property of the product until the operator
                // overrides it, so switching product resets it. Leaving diesel's
                // 840 kg/m3 attached to petrol would be a silent wrong answer.
                const next = meta.products.find((p) => p.key === key);
                onChange({ product: key, densityKgm3: next?.densityRef ?? params.densityKgm3 });
              }}
            />
          </Field>
          <Field label="Dispatch volume [KL]">
            <NumberField
              value={params.volumeKL}
              min={limits.volumeMinKL}
              max={limits.volumeMaxKL}
              step={50}
              unit="KL"
              onChange={(volumeKL) => onChange({ volumeKL })}
            />
          </Field>
          <Field
            label="Density [kg/m³]"
            help="Observed density at the dispatch meter. Changing the product resets this to its reference density."
          >
            <NumberField
              value={params.densityKgm3}
              min={limits.densityMinKgm3}
              max={limits.densityMaxKgm3}
              step={0.5}
              unit="kg/m³"
              onChange={(densityKgm3) => onChange({ densityKgm3 })}
            />
          </Field>
          <div className="num rounded-lg border border-edge bg-bg/50 px-3 py-2 text-[10.5px] text-muted">
            {product.name} · typical {num(product.densityMin, 0)}–{num(product.densityMax, 0)} kg/m³ ·
            Cp {num(product.cpJkgK, 0)} J/kg·K · μ₄₀ {num(product.mu40, 2)} mPa·s
          </div>
        </Popover>

        {/* ── Temperature ───────────────────────────────────────── */}
        <Popover
          title="Dispatch temperature"
          trigger={(open) => (
            <Readout
              label="Dispatch temp"
              value={num(params.tempC, 1)}
              unit="°C"
              accent={C.gold}
              open={open}
              interactive
              sub="initial condition"
            />
          )}
        >
          <Field
            label="Fuel temperature at the refinery [°C]"
            help="Meter reading at Bina. Both the initial condition on the energy equation and, since the pipe is above ground there, half of Bina's environment temperature."
          >
            <Slider
              value={params.tempC}
              min={limits.tempMinC}
              max={limits.tempMaxC}
              step={0.5}
              decimals={1}
              unit="°C"
              accent={C.gold}
              onChange={(tempC) => onChange({ tempC })}
            />
          </Field>
        </Popover>

        {/* ── Pressure ──────────────────────────────────────────── */}
        <Popover
          title="Dispatch pressure"
          trigger={(open) => (
            <Readout
              label="Dispatch pressure"
              value={num(params.pressureBar, 0)}
              unit="bar g"
              accent={C.blue}
              open={open}
              interactive
              sub="initial condition"
            />
          )}
        >
          <Field
            label="Dispatch pressure [bar g]"
            help="Initial condition on the momentum equation. Too low and the line goes slack before Bijwasan."
          >
            <Slider
              value={params.pressureBar}
              min={limits.pressureMinBar}
              max={limits.pressureMaxBar}
              step={1}
              unit="bar g"
              accent={C.blue}
              onChange={(pressureBar) => onChange({ pressureBar })}
            />
          </Field>
        </Popover>

        {/* ── Flow ──────────────────────────────────────────────── */}
        <Popover
          title="Flow rate"
          width={360}
          trigger={(open) => (
            <Readout
              label="Flow rate"
              value={num(params.flowM3hr, 0)}
              unit="m³/hr"
              accent={C.blue}
              open={open}
              interactive
              // Only the 8" tail velocity is reported on the face: it is the
              // binding constraint, since the whole batch passes through it.
              sub={<><Hot>{num(velocityTail, 2)} m/s</Hot> in the 8″ tail</>}
            />
          )}
        >
          <Field
            label="Flow rate [m³/hr]"
            help={`Sets velocity, Reynolds number and friction. The ceiling of ${limits.flowMaxM3hr.toFixed(0)} m³/hr is ${limits.velocityCapMs.toFixed(0)} m/s in the 8″ tail, the bore that limits the line.`}
          >
            <Slider
              value={params.flowM3hr}
              min={limits.flowMinM3hr}
              max={limits.flowMaxM3hr}
              step={limits.flowStepM3hr}
              unit="m³/hr"
              accent={C.blue}
              onChange={(flowM3hr) => onChange({ flowM3hr })}
            />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <div className="rounded-lg border border-edge bg-bg/50 px-3 py-2">
              <div className="text-[9.5px] font-bold tracking-wider text-muted uppercase">
                18″ mainline
              </div>
              <div className="num text-[15px] font-bold" style={{ color: C.blue }}>
                {num(velocityMain, 2)}
                <span className="ml-1 text-[10px] font-normal text-muted">m/s</span>
              </div>
            </div>
            <div className="rounded-lg border border-edge bg-bg/50 px-3 py-2">
              <div className="text-[9.5px] font-bold tracking-wider text-muted uppercase">
                8″ tail · binds
              </div>
              <div className="num text-[15px] font-bold" style={{ color: C.violet }}>
                {num(velocityTail, 2)}
                <span className="ml-1 text-[10px] font-normal text-muted">m/s</span>
              </div>
            </div>
          </div>
          <p className="text-[10.5px] leading-relaxed text-muted">
            The tail's area is {(route.areaMainM2 / route.areaTailM2).toFixed(2)}× smaller, so it
            sets the ceiling. 3 m/s there ={' '}
            <span className="num">
              {((3 * route.areaTailM2 * 3600) / route.tailFraction).toFixed(0)} m³/hr
            </span>
            .
          </p>
        </Popover>

        {/* ── Season ────────────────────────────────────────────── */}
        <Popover
          title="Season"
          trigger={(open) => (
            <Readout
              label="Season"
              value={monthName}
              accent={C.cyan}
              open={open}
              interactive
              sub={`ERA5 soil · burial ${meta.constants.burialDepthM} m`}
            />
          )}
        >
          <Field
            label="Month"
            help="Soil temperature is a seasonal boundary condition."
          >
            <Select
              value={params.month}
              options={meta.months.map((m) => ({ value: m.value, label: m.name }))}
              onChange={(month) => onChange({ month })}
            />
          </Field>
          <div className="num rounded-lg border border-edge bg-bg/50 px-3 py-2 text-[10.5px] text-muted">
            ERA5-Land <b className="text-ink">{meta.constants.soilVar}</b> (100–289 cm) · burial{' '}
            {meta.constants.burialDepthM} m · {meta.months.length} months in the table
          </div>
        </Popover>

        {/* ── Model ─────────────────────────────────────────────── */}
        <Popover
          title="Model terms"
          width={340}
          trigger={(open) => (
            <Readout
              label="Model"
              value={`${3 - ablated.length} / 3`}
              unit="terms"
              accent={ablated.length === 0 && uncalibrated ? C.green : C.amber}
              open={open}
              interactive
              sub={
                ablated.length === 0 ? (
                  `U × ${params.uScale.toFixed(2)} · complete`
                ) : (
                  <>
                    U × {params.uScale.toFixed(2)} · <Hot>off: {ablated.join(', ')}</Hot>
                  </>
                )
              }
            />
          )}
        >
          <p className="text-[10.5px] leading-relaxed text-muted">
            Switch a term off to isolate its contribution. All on is the complete model.
          </p>
          <Toggle
            checked={params.viscousHeating}
            label="Viscous heating"
            help="Friction dissipates pump work as heat in the oil: ΔT ≈ 0.58 °C per 10 bar."
            onChange={(viscousHeating) => onChange({ viscousHeating })}
          />
          <Toggle
            checked={params.elevation}
            label="Elevation (ρ·g·dz)"
            help="100 m of elevation is ~8 bar of static head."
            onChange={(elevation) => onChange({ elevation })}
          />
          <Toggle
            checked={params.pressureCorrection}
            label="Pressure correction (CPL)"
            help="API MPMS 11.2.1. Shifts density ~0.5 % at 50 bar."
            onChange={(pressureCorrection) => onChange({ pressureCorrection })}
          />
          <Field
            label="U-value multiplier"
            help="Johansen and burial correlations are ±30 % at best. 1.00 is uncalibrated."
          >
            <Slider
              value={params.uScale}
              min={limits.uScaleMin}
              max={limits.uScaleMax}
              step={0.05}
              decimals={2}
              unit="×"
              accent={uncalibrated ? C.green : C.amber}
              onChange={(uScale) => onChange({ uScale })}
            />
          </Field>
        </Popover>
      </div>
    </section>
  );
}
