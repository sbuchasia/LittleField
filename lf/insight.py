"""Read the game's own daily data and say what is really happening.

Every run this turns the game's plots (orders per day, utilisation, lead time, revenue per job)
into a few plain numbers, compares them with what the bot is planning for, and raises warnings
when reality and the plan disagree. Output goes to the log, status.json, the dashboard and the
phone (only when the set of warnings changes).
"""
from __future__ import annotations

from lf.policy import hours_per_job

PRICE = {1: 750, 2: 1000, 3: 1500}
QUOTE_DAYS = {1: 3.0, 2: 1.0, 3: 0.25}


def _by_day(series: dict, prefix: str) -> dict:
    """Merge every plot whose name starts with prefix (e.g. JOBT_c1, JOBT_c3) into {day: value}.
    Days with several series keep the largest value (the active contract's line)."""
    out = {}
    for k, v in series.items():
        if k == prefix or k.startswith(prefix + "_"):
            for x, y in v.items():
                d = int(round(float(x)))
                if y is not None and float(y) > 0:
                    out[d] = max(out.get(d, 0.0), float(y))
    return out


def _mean(d: dict, lo: float, hi: float):
    v = [y for x, y in d.items() if lo <= x <= hi]
    return (sum(v) / len(v), len(v)) if v else (None, 0)


def analyse(snap, plan_rate: float | None = None) -> dict:
    s = snap.series
    day = int(snap.day)
    jobin = _by_day(s, "JOBIN")
    util = {k: _by_day(s, f"S{k}UTIL") for k in (1, 2, 3)}
    lt = _by_day(s, "JOBT")
    rev = _by_day(s, "JOBREV")
    out = _by_day(s, "JOBOUT")

    dem, n = _mean(jobin, day - 5, day)
    prev, _ = _mean(jobin, day - 12, day - 6)
    peak = max((v for x, v in jobin.items() if x >= day - 30), default=None)
    lots = snap.lots_per_job or 3
    cap = {k: snap.machines[k] * 24 / hours_per_job(k, lots) for k in (1, 2, 3)}
    u3 = {k: _mean(util[k], day - 3, day)[0] for k in (1, 2, 3)}
    u3 = {k: (v * 100 if v is not None and v <= 1.5 else v) for k, v in u3.items()}   # % either way
    lead, _ = _mean(lt, day - 3, day)
    rpj, _ = _mean(rev, day - 3, day)
    shipped, _ = _mean(out, day - 5, day)
    price = PRICE.get(snap.contract, 0)

    r = dict(day=day, demand=_r(dem), demand_prev=_r(prev), demand_peak30=_r(peak),
             trend_pct=_r((dem / prev - 1) * 100) if dem and prev else None,
             shipped=_r(shipped), capacity=_r(min(cap.values())),
             cap={k: _r(v) for k, v in cap.items()}, util={k: _r(v) for k, v in u3.items()},
             lead_days=_r(lead, 3), rev_per_job=_r(rpj, 0), price=price,
             plan_rate=_r(plan_rate), kits_days=_r(snap.inventory / (dem * 60), 1) if dem else None)

    w = []
    if dem:
        if plan_rate and plan_rate > dem * 1.15:
            w.append(f"plan assumes {plan_rate:.0f}/d but real orders are {dem:.0f}/d")
        if r["trend_pct"] is not None and abs(r["trend_pct"]) >= 15:
            w.append(f"demand {'rose' if r['trend_pct'] > 0 else 'fell'} {abs(r['trend_pct']):.0f}% "
                     f"({prev:.0f} -> {dem:.0f}/d)")
        if dem > 0.9 * min(cap.values()):
            w.append(f"orders {dem:.0f}/d near capacity {min(cap.values()):.0f}/d")
        if r["kits_days"] is not None and r["kits_days"] < 1.0 and not snap.kits_on_order:
            w.append(f"kits for only {r['kits_days']:.1f} days and none on order")
    if lead is not None and snap.contract in QUOTE_DAYS and lead > QUOTE_DAYS[snap.contract]:
        w.append(f"lead time {lead*24:.1f}h is over the {QUOTE_DAYS[snap.contract]*24:.0f}h quote")
    if rpj is not None and price and rpj < 0.95 * price:
        w.append(f"jobs earn ${rpj:,.0f} avg, below the ${price:,} price (late orders)")
    hot = [k for k, v in u3.items() if v is not None and v > 90]
    if hot:
        w.append("station(s) " + ",".join(map(str, hot)) + " over 90% busy")
    r["warnings"] = w
    return r


def _r(v, nd=1):
    return None if v is None else round(float(v), nd)


def line(r: dict) -> str:
    def f(v, suf=""):
        return "?" if v is None else f"{v:g}{suf}"
    u = r["util"]
    lead = "?" if r["lead_days"] is None else f"{r['lead_days'] * 24:.1f}h"
    txt = (f"  insight: orders {f(r['demand'])}/d (prev {f(r['demand_prev'])}, plan {f(r['plan_rate'])}) "
           f"shipped {f(r['shipped'])}/d capacity {f(r['capacity'])}/d "
           f"util {f(u[1],'%')}/{f(u[2],'%')}/{f(u[3],'%')} "
           f"lead {lead} rev/job {f(r['rev_per_job'])} of {r['price']}")
    if r["warnings"]:
        txt += "\n  WARN " + "; ".join(r["warnings"])
    return txt
