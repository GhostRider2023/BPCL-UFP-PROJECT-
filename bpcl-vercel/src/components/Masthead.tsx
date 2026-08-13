/**
 * Masthead — identity, the run's summary line, and run status.
 *
 * The pills exist so an ablated or uncalibrated run cannot be mistaken for a
 * complete one at a glance. That is why "PHYSICS ABLATED" is red rather than
 * neutral: a model missing its viscous-heating term still produces plausible
 * numbers, and plausible-but-wrong is the state worth flagging loudly.
 */

import type { Meta, SimResult } from '../api';
import { Pill } from './ui';

export function Masthead({
  result,
  meta,
  pending,
}: {
  result: SimResult | null;
  meta: Meta | null;
  pending: boolean;
}) {
  const terminals = meta?.terminals.length ?? 2;
  const withAir = result?.physics.stationsWithAir ?? 0;

  return (
    <header className="panel relative mb-5 overflow-hidden">
      {pending && (
        <div className="absolute inset-x-0 top-0 h-[2px] overflow-hidden">
          <div className="animate-sweep h-full w-1/3 bg-gradient-to-r from-transparent via-gold to-transparent" />
        </div>
      )}

      <div className="flex flex-wrap items-center gap-x-5 gap-y-3 px-5 py-4">
        <img
          src="/bpcl-logo.png"
          alt="Bharat Petroleum"
          className="h-11 w-auto shrink-0 rounded-md bg-white px-1.5 py-1"
        />
        <div className="hidden h-10 w-px bg-edge sm:block" />

        <div className="min-w-0 flex-1">
          <h1 className="text-[16px] leading-tight font-semibold tracking-[-0.01em] text-ink">
            Thermal-Hydraulic Simulator
          </h1>
          <p className="num mt-1.5 truncate text-[11px] text-muted">
            {result && meta ? (
              <>
                {meta.route.originName.split(' (')[0]} → {meta.route.terminusName.split(' (')[0]}
                <Sep />
                {meta.route.lengthKm.toFixed(0)} km
                <Sep />
                {result.inputs.productName}
                <Sep />
                {result.inputs.volumeKL.toLocaleString('en-US')} KL at{' '}
                {result.inputs.tempC.toFixed(1)} °C
                <Sep />
                {result.inputs.flowM3hr.toFixed(0)} m³/hr
              </>
            ) : (
              'Loading route geometry and soil boundary condition'
            )}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {result ? (
            <>
              {result.physics.feasible ? (
                <Pill tone="ok">Hydraulics OK</Pill>
              ) : (
                <Pill tone="bad" pulse>
                  Slack flow
                </Pill>
              )}

              <Pill tone="info">
                ERA5 {meta?.constants.soilVar.toUpperCase()} · {result.inputs.monthName}
              </Pill>

              {withAir > 0 ? (
                <Pill tone={withAir === terminals ? 'ok' : 'bad'}>
                  Live air {withAir}/{terminals}
                </Pill>
              ) : (
                <Pill tone="bad">Soil only</Pill>
              )}

              {result.inputs.ablated.length > 0 && (
                <Pill tone="bad">Ablated: {result.inputs.ablated.join(', ')}</Pill>
              )}

              {Math.abs(result.inputs.uScale - 1) > 1e-9 && (
                <Pill tone="info">U × {result.inputs.uScale.toFixed(2)}</Pill>
              )}

              <span className="num ml-1 text-[9.5px] text-muted" title="Server-side solve time">
                {result.elapsedMs.toFixed(0)} ms
              </span>
            </>
          ) : (
            <Pill tone="info" pulse>
              Starting
            </Pill>
          )}
        </div>
      </div>
    </header>
  );
}

function Sep() {
  return <span className="mx-2 text-edge">|</span>;
}
