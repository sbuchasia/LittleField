"""Full rehearsal before the live game.

Drives the REAL bot.py and the REAL HTTP client against the mock game server
(tests/mock_server.py), which serves pages in the live game's format on top of
the digital twin started from the same day-50 state as the live factory.

Scenarios
  A  before 9:30 pm   order controls hidden, clock frozen -> bot must change nothing
  B  first live run   controls visible, dry run at day 50 -> what the bot will do first
  C  full season      one bot run per 10 real minutes (0.345 sim day), day 50 -> 300
  D  bad night        same, but 25% of runs skipped and a 2-hour GitHub outage
  E  no loan form     loans never appear -> buys from cash only
  F  do nothing       baseline

    python scripts/demo.py            # writes demo/results.json and demo/*.log
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from lf.client import LittlefieldClient  # noqa: E402
from lf.sim import Factory  # noqa: E402
from tests.mock_server import MockGame, serve  # noqa: E402

OUT = ROOT / "demo"
OUT.mkdir(exist_ok=True)
STEP = 10 / 29          # sim days per 10 real minutes


def fresh(tag):
    for p in (OUT / f"{tag}.state.json", OUT / f"{tag}.log"):
        p.unlink(missing_ok=True)
    bot.STATE = OUT / f"{tag}.state.json"
    bot.LOG = OUT / f"{tag}.log"


def one_run(game, base, dry=False):
    os.environ.pop("PAUSE", None)
    if dry:
        os.environ["DRY_RUN"] = "1"
    else:
        os.environ.pop("DRY_RUN", None)
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        rc = bot.main(client=LittlefieldClient(game.team, game.password, base=base, pause=0))
    return rc, buf.getvalue()


def point(f, rc):
    L = f.log
    d = int(f.day)
    lt = L["JOBT"].get(d - 1) or L["JOBT"].get(d)
    return dict(day=round(f.day, 2), cash=round(f.cash), debt=round(f.debt), net=round(f.net_cash),
                m1=f.machines[1], m2=f.machines[2], m3=f.machines[3], contract=f.contract,
                wip=f.wip_limit, inv=f.inventory, lead=round(lt, 2) if lt is not None else None, rc=rc)


def season(tag, seed=7, skip=0.0, outage=None, loans=True):
    fresh(tag)
    game = MockGame(seed=seed, advance_per_login=0.0, loans=loans)
    game.frozen = True                    # we move the clock ourselves
    srv, base = serve(game)
    rng = random.Random(seed)
    pts, acts, rcs = [], [], []
    t = 50.0
    try:
        while t < 300:
            t = min(300.0, t + STEP)
            game.f.step_until(t)
            if outage and outage[0] <= t < outage[1]:
                continue
            if rng.random() < skip:
                continue
            rc, txt = one_run(game, base)
            rcs.append(rc)
            for ln in txt.splitlines()[1:]:
                if ln.strip().startswith(("OK", "FAIL", "note")):
                    acts.append(f"d{game.f.day:6.1f} {ln.strip()}")
            pts.append(point(game.f, rc))
        game.f.step_until(305)
    finally:
        srv.shutdown()
    end = point(game.f, 0)
    return dict(points=pts[::3] + [end], actions=acts, runs=len(rcs),
                fails=sum(1 for r in rcs if r != 0), net=end["net"], cash=end["cash"], debt=end["debt"],
                final=end)


def snapshot_runs():
    # A: before the start, controls hidden, clock frozen at day 50
    fresh("A_before_start")
    g = MockGame(seed=7, editable_orders=False, loans=False)
    g.frozen = True
    srv, base = serve(g)
    try:
        rc_a, txt_a = one_run(g, base)
    finally:
        srv.shutdown()
    a = dict(rc=rc_a, posts=len(g.posts), log=txt_a.strip().splitlines())

    # B: first run once controls appear (dry run at day 50)
    fresh("B_first_live_run")
    g = MockGame(seed=7)
    g.frozen = True
    srv, base = serve(g)
    try:
        rc_b, txt_b = one_run(g, base, dry=True)
    finally:
        srv.shutdown()
    b = dict(rc=rc_b, posts=len(g.posts), log=txt_b.strip().splitlines())
    return a, b


def main():
    t0 = time.time()
    res = {}
    res["A"], res["B"] = snapshot_runs()
    print("A before start:", res["A"]["log"][0][:160], "| posts", res["A"]["posts"])
    print("B first run   :", *res["B"]["log"], sep="\n  ")
    res["C"] = season("C_full_season")
    print(f"C full season : net ${res['C']['net']:,.0f}  runs {res['C']['runs']}  fails {res['C']['fails']}")
    res["D"] = season("D_bad_night", skip=0.25, outage=(95.0, 99.2))
    print(f"D bad night   : net ${res['D']['net']:,.0f}  runs {res['D']['runs']}  fails {res['D']['fails']}")
    res["E"] = season("E_no_loans", loans=False)
    print(f"E no loan form: net ${res['E']['net']:,.0f}  runs {res['E']['runs']}  fails {res['E']['fails']}")
    f = Factory(seed=7)
    f.step_until(305)
    res["F"] = dict(net=round(f.net_cash))
    print(f"F do nothing  : net ${res['F']['net']:,.0f}")
    (OUT / "results.json").write_text(json.dumps(res, indent=1))
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
