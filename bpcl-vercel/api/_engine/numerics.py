"""
Numerics — the two SciPy routines this project uses, in NumPy alone
===================================================================

`model/kernel.py` and `model/heat_transfer.py` need exactly two things from
SciPy: an adaptive ODE integrator with dense output, and a 1-D interpolator that
extrapolates linearly outside its knots. Everything else in SciPy's 118 MB is
dead weight.

That size is not an aesthetic complaint. A Vercel serverless function has a
250 MB unzipped budget, and NumPy (33 MB) + pandas (65 MB) + SciPy (118 MB)
overruns it before a single line of this project's own code is added. Dropping
SciPy takes the bundle to ~100 MB and cuts several seconds off every cold start,
because SciPy's import graph is enormous and is paid on every cold invocation.

What is implemented
-------------------
`solve_ivp`
    Explicit Runge-Kutta 5(4), Dormand-Prince coefficients — the same tableau as
    `scipy.integrate.RK45`, including the same 4th-order dense-output
    interpolant (Shampine 1986, as tabulated in SciPy's `RK45.P`). Adaptive step
    with the standard PI-free error controller, `max_step` clamp, and scalar or
    per-component `rtol`/`atol`.

`interp1d`
    `kind="linear"` and `kind="nearest"`, with `fill_value="extrapolate"`. Both
    are used by this project; nothing else is.

Is RK45 the right substitute for LSODA?
---------------------------------------
LSODA switches between an Adams method (non-stiff) and BDF (stiff) based on a
running stiffness estimate. This system is not stiff and LSODA runs it in Adams
mode throughout: the temperature equation is a smooth relaxation with a
characteristic length of hundreds of kilometres, the pressure equation is a
smooth quadrature, and the only discontinuities on the route — booster stations
and flow splits — are already handled by breaking the integration at those
chainages rather than by stepping over them. An explicit RK45 at the same
tolerances is therefore solving the same problem the same way.

That argument is not taken on faith. `validate_against_scipy.py` runs the full
`simulate()` over a grid of products, months, flows, temperatures and pressures
against the SciPy build and asserts agreement to well below the precision the
results are reported at. Run it whenever this file changes.

Sources
-------
  Dormand & Prince (1980), "A family of embedded Runge-Kutta formulae".
  Shampine (1986), "Some practical Runge-Kutta formulas" — dense output.
  Hairer, Norsett & Wanner, "Solving Ordinary Differential Equations I", II.4.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right

import numpy as np

__all__ = ["solve_ivp", "interp1d", "OdeResult"]


# ═══════════════════════════════════════════════════════════════════
# Dormand-Prince 5(4) tableau
# ═══════════════════════════════════════════════════════════════════

_C = np.array([0.0, 1 / 5, 3 / 10, 4 / 5, 8 / 9, 1.0])

_A = np.array(
    [
        [0.0, 0.0, 0.0, 0.0, 0.0],
        [1 / 5, 0.0, 0.0, 0.0, 0.0],
        [3 / 40, 9 / 40, 0.0, 0.0, 0.0],
        [44 / 45, -56 / 15, 32 / 9, 0.0, 0.0],
        [19372 / 6561, -25360 / 2187, 64448 / 6561, -212 / 729, 0.0],
        [9017 / 3168, -355 / 33, 46732 / 5247, 49 / 176, -5103 / 18656],
    ]
)

# 5th-order solution weights.
_B = np.array([35 / 384, 0.0, 500 / 1113, 125 / 192, -2187 / 6784, 11 / 84])

# Error estimator: difference between the 5th- and 4th-order solutions. Seven
# entries because it uses K[6] = f(t + h, y_new), the FSAL stage.
_E = np.array([-71 / 57600, 0.0, 71 / 16695, -71 / 1920, 17253 / 339200, -22 / 525, 1 / 40])

# Dense-output interpolant: a 4th-order polynomial in theta = (t - t_old)/h.
#   y(t_old + theta*h) = y_old + h * (K.T @ P) @ [theta, theta^2, theta^3, theta^4]
_P = np.array(
    [
        [1.0, -8048581381 / 2820520608, 8663915743 / 2820520608, -12715105075 / 11282082432],
        [0.0, 0.0, 0.0, 0.0],
        [0.0, 131558114200 / 32700410799, -68118460800 / 10900136933, 87487479700 / 32700410799],
        [0.0, -1754552775 / 470086768, 14199869525 / 1410260304, -10690763975 / 1880347072],
        [0.0, 127303824393 / 49829197408, -318862633887 / 49829197408, 701980252875 / 199316789632],
        [0.0, -282668133 / 205662961, 2019193451 / 616988883, -1453857185 / 822651844],
        [0.0, 40617522 / 29380423, -110615467 / 29380423, 69997945 / 29380423],
    ]
)

_ORDER = 5  # the order of the advancing (5th-order) solution
_ERROR_EXPONENT = -1.0 / (_ORDER)  # step-size exponent, as in SciPy's RK45
_SAFETY = 0.9
_MIN_FACTOR = 0.2
_MAX_FACTOR = 10.0


class OdeResult:
    """The subset of `scipy.integrate.OdeResult` this project reads.

    `simulate()` checks `.success` and `.message` and then takes `.y`, so those
    are what is provided. `.t` is carried too, since it costs nothing and makes
    the object useful on its own.
    """

    __slots__ = ("t", "y", "success", "message", "nfev", "status")

    def __init__(self, t, y, success, message, nfev):
        self.t = t
        self.y = y
        self.success = success
        self.message = message
        self.nfev = nfev
        self.status = 0 if success else -1

    def __repr__(self) -> str:
        return (
            f"OdeResult(success={self.success}, nfev={self.nfev}, "
            f"points={self.y.shape[1] if self.y.size else 0})"
        )


def _rms_norm(x) -> float:
    """SciPy's error norm: root-mean-square, not max-abs."""
    return float(np.sqrt(np.mean(np.square(x))))


def _initial_step(fun, t0, y0, f0, direction, rtol, atol, max_step):
    """Hairer's automatic starting step (ODE I, II.4, "Starting Step Size").

    A bad first guess costs correctness, not just time: too large and the first
    step is rejected repeatedly; too small and the controller crawls. This is
    the same algorithm SciPy uses.
    """
    scale = atol + np.abs(y0) * rtol
    d0 = _rms_norm(y0 / scale)
    d1 = _rms_norm(f0 / scale)

    h0 = 1e-6 if (d0 < 1e-5 or d1 < 1e-5) else 0.01 * d0 / d1

    y1 = y0 + h0 * direction * f0
    f1 = np.asarray(fun(t0 + h0 * direction, y1), dtype=float)
    d2 = _rms_norm((f1 - f0) / scale) / h0

    if d1 <= 1e-15 and d2 <= 1e-15:
        h1 = max(1e-6, h0 * 1e-3)
    else:
        h1 = (0.01 / max(d1, d2)) ** (1.0 / _ORDER)

    return min(100.0 * h0, h1, max_step)


def solve_ivp(
    fun,
    t_span,
    y0,
    method: str = "RK45",
    t_eval=None,
    max_step: float = np.inf,
    rtol: float = 1e-3,
    atol=1e-6,
    **_ignored,
):
    """Integrate dy/dt = fun(t, y) with adaptive Dormand-Prince 5(4).

    Signature-compatible with the `scipy.integrate.solve_ivp` calls this project
    makes. `method` is accepted and ignored — LSODA's stiff branch is never
    entered on this system (see the module docstring), so there is one
    integrator here rather than a switch that would always land the same way.

    Parameters
    ----------
    fun : callable(t, y) -> array_like
        Right-hand side. May return a list; it is coerced.
    t_span : (float, float)
        Integration interval. `t1 < t0` integrates backwards.
    y0 : array_like
        Initial state.
    t_eval : array_like, optional
        Points at which to report the solution, evaluated by dense output rather
        than by forcing steps to land on them. Must lie within `t_span` and be
        sorted in the direction of integration. Defaults to the step points.
    max_step : float
        Hard cap on step size. This project uses it so soil features are never
        stepped over.
    rtol, atol : float or array_like
        Relative and absolute tolerances, per component if array_like.

    Returns
    -------
    OdeResult
        `.y` has shape `(n_states, n_points)`, matching SciPy.
    """
    t0, t1 = float(t_span[0]), float(t_span[1])
    y0 = np.asarray(y0, dtype=float).ravel()
    n = y0.size

    rtol = np.asarray(rtol, dtype=float)
    atol = np.asarray(atol, dtype=float)
    if rtol.ndim == 0:
        rtol = np.full(n, float(rtol))
    if atol.ndim == 0:
        atol = np.full(n, float(atol))
    # SciPy clamps rtol from below for the same reason: below ~2.3e-14 the
    # controller is chasing floating-point noise and never converges.
    rtol = np.maximum(rtol, 100.0 * np.finfo(float).eps)

    max_step = float(max_step) if max_step is not None else np.inf
    if max_step <= 0:
        raise ValueError("max_step must be positive")

    direction = 1.0 if t1 >= t0 else -1.0
    total = abs(t1 - t0)

    # A zero-length interval is legitimate here — the route can put a booster
    # station on a segment boundary — and means "the state does not change".
    if total == 0.0:
        t_out = np.asarray(t_eval, dtype=float) if t_eval is not None else np.array([t0])
        return OdeResult(t_out, np.tile(y0[:, None], (1, t_out.size)), True, "", 0)

    if t_eval is not None:
        t_eval = np.asarray(t_eval, dtype=float).ravel()
        lo, hi = min(t0, t1), max(t1, t0)
        if t_eval.size and (t_eval.min() < lo - 1e-9 or t_eval.max() > hi + 1e-9):
            raise ValueError("t_eval must lie within t_span")

    ts = [t0]
    ys = [y0.copy()]
    out_t: list[float] = []
    out_y: list[np.ndarray] = []
    eval_i = 0
    n_eval = 0 if t_eval is None else t_eval.size

    nfev = 0

    def rhs(t, y):
        nonlocal nfev
        nfev += 1
        return np.asarray(fun(t, y), dtype=float).ravel()

    f = rhs(t0, y0)
    h = _initial_step(rhs, t0, y0, f, direction, rtol, atol, max_step)
    h = min(h, total)

    t = t0
    y = y0.copy()
    K = np.empty((7, n), dtype=float)

    # Bound the work. A well-posed run on this route takes O(10^3) steps; if it
    # takes 10^6 something is wrong with the model, and silently spinning for
    # the whole function timeout is the worst way to report that.
    max_steps = 1_000_000
    step_count = 0

    while (t - t1) * direction < 0:
        step_count += 1
        if step_count > max_steps:
            return OdeResult(
                np.asarray(out_t if t_eval is not None else ts),
                np.asarray(out_y if t_eval is not None else ys).T,
                False,
                f"Exceeded {max_steps} steps at t={t:.6g}; the step size has collapsed.",
                nfev,
            )

        # Never overshoot the endpoint, and never take a step so small it makes
        # no progress against floating-point resolution at this t.
        h = min(h, max_step, abs(t1 - t))
        h_min = 10.0 * abs(np.nextafter(t, direction * np.inf) - t)
        if h < h_min:
            h = h_min

        t_new = t + direction * h
        # Guard against the endpoint being overshot by rounding on the last step.
        if (t_new - t1) * direction > 0:
            t_new = t1
            h = abs(t_new - t)

        # ── One Dormand-Prince step ──────────────────────────────────
        K[0] = f
        for i in range(1, 6):
            dy = h * direction * (_A[i, :i] @ K[:i])
            K[i] = rhs(t + direction * _C[i] * h, y + dy)

        y_new = y + h * direction * (_B @ K[:6])
        f_new = rhs(t_new, y_new)
        K[6] = f_new  # FSAL stage, used by the error estimator

        # ── Error control ────────────────────────────────────────────
        scale = atol + np.maximum(np.abs(y), np.abs(y_new)) * rtol
        error = h * direction * (_E @ K)
        error_norm = _rms_norm(error / scale)

        if error_norm < 1.0:
            # Accept. The FSAL property gives the next step's first stage free.
            if error_norm == 0.0:
                factor = _MAX_FACTOR
            else:
                factor = min(_MAX_FACTOR, _SAFETY * error_norm**_ERROR_EXPONENT)

            # Emit any requested points that this step spans, via dense output —
            # so t_eval never perturbs the step sequence.
            if t_eval is not None:
                Q = K.T @ _P  # (n, 4)
                while eval_i < n_eval and (t_eval[eval_i] - t_new) * direction <= 1e-12:
                    te = t_eval[eval_i]
                    theta = 0.0 if h == 0 else (te - t) * direction / h
                    theta = min(max(theta, 0.0), 1.0)
                    powers = np.array([theta, theta**2, theta**3, theta**4])
                    out_t.append(float(te))
                    out_y.append(y + h * direction * (Q @ powers))
                    eval_i += 1
            else:
                ts.append(t_new)
                ys.append(y_new.copy())

            t, y, f = t_new, y_new, f_new
            h *= factor
        else:
            # Reject and shrink. `f` is unchanged, so the retry costs 6 stages.
            h *= max(_MIN_FACTOR, _SAFETY * error_norm**_ERROR_EXPONENT)

    # The final point can be left unemitted when it sits exactly on t1 and the
    # comparison above lands on the wrong side of the tolerance.
    if t_eval is not None:
        while eval_i < n_eval:
            out_t.append(float(t_eval[eval_i]))
            out_y.append(y.copy())
            eval_i += 1
        t_arr = np.asarray(out_t, dtype=float)
        y_arr = np.asarray(out_y, dtype=float).T if out_y else np.empty((n, 0))
    else:
        t_arr = np.asarray(ts, dtype=float)
        y_arr = np.asarray(ys, dtype=float).T

    return OdeResult(t_arr, y_arr, True, "", nfev)


# ═══════════════════════════════════════════════════════════════════
# interp1d
# ═══════════════════════════════════════════════════════════════════


class _Interp1D:
    """A callable that mirrors `scipy.interpolate.interp1d`'s call convention.

    The ODE right-hand side calls this several times per stage, several thousand
    times per run, always with a scalar. So there is a dedicated scalar branch
    that works on plain Python floats and `bisect` — array machinery costs more
    in dispatch overhead than the arithmetic saves at size 1, and this is the
    hot path of the whole simulator.
    """

    __slots__ = ("x", "y", "kind", "_slope_lo", "_slope_hi", "_xl", "_yl")

    def __init__(self, x, y, kind: str):
        x = np.asarray(x, dtype=float).ravel()
        y = np.asarray(y, dtype=float).ravel()
        if x.size != y.size:
            raise ValueError(f"x and y must be the same length; got {x.size} and {y.size}")
        if x.size < 2:
            raise ValueError("interp1d needs at least two points")

        order = np.argsort(x, kind="stable")
        self.x = x[order]
        self.y = y[order]
        self.kind = kind

        # Slopes of the end segments, held for linear extrapolation. Computed
        # once here rather than per call.
        dx_lo = self.x[1] - self.x[0]
        dx_hi = self.x[-1] - self.x[-2]
        self._slope_lo = (self.y[1] - self.y[0]) / dx_lo if dx_lo != 0 else 0.0
        self._slope_hi = (self.y[-1] - self.y[-2]) / dx_hi if dx_hi != 0 else 0.0

        # Plain-float copies for the scalar path.
        self._xl = self.x.tolist()
        self._yl = self.y.tolist()

    def _scalar(self, q: float) -> float:
        xl, yl = self._xl, self._yl

        if self.kind == "nearest":
            i = bisect_left(xl, q)
            if i == 0:
                return yl[0]
            if i >= len(xl):
                return yl[-1]
            return yl[i - 1] if (q - xl[i - 1]) <= (xl[i] - q) else yl[i]

        if q <= xl[0]:
            return yl[0] + self._slope_lo * (q - xl[0])
        if q >= xl[-1]:
            return yl[-1] + self._slope_hi * (q - xl[-1])

        i = bisect_right(xl, q)  # xl[0] < q < xl[-1], so 1 <= i <= len-1
        x0, x1 = xl[i - 1], xl[i]
        y0, y1 = yl[i - 1], yl[i]
        return y0 + (y1 - y0) * (q - x0) / (x1 - x0)

    def __call__(self, q):
        if type(q) is float:  # noqa: E721 — the hot path, and it is exactly float
            return self._scalar(q)

        scalar = np.ndim(q) == 0
        if scalar:
            return self._scalar(float(q))
        qa = np.asarray(q, dtype=float).ravel()

        if self.kind == "nearest":
            # Ties go to the lower index, matching scipy's default rounding for
            # `kind="nearest"`. Outside the knots this clamps, which IS the
            # correct extrapolation for a piecewise-constant quantity: the pipe
            # spec at the last waypoint holds beyond it.
            idx = np.searchsorted(self.x, qa, side="left")
            idx = np.clip(idx, 1, self.x.size - 1)
            left = self.x[idx - 1]
            right = self.x[idx]
            take_left = (qa - left) <= (right - qa)
            out = np.where(take_left, self.y[idx - 1], self.y[idx])
            out = np.where(qa <= self.x[0], self.y[0], out)
            out = np.where(qa >= self.x[-1], self.y[-1], out)
        else:
            out = np.interp(qa, self.x, self.y)
            below = qa < self.x[0]
            above = qa > self.x[-1]
            if below.any():
                out = np.where(below, self.y[0] + self._slope_lo * (qa - self.x[0]), out)
            if above.any():
                out = np.where(above, self.y[-1] + self._slope_hi * (qa - self.x[-1]), out)

        return out


def interp1d(x, y, kind: str = "linear", fill_value=None, bounds_error=None, **_ignored):
    """1-D interpolation over `kind` in {"linear", "nearest"}.

    Only the two kinds this project uses are implemented, and only with
    `fill_value="extrapolate"` semantics — an unsupported argument raises rather
    than being quietly ignored, because a silently wrong boundary condition is
    exactly the kind of error this codebase is built to refuse.
    """
    if kind not in ("linear", "nearest"):
        raise NotImplementedError(
            f"interp1d(kind={kind!r}) is not implemented here. This module "
            f"provides only the 'linear' and 'nearest' kinds the project uses."
        )
    if fill_value not in (None, "extrapolate"):
        raise NotImplementedError(
            f"interp1d(fill_value={fill_value!r}) is not implemented here; "
            f"only 'extrapolate' is."
        )
    if bounds_error:
        raise NotImplementedError("interp1d(bounds_error=True) is not implemented here.")
    return _Interp1D(x, y, kind)
