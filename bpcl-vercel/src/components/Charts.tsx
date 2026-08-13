/**
 * Charts
 * ======
 *
 * Two stacks of three panels, sharing a chainage axis and a synchronised
 * cursor: thermal + volume, and hydraulics. The pairing is not arbitrary —
 * within each stack the panels are the terms of one equation, so reading them
 * together is reading the physics.
 *
 * Three details here are load-bearing rather than stylistic:
 *
 *   1. Air is drawn as MARKERS, never as a line. There is a live air
 *      measurement at exactly two points on a 619 km route; joining them with a
 *      curve would draw an air temperature across hundreds of kilometres of
 *      buried pipe where none was measured and none is used.
 *
 *   2. The standard-volume axis is pinned to +/-1 KL around its own mean. Left
 *      to autoscale, a quantity that is invariant by construction gets rendered
 *      as a dramatic wiggle of floating-point noise — the chart would show
 *      motion where the model's strongest guarantee is that there is none.
 *
 *   3. The slack-flow region is shaded on the pressure panel. Downstream of it
 *      the single-phase equations are not valid, and the curve is drawn but
 *      marked rather than silently trusted.
 */

import { useMemo, useState } from 'react';
import {
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { Meta, SimResult } from '../api';
import { C, alpha } from '../theme';
import { num } from './ui';

interface Row {
  km: number;
  T_C: number;
  T_env_C: number;
  T_soil_C: number;
  T_air_C: number | null;
  P_bar: number;
  P_vapour_bar: number;
  rho_kgm3: number;
  mu_cP: number;
  Re: number;
  friction_factor: number;
  V_gross_KL: number;
  V_std_KL: number;
}

const AXIS = { stroke: '#243049', tickLine: false, axisLine: { stroke: '#243049' } };

function useRows(result: SimResult): Row[] {
  return useMemo(() => {
    const p = result.profile;
    return p.km.map((km, i) => ({
      km,
      T_C: p.T_C[i],
      T_env_C: p.T_env_C[i],
      T_soil_C: p.T_soil_C[i],
      T_air_C: p.T_air_C[i],
      P_bar: p.P_bar[i],
      P_vapour_bar: p.P_vapour_bar[i],
      rho_kgm3: p.rho_kgm3[i],
      mu_cP: p.mu_cP[i],
      Re: p.Re[i],
      friction_factor: p.friction_factor[i],
      V_gross_KL: p.V_gross_KL[i],
      V_std_KL: p.V_std_KL[i],
    }));
  }, [result]);
}

function ChartTooltip({
  active,
  payload,
  label,
  units,
}: {
  active?: boolean;
  payload?: readonly { name?: string; value?: number | string; color?: string }[];
  label?: string | number;
  units: Record<string, string>;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-edge bg-panel-2/97 px-3 py-2 shadow-2xl backdrop-blur-sm">
      <div className="num mb-1 text-[10px] font-bold tracking-wider text-muted">
        km {num(Number(label), 0)}
      </div>
      {payload.map((entry, i) => {
        // A null means "no measurement here" — most obviously for air, which
        // exists at two chainages only. Omitted rather than shown as a gap.
        if (entry.value == null) return null;
        return (
          <div key={i} className="flex items-baseline justify-between gap-5 text-[11px]">
            <span className="text-muted">{entry.name}</span>
            <span className="num font-semibold" style={{ color: entry.color }}>
              {num(Number(entry.value), 2)}
              <span className="ml-1 text-[9px] font-normal text-muted">
                {units[entry.name ?? ''] ?? ''}
              </span>
            </span>
          </div>
        );
      })}
    </div>
  );
}

function Panel({
  title,
  note,
  height = 218,
  children,
}: {
  title: string;
  note?: string;
  height?: number;
  children: React.ReactElement;
}) {
  return (
    <div className="panel px-3 pt-3 pb-1">
      <div className="mb-1 flex flex-wrap items-baseline gap-x-2.5 px-1">
        <span className="text-[11.5px] font-bold text-ink">{title}</span>
        {note && <span className="text-[10.5px] text-muted">{note}</span>}
      </div>
      <ResponsiveContainer width="100%" height={height}>
        {children}
      </ResponsiveContainer>
    </div>
  );
}

const LEGEND = {
  wrapperStyle: { fontSize: 10.5, paddingBottom: 2 },
  iconSize: 9,
  align: 'right' as const,
  verticalAlign: 'top' as const,
};

/* ═══════════════════════════════════════════════════════════════════
   Thermal + volume
   ═══════════════════════════════════════════════════════════════════ */

export function ThermalCharts({ result, meta }: { result: SimResult; meta: Meta }) {
  const rows = useRows(result);
  const terminals = meta.terminals.join('/');
  const airRows = useMemo(() => rows.filter((r) => r.T_air_C != null), [rows]);

  // Pin the standard-volume axis around its own mean, so an invariant reads as
  // invariant instead of as magnified noise. See the header note.
  const vStdMean = useMemo(
    () => rows.reduce((sum, r) => sum + r.V_std_KL, 0) / rows.length,
    [rows],
  );

  const units = {
    Product: '°C',
    'Environment (boundary)': '°C',
    'Soil — ERA5': '°C',
    [`Air — live, ${terminals} only`]: '°C',
    'Gross volume': 'KL',
    'Standard volume': 'KL',
  };

  return (
    <div className="space-y-3">
      <Panel
        title="Temperature"
        note="product relaxing toward the environment"
        height={248}
      >
        <ComposedChart data={rows} syncId="chainage" margin={{ top: 8, right: 16, left: 4, bottom: 4 }}>
          <CartesianGrid strokeDasharray="0" vertical={false} />
          <XAxis dataKey="km" type="number" domain={['dataMin', 'dataMax']} {...AXIS} hide />
          <YAxis
            {...AXIS}
            width={46}
            // Recharts anchors a numeric axis at zero unless told otherwise.
            // Here that wastes the bottom half of the panel on a range no
            // reading occupies, and flattens the 13 °C of relaxation that is
            // the whole point of the panel.
            domain={['auto', 'auto']}
            label={{ value: 'T [°C]', angle: -90, position: 'insideLeft', fontSize: 10, dy: 18 }}
          />
          <Tooltip content={<ChartTooltip units={units} />} />
          <Legend {...LEGEND} />
          <Line
            dataKey="T_C"
            name="Product"
            stroke={C.gold}
            strokeWidth={2.6}
            dot={false}
            isAnimationActive={false}
          />
          <Line
            dataKey="T_env_C"
            name="Environment (boundary)"
            stroke={C.cyan}
            strokeWidth={2.2}
            dot={false}
            isAnimationActive={false}
          />
          <Line
            dataKey="T_soil_C"
            name="Soil — ERA5"
            stroke={C.blue}
            strokeWidth={1.7}
            strokeDasharray="5 4"
            dot={false}
            isAnimationActive={false}
          />
          {airRows.length > 0 && (
            <Scatter
              data={airRows}
              dataKey="T_air_C"
              name={`Air — live, ${terminals} only`}
              fill={C.amber}
              shape="diamond"
              isAnimationActive={false}
            />
          )}
        </ComposedChart>
      </Panel>

      <Panel title="Gross volume" note="varies with temperature and pressure">
        <ComposedChart data={rows} syncId="chainage" margin={{ top: 8, right: 16, left: 4, bottom: 4 }}>
          <CartesianGrid strokeDasharray="0" vertical={false} />
          <XAxis dataKey="km" type="number" domain={['dataMin', 'dataMax']} {...AXIS} hide />
          <YAxis
            {...AXIS}
            width={46}
            domain={['auto', 'auto']}
            tickFormatter={(v: number) => v.toFixed(0)}
            label={{ value: 'V [KL]', angle: -90, position: 'insideLeft', fontSize: 10, dy: 16 }}
          />
          <Tooltip content={<ChartTooltip units={units} />} />
          <Legend {...LEGEND} />
          <Line
            dataKey="V_gross_KL"
            name="Gross volume"
            stroke={C.gold}
            strokeWidth={2.6}
            dot={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </Panel>

      <Panel
        title="Standard volume at 15 °C"
        note="axis fixed at ±1 KL — this line must stay flat"
      >
        <ComposedChart data={rows} syncId="chainage" margin={{ top: 8, right: 16, left: 4, bottom: 8 }}>
          <CartesianGrid strokeDasharray="0" vertical={false} />
          <XAxis
            dataKey="km"
            type="number"
            domain={['dataMin', 'dataMax']}
            {...AXIS}
            label={{
              value: 'Chainage from dispatch [km]',
              position: 'insideBottom',
              fontSize: 10,
              dy: 12,
            }}
            height={40}
          />
          <YAxis
            {...AXIS}
            width={46}
            domain={[vStdMean - 1, vStdMean + 1]}
            tickFormatter={(v: number) => v.toFixed(1)}
            label={{ value: 'V₁₅ [KL]', angle: -90, position: 'insideLeft', fontSize: 10, dy: 18 }}
          />
          <Tooltip content={<ChartTooltip units={units} />} />
          <Legend {...LEGEND} />
          <ReferenceLine y={vStdMean} stroke={alpha(C.green, 0.35)} strokeDasharray="4 4" />
          <Line
            dataKey="V_std_KL"
            name="Standard volume"
            stroke={C.green}
            strokeWidth={2.6}
            dot={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </Panel>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   Hydraulics
   ═══════════════════════════════════════════════════════════════════ */

export function HydraulicCharts({ result }: { result: SimResult }) {
  const rows = useRows(result);
  const [showDetail, setShowDetail] = useState(false);

  const lengthKm = rows[rows.length - 1].km;
  const slackFrom = result.physics.slackOnsetKm;

  const units = {
    Pressure: 'bar g',
    'Vapour pressure': 'bar',
    Density: 'kg/m³',
    Viscosity: 'cP',
    'Reynolds number': '',
    'Darcy friction factor': '',
  };

  return (
    <div className="space-y-3">
      <Panel title="Pressure" note="friction and static head" height={248}>
        <ComposedChart data={rows} syncId="hydraulics" margin={{ top: 8, right: 16, left: 4, bottom: 4 }}>
          <CartesianGrid strokeDasharray="0" vertical={false} />
          <XAxis dataKey="km" type="number" domain={['dataMin', 'dataMax']} {...AXIS} hide />
          <YAxis
            {...AXIS}
            width={46}
            label={{ value: 'P [bar g]', angle: -90, position: 'insideLeft', fontSize: 10, dy: 22 }}
          />
          <Tooltip content={<ChartTooltip units={units} />} />
          <Legend {...LEGEND} />
          {slackFrom != null && (
            <ReferenceArea
              x1={slackFrom}
              x2={lengthKm}
              fill={alpha(C.red, 0.13)}
              stroke={alpha(C.red, 0.4)}
              label={{ value: 'slack flow', fill: C.red, fontSize: 10, position: 'insideTopLeft' }}
            />
          )}
          <ReferenceLine y={0} stroke={alpha(C.muted, 0.5)} />
          <Line
            dataKey="P_bar"
            name="Pressure"
            stroke={C.blue}
            strokeWidth={2.6}
            dot={false}
            isAnimationActive={false}
          />
          <Line
            dataKey="P_vapour_bar"
            name="Vapour pressure"
            stroke={C.red}
            strokeWidth={1.6}
            strokeDasharray="2 3"
            dot={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </Panel>

      <Panel title="Density" note="rises as the product cools">
        <ComposedChart data={rows} syncId="hydraulics" margin={{ top: 8, right: 16, left: 4, bottom: 4 }}>
          <CartesianGrid strokeDasharray="0" vertical={false} />
          <XAxis dataKey="km" type="number" domain={['dataMin', 'dataMax']} {...AXIS} hide />
          <YAxis
            {...AXIS}
            width={46}
            domain={['auto', 'auto']}
            tickFormatter={(v: number) => v.toFixed(1)}
            label={{ value: 'ρ [kg/m³]', angle: -90, position: 'insideLeft', fontSize: 10, dy: 26 }}
          />
          <Tooltip content={<ChartTooltip units={units} />} />
          <Legend {...LEGEND} />
          <Line
            dataKey="rho_kgm3"
            name="Density"
            stroke={C.cyan}
            strokeWidth={2.6}
            dot={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </Panel>

      <Panel title="Viscosity" note="drives the friction factor">
        <ComposedChart data={rows} syncId="hydraulics" margin={{ top: 8, right: 16, left: 4, bottom: 8 }}>
          <CartesianGrid strokeDasharray="0" vertical={false} />
          <XAxis
            dataKey="km"
            type="number"
            domain={['dataMin', 'dataMax']}
            {...AXIS}
            label={{
              value: 'Chainage from dispatch [km]',
              position: 'insideBottom',
              fontSize: 10,
              dy: 12,
            }}
            height={40}
          />
          <YAxis
            {...AXIS}
            width={46}
            domain={['auto', 'auto']}
            tickFormatter={(v: number) => v.toFixed(2)}
            label={{ value: 'μ [cP]', angle: -90, position: 'insideLeft', fontSize: 10, dy: 18 }}
          />
          <Tooltip content={<ChartTooltip units={units} />} />
          <Legend {...LEGEND} />
          <Line
            dataKey="mu_cP"
            name="Viscosity"
            stroke={C.violet}
            strokeWidth={2.6}
            dot={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </Panel>

      <div className="panel overflow-hidden">
        <button
          type="button"
          onClick={() => setShowDetail((v) => !v)}
          aria-expanded={showDetail}
          className="flex w-full cursor-pointer items-center gap-2 px-4 py-2.5 text-left text-[11.5px] font-semibold text-ink transition-colors hover:bg-panel-2"
        >
          <span className="text-[9px] text-muted" aria-hidden>
            {showDetail ? '▼' : '▶'}
          </span>
          Reynolds number and friction factor
        </button>
        {showDetail && (
          <div className="animate-fade-up grid gap-3 px-3 pt-1 pb-3 lg:grid-cols-2">
            <ResponsiveContainer width="100%" height={190}>
              <ComposedChart data={rows} margin={{ top: 12, right: 14, left: 4, bottom: 4 }}>
                <CartesianGrid strokeDasharray="0" vertical={false} />
                <XAxis dataKey="km" type="number" domain={['dataMin', 'dataMax']} {...AXIS} />
                <YAxis
                  {...AXIS}
                  width={52}
                  tickFormatter={(v: number) => (v / 1000).toFixed(0) + 'k'}
                />
                <Tooltip content={<ChartTooltip units={units} />} />
                <Legend {...LEGEND} />
                <Line
                  dataKey="Re"
                  name="Reynolds number"
                  stroke={C.gold}
                  strokeWidth={2.2}
                  dot={false}
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
            <ResponsiveContainer width="100%" height={190}>
              <ComposedChart data={rows} margin={{ top: 12, right: 14, left: 4, bottom: 4 }}>
                <CartesianGrid strokeDasharray="0" vertical={false} />
                <XAxis dataKey="km" type="number" domain={['dataMin', 'dataMax']} {...AXIS} />
                <YAxis {...AXIS} width={52} tickFormatter={(v: number) => v.toFixed(3)} />
                <Tooltip content={<ChartTooltip units={units} />} />
                <Legend {...LEGEND} />
                <Line
                  dataKey="friction_factor"
                  name="Darcy friction factor"
                  stroke={C.violet}
                  strokeWidth={2.2}
                  dot={false}
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  );
}
