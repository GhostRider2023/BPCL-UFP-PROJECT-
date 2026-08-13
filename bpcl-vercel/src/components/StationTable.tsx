/**
 * Station report
 * ==============
 *
 * The state at each named point on the route, plus the booster-station log.
 *
 * Column order in the main table is deliberate: air, soil, environment,
 * product, left to right. That is the causal chain — two measured surroundings,
 * their mean, and the fluid temperature solved from using that mean as the
 * boundary condition — and it reads across the row.
 *
 * `Air` is blank at every station except the two terminals. That is not missing
 * data; it is the model saying air is not used there, because the pipe is buried
 * at 1.2 m where the daily air signal does not reach. A blank cell is the
 * correct rendering, and it must never be filled with a plausible number.
 */

import type { Meta, SimResult } from '../api';
import { C, alpha } from '../theme';
import { Callout, Section, num } from './ui';

function basisChip(basis: string) {
  const map: Record<string, { fg: string; title: string }> = {
    'air+fuel': {
      fg: C.gold,
      title: 'Above-ground pipework at the refinery: (air + dispatch fuel) / 2',
    },
    'air+soil': { fg: C.amber, title: 'Terminal where the line surfaces: (air + soil) / 2' },
    soil: { fg: C.cyan, title: 'Buried at 1.2 m: the ERA5 soil temperature alone' },
  };
  const style = map[basis] ?? { fg: C.muted, title: basis };
  return (
    <span
      title={style.title}
      className="num rounded px-1.5 py-0.5 text-[9.5px] font-medium whitespace-nowrap"
      style={{ color: style.fg, background: alpha(style.fg, 0.13) }}
    >
      {basis || '—'}
    </span>
  );
}

const HEAD =
  'sticky top-0 z-10 bg-panel px-2.5 py-2.5 text-[9.5px] font-semibold tracking-[0.08em] text-muted uppercase whitespace-nowrap';
const CELL = 'num px-2.5 py-1.5 text-[11.5px] whitespace-nowrap';

export function StationTable({
  result,
  meta,
  onExport,
  exporting,
}: {
  result: SimResult;
  meta: Meta;
  onExport: () => void;
  exporting: boolean;
}) {
  const terminals = meta.terminals.join(' and ');
  const pumps = result.physics.pumpStations;
  const cavitating = result.physics.cavitatingStations;

  return (
    <div>
      {pumps.length > 0 && (
        <>
          <Section title="Booster pump stations" note="discharge setting 100 bar" />
          <div className="panel overflow-x-auto">
            <table className="w-full border-collapse text-left">
              <thead>
                <tr className="border-b border-edge">
                  <th className={HEAD}>Station</th>
                  <th className={`${HEAD} text-right`}>km</th>
                  <th className={`${HEAD} text-right`}>Suction [bar]</th>
                  <th className={`${HEAD} text-right`}>Discharge [bar]</th>
                  <th className={`${HEAD} text-right`}>Boost [bar]</th>
                  <th className={HEAD}>State</th>
                </tr>
              </thead>
              <tbody>
                {pumps.map((pump) => (
                  <tr
                    key={pump.name}
                    className="border-b border-edge/40 transition-colors last:border-0 hover:bg-panel-2"
                  >
                    <td className={`${CELL} font-semibold text-ink`}>{pump.name}</td>
                    <td className={`${CELL} text-right text-muted`}>{num(pump.km, 0)}</td>
                    <td
                      className={`${CELL} text-right`}
                      style={{ color: pump.suction_ok ? C.text : C.red }}
                    >
                      {num(pump.P_suction_bar, 2)}
                    </td>
                    <td className={`${CELL} text-right`} style={{ color: C.blue }}>
                      {num(pump.P_discharge_bar, 2)}
                    </td>
                    <td className={`${CELL} text-right`} style={{ color: C.green }}>
                      {num(pump.boost_bar, 2, { sign: true })}
                    </td>
                    <td className={CELL}>
                      {!pump.suction_ok ? (
                        <span style={{ color: C.red }}>cavitating</span>
                      ) : pump.idle ? (
                        <span className="text-muted" title="Arrived above the discharge setting; a booster raises pressure but never lowers it">
                          idle
                        </span>
                      ) : (
                        <span style={{ color: C.green }}>boosting</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {cavitating.length > 0 && (
            <Callout tone="bad" className="mt-3">
              <b>Inadequate suction at {cavitating.join(', ')}.</b> Product arrives below the{' '}
              {num(meta.limits.minSuctionBar, 1)} bar minimum, so the pump would cavitate rather than
              boost.
            </Callout>
          )}
        </>
      )}

      <Section title="State at each station" />
      <div className="panel overflow-x-auto">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-edge">
              <th className={`${HEAD} text-right`}>km</th>
              <th className={HEAD}>Station</th>
              <th className={`${HEAD} text-right`}>Elev [m]</th>
              <th className={`${HEAD} text-right`} style={{ color: C.amber }}>
                Air [°C]
              </th>
              <th className={`${HEAD} text-right`} style={{ color: C.blue }}>
                Soil [°C]
              </th>
              <th className={`${HEAD} text-right`} style={{ color: C.cyan }}>
                Env [°C]
              </th>
              <th className={HEAD}>Env basis</th>
              <th className={`${HEAD} text-right`} style={{ color: C.gold }}>
                Product [°C]
              </th>
              <th className={`${HEAD} text-right`}>P [bar]</th>
              <th className={`${HEAD} text-right`}>ρ [kg/m³]</th>
              <th className={`${HEAD} text-right`}>μ [cP]</th>
              <th className={`${HEAD} text-right`}>Re</th>
              <th className={`${HEAD} text-right`}>Gross V [KL]</th>
              <th className={`${HEAD} text-right`}>V₁₅ [KL]</th>
            </tr>
          </thead>
          <tbody>
            {result.stations.map((station) => (
              <tr
                key={station.waypoint_name}
                className="border-b border-edge/40 transition-colors last:border-0 hover:bg-panel-2"
              >
                <td className={`${CELL} text-right text-muted`}>{num(station.km, 0)}</td>
                <td className={`${CELL} font-semibold text-ink`}>{station.waypoint_name}</td>
                <td className={`${CELL} text-right text-muted`}>{num(station.elevation_m, 0)}</td>
                <td
                  className={`${CELL} text-right`}
                  style={{ color: station.T_air_C == null ? C.muted : C.amber }}
                >
                  {num(station.T_air_C, 2)}
                </td>
                <td className={`${CELL} text-right`} style={{ color: C.blue }}>
                  {num(station.T_soil_C, 2)}
                </td>
                <td className={`${CELL} text-right`} style={{ color: C.cyan }}>
                  {num(station.T_env_C, 2)}
                </td>
                <td className={CELL}>{basisChip(station.T_env_basis)}</td>
                <td className={`${CELL} text-right font-semibold`} style={{ color: C.gold }}>
                  {num(station.T_C, 2)}
                </td>
                <td className={`${CELL} text-right`}>{num(station.P_bar, 2)}</td>
                <td className={`${CELL} text-right`}>{num(station.rho_kgm3, 2)}</td>
                <td className={`${CELL} text-right`}>{num(station.mu_cP, 3)}</td>
                <td className={`${CELL} text-right text-muted`}>
                  {num(station.Re, 0, { group: true })}
                </td>
                <td className={`${CELL} text-right`}>{num(station.V_gross_KL, 2)}</td>
                <td className={`${CELL} text-right`} style={{ color: C.green }}>
                  {num(station.V_std_KL, 3)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="mt-3 text-[10.5px] leading-relaxed text-muted">
        Air is measured at {terminals} only and is blank elsewhere, because air is not used
        elsewhere. Soil is ERA5-Land{' '}
        <code className="num">{meta.constants.soilVar}</code> at {meta.constants.burialDepthM} m
        burial. Env is the boundary temperature the energy equation drives toward; Env basis says how
        it was formed. Product is the solved fluid temperature.
      </p>

      <button
        type="button"
        onClick={onExport}
        disabled={exporting}
        className="mt-4 inline-flex cursor-pointer items-center gap-2.5 rounded-lg border border-edge bg-panel px-4 py-2.5 text-[12px] font-medium text-ink transition-colors hover:border-gold/50 disabled:cursor-wait disabled:opacity-60"
      >
        <svg
          aria-hidden
          viewBox="0 0 12 12"
          className="h-3 w-3 shrink-0"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M6 1v7m0 0L3.5 5.5M6 8l2.5-2.5M1.5 10.5h9" />
        </svg>
        {exporting ? 'Preparing export' : 'Download full state table'}
        <span className="text-[10.5px] font-normal text-muted">
          {result.profile.km.length} chainages, all columns, CSV
        </span>
      </button>
    </div>
  );
}
