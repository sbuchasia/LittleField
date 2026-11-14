"""Track every team's cash (cash - debt) from the game's standings page and estimate how fast
each is earning, when it spent money, and where it would finish if it kept its current pace.

The standings page is the only thing the game shows about other teams, so machines and settings
are inferred from cash: steady growth = earnings, a sudden drop = a purchase (machines or a kit
order), a rise followed by flat = a team that has stopped serving demand.
"""
from __future__ import annotations

import json
from pathlib import Path

PEAK_END = 180          # handout: flat demand until ~day 180
AFTER_PEAK_DAYS = 60    # post-180 profit ~ 60 days at peak pace (lower plateau + decline), from our model
WINDOW = 10             # game days used for the earning rate (kit orders / purchases make short windows misleading)


def update(path: Path, day: float, standings: list[dict]) -> dict:
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except Exception:  # noqa: BLE001
            data = {}
    d = str(int(day))
    for s in standings:
        if s.get("net") is None:
            continue
        data.setdefault(s["team"], {})[d] = float(s["net"])
    path.write_text(json.dumps(data, separators=(",", ":")))
    return data


def _days_left_equiv(day: float) -> float:
    """Earning days left, in days at today's pace: full pace to day 180, then demand assumed to
    fall in a straight line to zero at day 300 (60 pace-days at day 180, 0 at day 300)."""
    day = min(max(day, 0), 300)
    if day < PEAK_END:
        return (PEAK_END - day) + AFTER_PEAK_DAYS
    # after the peak today's pace is already the lower level: remaining earning ~ half the days left
    return (300 - day) / 2


def _slope(pts):
    n = len(pts)
    if n < 2:
        return None
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    den = sum((x - mx) ** 2 for x, _ in pts)
    return sum((x - mx) * (y - my) for x, y in pts) / den if den else None


def analyse(data: dict, day: float, me: str, top: int = 12, proj_n: int = 50) -> dict:
    day = int(day)
    rows = []
    for team, series in data.items():
        pts = sorted((int(k), v) for k, v in series.items())
        if not pts:
            continue
        net = pts[-1][1]
        win = [(x, y) for x, y in pts if x >= day - WINDOW]
        # median day-over-day change: one-off jumps (machine sales in, kit orders out) don't skew it
        steps = sorted((y1 - y0) / (x1 - x0) for (x0, y0), (x1, y1) in zip(win, win[1:]) if x1 > x0)
        rate = (steps[len(steps) // 2] if len(steps) % 2 else
                (steps[len(steps) // 2 - 1] + steps[len(steps) // 2]) / 2) if steps else None
        spent = []          # one-day drops bigger than a kit order -> machines bought
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x1 - x0 == 1 and y1 - y0 < -60_000 and x1 >= day - 30:
                spent.append((x1, round(y0 - y1)))
        proj = None
        if rate is not None and len(win) >= 8:
            proj = net + rate * _days_left_equiv(day)
        rows.append(dict(team=team, net=round(net), rate=None if rate is None else round(rate),
                         proj=None if proj is None else round(proj), days=len(win),
                         spent=spent[-3:]))
    by_now = sorted(rows, key=lambda r: -r["net"])
    for i, r in enumerate(by_now, 1):
        r["rank"] = i
    by_proj = sorted([r for r in rows if r["proj"] is not None], key=lambda r: -r["proj"])
    for i, r in enumerate(by_proj, 1):
        r["proj_rank"] = i
    mine = next((r for r in rows if r["team"] == me), None)
    show = by_now[:top]
    if mine and mine not in show:
        show = show + [mine]
    return dict(day=day, teams=len(rows), window=WINDOW, me=mine, table=show,
                proj_top=by_proj[:proj_n], enough_data=bool(mine and mine["days"] >= 8))


def line(a: dict) -> str:
    m = a.get("me") or {}
    if not a.get("enough_data"):
        return f"  rivals: tracking {a['teams']} teams (rates after 8 game days of data)"
    lead = a["table"][0]
    best = a["proj_top"][0] if a["proj_top"] else None
    return (f"  rivals: us #{m.get('rank')} net {m.get('net'):,} at {m.get('rate') or 0:+,}/d -> "
            f"proj ${(m.get('proj') or 0)/1e6:.2f}M (#{m.get('proj_rank')}); leader {lead['team']} "
            f"{lead['net']:,} at {lead['rate'] or 0:+,}/d"
            + (f"; best projected {best['team']} ${best['proj']/1e6:.2f}M" if best else ""))
