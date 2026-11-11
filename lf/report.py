"""Run history, standings and phone notifications.

After every bot run:
  - logs/status.json gets one history point (day, cash, debt, net, machines, contract, rank...),
    any actions taken, and the latest Overall Standing. The dashboard (index.html) reads it.
  - a push notification goes to ntfy.sh/<notify_topic> (config.yaml) when the bot changes
    something, when anything fails, and every ~2 hours as a status summary.
Reporting never breaks a bot run: every error here is swallowed.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import requests

SUMMARY_EVERY_RUNS = 12          # ~2 hours at one run per 10 minutes
MAX_HISTORY = 4000


def parse_standing(html: str) -> list[dict]:
    """Overall Standing page -> [{rank, team, net}] in page order."""
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.I | re.S):
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.I | re.S)]
        cells = [c for c in cells if c]
        if len(cells) >= 3 and cells[0].isdigit():
            try:
                out.append(dict(rank=int(cells[0]), team=cells[1],
                                net=float(cells[-1].replace(",", "").replace("$", ""))))
            except ValueError:
                continue
    return out


HEAD_RE = re.compile(r"^(\S+) day ([\d.]+) cash \$([-\d,]+) debt \$([-\d,]+) machines \{1: (\d+), 2: (\d+), 3: (\d+)\} "
                     r"on-order \{1: (\d+), 2: (\d+), 3: (\d+)\} inv (\d+) rop/roq \d+/\d+ contract (\d) wip (\d+)")


def backfill(log: Path) -> dict:
    """Rebuild history and actions from actions.log (used once, when status.json does not exist yet)."""
    data = {"history": [], "actions": [], "standings": [], "team": None}
    ts, day = None, None
    try:
        text = log.read_text()
    except Exception:  # noqa: BLE001
        return data
    for ln in text.splitlines():
        m = HEAD_RE.match(ln)
        if m:
            g = m.groups()
            ts, day = g[0], float(g[1])
            cash, debt = float(g[2].replace(",", "")), float(g[3].replace(",", ""))
            if day >= 50.5 and debt > 0 and cash < 200_000 and "edits=on" in ln and ts < "2026-11-09T16:45":
                cash = cash + debt   # runs before the 9 Nov fix logged cash net of debt
            data["history"].append(dict(ts=ts, day=day, cash=round(cash), debt=round(debt), net=round(cash - debt),
                                        machines={"1": int(g[4]), "2": int(g[5]), "3": int(g[6])},
                                        on_order={"1": int(g[7]), "2": int(g[8]), "3": int(g[9])},
                                        contract=int(g[11]), wip=int(g[12]), lots=None, inventory=int(g[10]),
                                        rank=None, teams=None))
            continue
        t = ln.strip()
        if ts and (t.startswith("OK ") or t.startswith("FAIL")):
            data["actions"].append(dict(ts=ts, day=day, ok=t.startswith("OK"), text=t[5:].split(" | ")[0].strip()))
    return data


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return backfill(path.parent / "actions.log")


def record(path: Path, ts: str, snap, standings: list[dict], acts_done: list[str], fails: list[str],
           insight: dict | None = None) -> dict:
    data = _load(path)
    team = getattr(snap, "team", None)
    me = next((s for s in standings if team and s["team"] == team), None)
    point = dict(ts=ts, day=round(snap.day, 2), cash=round(snap.cash), debt=round(snap.debt),
                 net=round(snap.cash - snap.debt), machines=snap.machines, on_order=snap.on_order,
                 contract=snap.contract, wip=snap.wip_limit, lots=snap.lots_per_job,
                 inventory=snap.inventory, rank=me["rank"] if me else None,
                 teams=len(standings) or None)
    if insight:
        point.update(demand=insight.get("demand"), shipped=insight.get("shipped"),
                     capacity=insight.get("capacity"), plan=insight.get("plan_rate"),
                     util=insight.get("util"), lead_h=(round(insight["lead_days"] * 24, 1)
                                                       if insight.get("lead_days") is not None else None),
                     rev_job=insight.get("rev_per_job"))
        data["insight"] = insight
    data["team"] = team
    hist = [h for h in data.get("history", []) if h.get("ts") != ts]   # backfill may already hold this run
    data["history"] = (hist + [point])[-MAX_HISTORY:]
    data["actions"] = [a for a in data.get("actions", []) if a.get("ts") != ts]
    for a in acts_done:
        data.setdefault("actions", []).append(dict(ts=ts, day=round(snap.day, 2), ok=True, text=a))
    for f in fails:
        data.setdefault("actions", []).append(dict(ts=ts, day=round(snap.day, 2), ok=False, text=f))
    if standings:
        data["standings"] = standings
        data["standings_ts"] = ts
        try:   # every team's cash over time -> earning rate, spending, projected finish
            from lf import rivals
            rv = rivals.update(path.parent / "rivals.json", snap.day, standings)
            data["rivals"] = rivals.analyse(rv, snap.day, team)
        except Exception as e:  # noqa: BLE001
            data["rivals_error"] = str(e)
    data["updated"] = ts
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":")))
    return point


def notify(topic: str, title: str, body: str, tags: str = "", priority: str = "default"):
    if not topic or os.environ.get("GITHUB_ACTIONS") != "true":
        return
    try:
        requests.post(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"), timeout=10,
                      headers={"Title": title, "Tags": tags, "Priority": priority})
    except requests.RequestException:
        pass


def _money(v):
    return f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}"


def after_run(cfg: dict, log_dir: Path, ts: str, snap, standings, lines: list[str], runs: int, error: str | None,
              insight: dict | None = None):
    try:
        acts = [ln.strip()[5:].split(" | ")[0].strip() for ln in lines if ln.strip().startswith("OK ")]
        fails = [ln.strip()[5:].strip() for ln in lines if ln.strip().startswith("FAIL")]
        topic = cfg.get("notify_topic", "")
        if snap is None:
            if error:
                notify(topic, "Littlefield bot ERROR", error[:300], "warning", "high")
            return
        prev_warn = (_load(log_dir / "status.json").get("insight") or {}).get("warnings") or []
        p = record(log_dir / "status.json", ts, snap, standings or [], acts, fails, insight)
        warn = (insight or {}).get("warnings") or []
        if warn and set(warn) != set(prev_warn):
            notify(topic, f"Littlefield: real data disagrees with plan (day {p['day']:.0f})",
                   "\n".join(warn)[:900], "warning", "high")
        rank = f"rank {p['rank']}/{p['teams']}" if p["rank"] else "rank n/a"
        state = (f"Day {p['day']:.0f} | net {_money(p['net'])} (cash {_money(p['cash'])}, debt {_money(p['debt'])}) | "
                 f"{rank} | contract {p['contract']} | machines {p['machines']}")
        if fails:
            notify(topic, f"Bot: {len(fails)} action(s) FAILED", state + "\n" + "\n".join(fails)[:900],
                   "warning", "high")
        if acts:
            notify(topic, f"Bot changed {len(acts)} setting(s) on day {p['day']:.0f}",
                   "\n".join(acts)[:900] + "\n\n" + state, "factory")
        elif runs % SUMMARY_EVERY_RUNS == 0:
            notify(topic, f"Littlefield status: day {p['day']:.0f}, {rank}", state, "bar_chart", "low")
    except Exception:  # noqa: BLE001
        pass
