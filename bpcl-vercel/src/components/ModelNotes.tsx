/**
 * What this simulator solves
 * ===========================
 *
 * The equations are typeset by hand in HTML rather than through KaTeX or
 * MathJax. There are five of them and they are all one line; pulling in a
 * 270 KB typesetting engine to render five expressions would be most of the
 * page weight for the least-visited tab.
 *
 * `Frac` and `Sub` below are the only two constructs any of them need.
 */

import type { Meta, SimResult } from '../api';
import { C } from '../theme';
import { Callout, Section } from './ui';

function Frac({ over, under }: { over: React.ReactNode; under: React.ReactNode }) {
  return (
    <span className="mx-1 inline-flex flex-col items-center align-middle text-[0.92em] leading-tight">
      <span className="px-1.5 pb-0.5">{over}</span>
      <span className="w-full border-t border-current pt-0.5">{under}</span>
    </span>
  );
}

function V({ children }: { children: React.ReactNode }) {
  return <span className="font-serif italic">{children}</span>;
}

function Sub({ children }: { children: React.ReactNode }) {
  return <sub className="text-[0.68em] not-italic opacity-85">{children}</sub>;
}

function Eq({
  children,
  note,
  accent = C.gold,
}: {
  children: React.ReactNode;
  note?: React.ReactNode;
  accent?: string;
}) {
  return (
    <div
      className="my-3 overflow-x-auto rounded-xl border px-5 py-4"
      style={{ borderColor: `${accent}44`, background: `${accent}0d` }}
    >
      <div className="text-[15.5px] leading-loose whitespace-nowrap text-ink">{children}</div>
      {note && <div className="mt-2.5 text-[11.5px] leading-relaxed text-muted">{note}</div>}
    </div>
  );
}

function Term({ colour, children }: { colour: string; children: React.ReactNode }) {
  return <span style={{ color: colour }}>{children}</span>;
}

export function ModelNotes({ result, meta }: { result: SimResult; meta: Meta }) {
  const terminals = meta.terminals;

  return (
    <div className="max-w-4xl">
      <Section title="What this simulator solves" />

      <p className="text-[13px] leading-relaxed text-ink/90">
        The energy and momentum equations are integrated <b>together</b>, as one ODE system in the
        state <V>y</V> = [<V>T</V>, <V>P</V>]. They are coupled three ways: temperature sets
        viscosity and therefore friction, friction heats the oil, and temperature sets density and
        therefore the static head.
      </p>

      <h3 className="mt-6 mb-1 text-[11px] font-semibold tracking-[0.15em] text-muted uppercase">
        Energy
      </h3>
      <Eq
        accent={C.gold}
        note={
          <>
            <Term colour={C.cyan}>Heat exchange with the environment</Term>, plus{' '}
            <Term colour={C.violet}>viscous dissipation</Term> — roughly +0.58 °C per 10 bar of
            friction loss, or +3 to +9 °C over this line against a soil-cooling signal of 10–13 °C.
          </>
        }
      >
        <V>ṁ</V> <V>C</V>
        <Sub>p</Sub>
        <Frac
          over={
            <>
              d<V>T</V>
            </>
          }
          under={
            <>
              d<V>x</V>
            </>
          }
        />{' '}
        ={' '}
        <Term colour={C.cyan}>
          − <V>U</V>(<V>x</V>) π <V>D</V>
          <Sub>o</Sub> ( <V>T</V> − <V>T</V>
          <Sub>env</Sub>(<V>x</V>) )
        </Term>{' '}
        +{' '}
        <Term colour={C.violet}>
          <V>ṁ</V>{' '}
          <Frac
            over={
              <>
                <V>f</V> <V>u</V>²
              </>
            }
            under={
              <>
                2<V>D</V>
                <Sub>i</Sub>
              </>
            }
          />
        </Term>
      </Eq>

      <p className="text-[13px] leading-relaxed text-ink/90">
        with the environment temperature the mean of the two measured surroundings,
      </p>

      <Eq
        accent={C.cyan}
        note={
          <>
            Elevation does not appear here: potential energy trades reversibly with pressure and
            does not heat the fluid. Only the irreversible friction loss does.
          </>
        }
      >
        <V>T</V>
        <Sub>env</Sub> ={' '}
        <Frac
          over={
            <>
              <V>T</V>
              <Sub>air</Sub>
              <sup className="text-[0.68em]">current</sup> + <V>T</V>
              <Sub>soil</Sub>
            </>
          }
          under="2"
        />
      </Eq>

      <h3 className="mt-6 mb-1 text-[11px] font-semibold tracking-[0.15em] text-muted uppercase">
        Momentum
      </h3>
      <Eq
        accent={C.blue}
        note={
          <>
            <Term colour={C.blue}>Darcy–Weisbach friction</Term> plus{' '}
            <Term colour={C.green}>static head</Term>; 100 m of elevation is about 8 bar.
          </>
        }
      >
        <Frac
          over={
            <>
              d<V>P</V>
            </>
          }
          under={
            <>
              d<V>x</V>
            </>
          }
        />{' '}
        ={' '}
        <Term colour={C.blue}>
          − <V>f</V>{' '}
          <Frac
            over={
              <>
                ρ <V>u</V>²
              </>
            }
            under={
              <>
                2<V>D</V>
                <Sub>i</Sub>
              </>
            }
          />
        </Term>{' '}
        <Term colour={C.green}>
          − ρ <V>g</V>{' '}
          <Frac
            over={
              <>
                d<V>z</V>
              </>
            }
            under={
              <>
                d<V>x</V>
              </>
            }
          />
        </Term>
      </Eq>

      <h3 className="mt-6 mb-1 text-[11px] font-semibold tracking-[0.15em] text-muted uppercase">
        Volume
      </h3>
      <p className="text-[13px] leading-relaxed text-ink/90">
        The batch's <b>mass</b> is fixed, so gross volume follows the density while the standard
        volume is invariant by construction:
      </p>
      <Eq accent={C.green}>
        <V>V</V>
        <Sub>gross</Sub>(<V>x</V>) ={' '}
        <Frac
          over={<V>m</V>}
          under={
            <>
              ρ(<V>T</V>, <V>P</V>)
            </>
          }
        />
        <span className="mx-6 text-muted">·</span>
        <V>V</V>
        <Sub>15</Sub> = <V>V</V>
        <Sub>gross</Sub> · <V>C</V>
        <Sub>TL</Sub>(<V>T</V>) · <V>C</V>
        <Sub>PL</Sub>(<V>P</V>) = constant
      </Eq>

      <Callout tone={result.kpi.stdVolumeDriftPct < 0.01 ? 'ok' : 'bad'}>
        On the run currently loaded, standard volume drifts by{' '}
        <b className="num">{result.kpi.stdVolumeDriftPct.toExponential(1)} %</b> over the whole line
        while gross volume moves by <b className="num">{Math.abs(result.kpi.deltaGrossPct).toFixed(2)} %</b>.
        The product physically expands and contracts; the <i>quantity</i> of product does not change.
      </Callout>

      <Section title="Boundary conditions" />
      <div className="grid gap-3 md:grid-cols-2">
        <div className="panel p-4">
          <div className="mb-1.5 text-[10.5px] font-semibold tracking-[0.13em] uppercase" style={{ color: C.blue }}>
            Soil
          </div>
          <p className="text-[12.5px] leading-relaxed text-ink/85">
            <V>T</V>
            <Sub>soil</Sub> and θ come from ERA5-Land <b>layer 4 (100–289 cm)</b> — the layer the
            pipe actually occupies at {meta.constants.burialDepthM} m. They are{' '}
            <i>boundary conditions</i>: measured inputs, never predicted. <V>k</V>
            <Sub>soil</Sub> follows from Johansen (1975), and <V>U</V> from radial conduction through
            the steel wall and the surrounding soil.
          </p>
          <p className="mt-2 text-[12.5px] leading-relaxed text-ink/85">
            Note that <V>k</V>
            <Sub>soil</Sub> is <b>not</b> averaged with anything — the conduction path out of the
            pipe is through soil, so the soil's own conductivity still sets <V>U</V>. Only the{' '}
            <i>sink temperature</i> is the air/soil mean.
          </p>
        </div>

        <div className="panel p-4">
          <div className="mb-1.5 text-[10.5px] font-semibold tracking-[0.13em] uppercase" style={{ color: C.amber }}>
            Air
          </div>
          <p className="text-[12.5px] leading-relaxed text-ink/85">
            <V>T</V>
            <Sub>air</Sub> is the <b>current observed</b> temperature at the two terminals —{' '}
            {terminals.map((name, i) => (
              <span key={name}>
                {i > 0 && ' and '}
                <b>{name}</b>
              </span>
            ))}{' '}
            — queried at each terminal's own coordinates.
            Those are the only two points where the line surfaces, so they are the only two where air
            belongs in the boundary condition and the only two that are queried at all.
          </p>
          <p className="mt-2 text-[12.5px] leading-relaxed text-ink/85">
            Never a forecast, never modelled, never interpolated in time. If a terminal's request
            fails it is <b>reported as failed</b> and falls back to soil alone — no air temperature
            is ever substituted for it.
          </p>
        </div>
      </div>

      <Callout tone="info" className="mt-3">
        <b>Why Bina is different.</b> The product leaves the refinery in <b>surface pipework</b> — not
        yet buried, so the soil is not in contact with it at all. Its environment is the open air and
        the product it carries, hence <span className="num">T_env = (T_air + T_fuel)/2</span>, reported
        as basis <span className="num">air+fuel</span>. <V>T</V>
        <Sub>fuel</Sub> is a meter measurement entered by the operator, not a solved quantity, which
        is what makes it admissible in a boundary condition. This applies at the refinery only —
        anywhere downstream the product temperature is solved, and using it would be circular.
      </Callout>

      <Section title="The 8″ tail sets the throughput" />
      <p className="text-[13px] leading-relaxed text-ink/90">
        The whole batch passes through both bores, and the tail's area is{' '}
        <b className="num">{(meta.route.areaMainM2 / meta.route.areaTailM2).toFixed(2)}×</b> smaller,
        so it runs that much faster than the mainline. The tail therefore binds, and the 18″ mainline
        is correspondingly under-utilised. That is what a trunk line narrowing for its final approach
        into a city terminal does — it is not a modelling artefact, and the fix is not to invent a
        mid-route delivery.
      </p>
      <div className="panel num mt-3 overflow-x-auto p-4 text-[12px] leading-relaxed">
        <div>
          <span style={{ color: C.gold }}>{result.inputs.flowM3hr.toFixed(0)} m³/hr</span>
          {'  →  '}
          <span style={{ color: C.blue }}>{result.inputs.velocityMainMs.toFixed(2)} m/s</span> mainline,{' '}
          <span style={{ color: C.violet }}>{result.inputs.velocityTailMs.toFixed(2)} m/s</span> tail
          {'   '}
          <span className="text-muted">← the run currently loaded</span>
        </div>
      </div>

      <Section title="Sources" />
      <ul className="space-y-1.5 text-[12.5px] text-ink/85">
        {[
          ['Çengel', 'Heat Transfer: A Practical Approach, Ch. 3 — buried-cylinder conduction'],
          ['Colebrook (1939); Swamee & Jain (1976); Moody (1944)', 'friction factor'],
          ['Johansen (1975)', 'soil thermal conductivity from moisture'],
          ['API MPMS Ch. 11.1 (CTL) and Ch. 11.2.1 (CPL)', 'volume correction'],
          ['ERA5-Land reanalysis (ECMWF/Copernicus)', 'soil temperature and moisture'],
          ['Dormand & Prince (1980); Hairer, Nørsett & Wanner', 'the RK45 integrator'],
        ].map(([source, what]) => (
          <li key={source} className="flex gap-2.5">
            <span style={{ color: C.gold }} aria-hidden>
              ·
            </span>
            <span>
              <b>{source}</b> — {what}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
