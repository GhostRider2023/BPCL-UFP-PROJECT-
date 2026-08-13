/**
 * Pipeline mimic — the SCADA-style schematic
 * ===========================================
 *
 * The route as a horizontal pipe whose colour IS the product temperature, with
 * station nodes above it and the terrain profile below.
 *
 * Design intent, carried over from the Streamlit build: colour carries the
 * primary variable, so the thermal state of the whole 619 km is legible at a
 * glance without consulting a chart. Terrain sits underneath because elevation
 * is what drives the static-head term in the momentum equation — the two belong
 * on the same axis, and putting them on separate ones would hide the fact that
 * the pressure kinks where the ground does.
 *
 * Drawn as inline SVG rather than through the chart library. The pipe is a run
 * of ~140 abutting rectangles, each filled with its own segment's mean
 * temperature; no charting API expresses that well, and hand-drawing it costs
 * less than fighting one that does not.
 */

import { useMemo, useRef, useState } from 'react';
import type { Meta, SimResult } from '../api';
import { C, alpha, sampleTempScale } from '../theme';
import { num } from './ui';
import { useElementWidth } from '../hooks/useElementWidth';

const HEIGHT = 300;
const PAD = { top: 46, right: 22, bottom: 30, left: 52 };
const PIPE_Y = 96; // centreline of the pipe band
const PIPE_H = 26;
const TERRAIN_TOP = 168;
const TERRAIN_BOTTOM = HEIGHT - PAD.bottom;
const SEGMENTS = 140;

export function Mimic({ result, meta }: { result: SimResult; meta: Meta }) {
  const host = useRef<HTMLDivElement>(null);
  const width = useElementWidth(host, 1180);
  const [hoverKm, setHoverKm] = useState<number | null>(null);

  const { profile } = result;
  const lengthKm = profile.km[profile.km.length - 1];
  const plotW = Math.max(width - PAD.left - PAD.right, 120);

  const x = (km: number) => PAD.left + (km / lengthKm) * plotW;

  // Temperature is normalised against the full span shown — product AND soil —
  // so the colour ramp stays readable even when the batch barely moves.
  const { lo, span } = useMemo(() => {
    const all = [...profile.T_C, ...profile.T_soil_C];
    const min = Math.min(...all) - 0.5;
    const max = Math.max(...all) + 0.5;
    return { lo: min, span: Math.max(max - min, 1e-6) };
  }, [profile]);

  /** Pipe segments, each the mean temperature over its own stretch of route. */
  const segments = useMemo(() => {
    const out: { x0: number; x1: number; fill: string; t: number }[] = [];
    for (let i = 0; i < SEGMENTS; i += 1) {
      const km0 = (i / SEGMENTS) * lengthKm;
      const km1 = ((i + 1) / SEGMENTS) * lengthKm;
      let sum = 0;
      let n = 0;
      for (let j = 0; j < profile.km.length; j += 1) {
        if (profile.km[j] >= km0 && profile.km[j] <= km1) {
          sum += profile.T_C[j];
          n += 1;
        }
      }
      const t = n ? sum / n : profile.T_C[0];
      out.push({ x0: x(km0), x1: x(km1), fill: sampleTempScale((t - lo) / span), t });
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile, lo, span, plotW, lengthKm]);

  /** Terrain profile, as an area path. */
  const terrain = useMemo(() => {
    const elevations = profile.elevation_m;
    const minE = Math.min(...elevations);
    const maxE = Math.max(...elevations);
    const range = Math.max(maxE - minE, 1);
    const y = (e: number) =>
      TERRAIN_BOTTOM - ((e - minE) / range) * (TERRAIN_BOTTOM - TERRAIN_TOP);

    const line = profile.km
      .map((km, i) => `${i === 0 ? 'M' : 'L'}${x(km).toFixed(1)},${y(elevations[i]).toFixed(1)}`)
      .join('');
    const area = `${line}L${x(lengthKm).toFixed(1)},${TERRAIN_BOTTOM}L${PAD.left},${TERRAIN_BOTTOM}Z`;
    return { line, area, minE, maxE, y };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile, plotW, lengthKm]);

  const boosters = meta.pumpStations.filter((s) => s.km > 0 && s.km < lengthKm);

  // Station labels are staggered onto two rows: at 619 km with nine waypoints,
  // one row collides around Piyala/Bijwasan, which sit 20 km apart.
  const stations = result.stations.map((s, i) => ({
    ...s,
    row: i % 2,
    label: s.waypoint_name.split(' (')[0],
  }));

  const hover = useMemo(() => {
    if (hoverKm == null) return null;
    let best = 0;
    for (let i = 1; i < profile.km.length; i += 1) {
      if (Math.abs(profile.km[i] - hoverKm) < Math.abs(profile.km[best] - hoverKm)) best = i;
    }
    return { i: best, km: profile.km[best] };
  }, [hoverKm, profile]);

  const onMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * width;
    const km = ((px - PAD.left) / plotW) * lengthKm;
    setHoverKm(km >= 0 && km <= lengthKm ? km : null);
  };

  const slackFrom = result.physics.slackOnsetKm;

  return (
    <div ref={host} className="panel relative overflow-hidden px-1 py-1">
      <svg
        width="100%"
        height={HEIGHT}
        viewBox={`0 0 ${width} ${HEIGHT}`}
        onMouseMove={onMove}
        onMouseLeave={() => setHoverKm(null)}
        role="img"
        aria-label={`Pipeline mimic: product temperature along ${lengthKm.toFixed(0)} km, with terrain profile`}
        style={{ display: 'block' }}
      >
        <defs>
          <linearGradient id="terrain-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={alpha(C.cyan, 0.3)} />
            <stop offset="100%" stopColor={alpha(C.cyan, 0.02)} />
          </linearGradient>
          <linearGradient id="pipe-gloss" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgba(255,255,255,0.28)" />
            <stop offset="42%" stopColor="rgba(255,255,255,0.02)" />
            <stop offset="100%" stopColor="rgba(0,0,0,0.32)" />
          </linearGradient>
          <clipPath id="pipe-clip">
            <rect
              x={PAD.left}
              y={PIPE_Y - PIPE_H / 2}
              width={plotW}
              height={PIPE_H}
              rx={PIPE_H / 2}
            />
          </clipPath>
        </defs>

        {/* ── The pipe ──────────────────────────────────────────── */}
        <g clipPath="url(#pipe-clip)">
          {segments.map((seg, i) => (
            <rect
              key={i}
              x={seg.x0}
              y={PIPE_Y - PIPE_H / 2}
              width={seg.x1 - seg.x0 + 0.7}
              height={PIPE_H}
              fill={seg.fill}
            />
          ))}
          <rect
            x={PAD.left}
            y={PIPE_Y - PIPE_H / 2}
            width={plotW}
            height={PIPE_H}
            fill="url(#pipe-gloss)"
          />
        </g>
        <rect
          x={PAD.left}
          y={PIPE_Y - PIPE_H / 2}
          width={plotW}
          height={PIPE_H}
          rx={PIPE_H / 2}
          fill="none"
          stroke={alpha(C.text, 0.22)}
          strokeWidth={1}
        />

        {/* Slack-flow overlay: where the liquid column would part, the
            single-phase model below is not valid, and the pipe says so. */}
        {slackFrom != null && (
          <g>
            <rect
              x={x(slackFrom)}
              y={PIPE_Y - PIPE_H / 2 - 5}
              width={x(lengthKm) - x(slackFrom)}
              height={PIPE_H + 10}
              fill={alpha(C.red, 0.2)}
              stroke={C.red}
              strokeWidth={1}
              strokeDasharray="4 3"
              rx={4}
            />
            {/* Slack often begins in the last few kilometres, where a label
                anchored at the start of the region runs off the right edge and
                gets clipped to nonsense. Past the midpoint it flips inward. */}
            {(() => {
              const late = x(slackFrom) > PAD.left + plotW * 0.55;
              return (
                <text
                  x={late ? x(slackFrom) - 6 : x(slackFrom) + 6}
                  // One line below the bore-change marker, which sits at km 599
                  // and would otherwise share this row whenever slack begins in
                  // the tail — which is exactly when it usually does.
                  y={PIPE_Y + PIPE_H / 2 + 33}
                  fill={C.red}
                  fontSize={9.5}
                  fontWeight={700}
                  textAnchor={late ? 'end' : 'start'}
                  className="num"
                >
                  SLACK FLOW FROM km {slackFrom.toFixed(0)}
                </text>
              );
            })()}
          </g>
        )}

        {/* ── The 8" tail, marked where the bore narrows ────────── */}
        {(() => {
          const tailStart = meta.route.waypoints.find((w) => w.odInch < 12)?.km;
          if (tailStart == null) return null;
          return (
            <g>
              <line
                x1={x(tailStart)}
                y1={PIPE_Y - PIPE_H / 2 - 8}
                x2={x(tailStart)}
                y2={PIPE_Y + PIPE_H / 2 + 8}
                stroke={C.violet}
                strokeWidth={1.5}
                strokeDasharray="3 2"
              />
              <text
                x={x(tailStart) - 4}
                y={PIPE_Y + PIPE_H / 2 + 20}
                fill={C.violet}
                fontSize={9}
                fontWeight={600}
                textAnchor="end"
                className="num"
              >
                8″ tail →
              </text>
            </g>
          );
        })()}

        {/* ── Booster stations ──────────────────────────────────── */}
        {boosters.map((station) => {
          const result_station = result.physics.pumpStations.find((p) => p.name === station.name);
          const cavitating = result_station ? !result_station.suction_ok : false;
          const idle = result_station?.idle ?? false;
          const colour = cavitating ? C.red : idle ? C.muted : C.blue;
          return (
            <g key={station.name}>
              <line
                x1={x(station.km)}
                y1={PIPE_Y - PIPE_H / 2}
                x2={x(station.km)}
                y2={PIPE_Y - PIPE_H / 2 - 11}
                stroke={colour}
                strokeWidth={1.4}
              />
              <polygon
                points={`${x(station.km)},${PIPE_Y - PIPE_H / 2 - 18} ${x(station.km) - 4.6},${PIPE_Y - PIPE_H / 2 - 10} ${x(station.km) + 4.6},${PIPE_Y - PIPE_H / 2 - 10}`}
                fill={colour}
              />
            </g>
          );
        })}

        {/* ── Station nodes and labels ──────────────────────────── */}
        {stations.map((station) => {
          const sx = x(station.km);
          const labelY = station.row === 0 ? 20 : 38;
          const terminal = station.T_air_C != null;
          return (
            <g key={station.waypoint_name}>
              <line
                x1={sx}
                y1={labelY + 5}
                x2={sx}
                y2={PIPE_Y - PIPE_H / 2 - 2}
                stroke={alpha(terminal ? C.gold : C.cyan, 0.42)}
                strokeWidth={1}
              />
              <circle
                cx={sx}
                cy={PIPE_Y}
                r={terminal ? 5.5 : 4}
                fill={terminal ? C.gold : C.cyan}
                stroke={C.bg}
                strokeWidth={1.6}
              />
              <text
                x={sx}
                y={labelY}
                fill={terminal ? C.gold : C.muted}
                fontSize={9.5}
                fontWeight={terminal ? 700 : 600}
                textAnchor={station.km < lengthKm * 0.06 ? 'start' : station.km > lengthKm * 0.94 ? 'end' : 'middle'}
              >
                {station.label}
              </text>
            </g>
          );
        })}

        {/* ── Terrain ───────────────────────────────────────────── */}
        <path d={terrain.area} fill="url(#terrain-fill)" />
        <path d={terrain.line} fill="none" stroke={C.cyan} strokeWidth={1.6} />

        <text x={PAD.left - 8} y={TERRAIN_TOP + 4} fill={C.muted} fontSize={9} textAnchor="end" className="num">
          {terrain.maxE.toFixed(0)}
        </text>
        <text x={PAD.left - 8} y={TERRAIN_BOTTOM} fill={C.muted} fontSize={9} textAnchor="end" className="num">
          {terrain.minE.toFixed(0)}
        </text>
        <text
          x={PAD.left - 8}
          y={(TERRAIN_TOP + TERRAIN_BOTTOM) / 2 + 3}
          fill={C.muted}
          fontSize={8.5}
          textAnchor="end"
        >
          m
        </text>

        {/* ── Chainage axis ─────────────────────────────────────── */}
        {/* The unit sits to the LEFT of the first tick, matching the "m" on the
            terrain axis. Putting it at the right edge collides with the final
            tick, which is always the full route length and always flush right. */}
        <text x={PAD.left - 8} y={HEIGHT - 9} fill={C.muted} fontSize={8.5} textAnchor="end">
          km
        </text>
        {Array.from({ length: 8 }, (_, i) => (i / 7) * lengthKm).map((km, i) => (
          <text
            key={km}
            x={x(km)}
            y={HEIGHT - 9}
            fill={C.muted}
            fontSize={9}
            textAnchor={i === 0 ? 'start' : i === 7 ? 'end' : 'middle'}
            className="num"
          >
            {km.toFixed(0)}
          </text>
        ))}

        {/* ── Hover crosshair ───────────────────────────────────── */}
        {hover && (
          <line
            x1={x(hover.km)}
            y1={PIPE_Y - PIPE_H / 2 - 22}
            x2={x(hover.km)}
            y2={TERRAIN_BOTTOM}
            stroke={alpha(C.text, 0.42)}
            strokeWidth={1}
            strokeDasharray="3 3"
            pointerEvents="none"
          />
        )}
      </svg>

      {hover && (
        <div
          className="pointer-events-none absolute z-10 rounded-lg border border-edge bg-panel-2/97 px-3 py-2 text-[11px] shadow-2xl backdrop-blur-sm"
          style={{
            left: Math.min(Math.max(x(hover.km) + 12, 8), width - 190),
            top: 8,
            minWidth: 168,
          }}
        >
          <div className="num mb-1 text-[10px] font-bold tracking-wider text-muted">
            km {num(hover.km, 0)}
            {profile.slack_flow[hover.i] && <span style={{ color: C.red }}> · SLACK</span>}
          </div>
          <Row label="Product" value={`${num(profile.T_C[hover.i], 2)} °C`} colour={C.gold} />
          <Row label="Environment" value={`${num(profile.T_env_C[hover.i], 2)} °C`} colour={C.cyan} />
          <Row label="Pressure" value={`${num(profile.P_bar[hover.i], 1)} bar`} colour={C.blue} />
          <Row
            label="Elevation"
            value={`${num(profile.elevation_m[hover.i], 0)} m`}
            colour={C.muted}
          />
        </div>
      )}

      {/* ── Legend ────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 pt-1 pb-2.5">
        <div className="flex items-center gap-2">
          <span className="text-[9.5px] font-semibold tracking-wider text-muted uppercase">
            {num(lo, 0)} °C
          </span>
          <div
            className="h-2 w-36 rounded-full"
            style={{
              background: `linear-gradient(90deg, ${sampleTempScale(0)}, ${sampleTempScale(0.25)}, ${sampleTempScale(0.5)}, ${sampleTempScale(0.7)}, ${sampleTempScale(1)})`,
            }}
          />
          <span className="text-[9.5px] font-semibold tracking-wider text-muted uppercase">
            {num(lo + span, 0)} °C
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-muted">
          <Key colour={C.gold} shape="dot">
            terminal
          </Key>
          <Key colour={C.cyan} shape="dot">
            station
          </Key>
          <Key colour={C.blue} shape="tri">
            booster
          </Key>
          <Key colour={C.cyan} shape="line">
            terrain
          </Key>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value, colour }: { label: string; value: string; colour: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <span className="text-muted">{label}</span>
      <span className="num font-semibold" style={{ color: colour }}>
        {value}
      </span>
    </div>
  );
}

function Key({
  colour,
  shape,
  children,
}: {
  colour: string;
  shape: 'dot' | 'tri' | 'line';
  children: React.ReactNode;
}) {
  return (
    <span className="flex items-center gap-1.5">
      {shape === 'dot' && (
        <span className="h-2 w-2 rounded-full" style={{ background: colour }} aria-hidden />
      )}
      {shape === 'tri' && (
        <span
          aria-hidden
          style={{
            width: 0,
            height: 0,
            borderLeft: '4px solid transparent',
            borderRight: '4px solid transparent',
            borderBottom: `7px solid ${colour}`,
          }}
        />
      )}
      {shape === 'line' && (
        <span className="h-[2px] w-4 rounded-full" style={{ background: colour }} aria-hidden />
      )}
      {children}
    </span>
  );
}
