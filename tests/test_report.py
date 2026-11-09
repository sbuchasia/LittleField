"""Reporting must record runs and never break the bot."""
import json

import bot
from lf import report
from lf.client import LittlefieldClient
from tests.mock_server import MockGame, serve


def test_standing_parser_live_markup():
    h = ("<tr> <td align=center class=columnHeader><B>Rank</B> </td><td><B>Team</B></td><td><b>Cash</b></td></tr>"
         "<tr> <td align=right><font face=arial>1</font> </td> <TD align=left><font face=arial>stonewallchampions</font> </td>"
         " <TD align=right><font face=arial> 713,930</font> </td> </tr>"
         "<tr><td>97</td><td>eureka</td><td>-4,333,111</td></tr>")
    assert report.parse_standing(h) == [dict(rank=1, team="stonewallchampions", net=713930.0),
                                        dict(rank=97, team="eureka", net=-4333111.0)]


def test_backfill_from_log(tmp_path):
    (tmp_path / "actions.log").write_text(
        "2026-11-09T18:00:49+00:00 day 54.0 cash $1,302,805 debt $1,200,000 machines {1: 4, 2: 3, 3: 4} "
        "on-order {1: 0, 2: 0, 3: 0} inv 8040 rop/roq 3660/3900 contract 1 wip 8 rule pri4 edits=on\n"
        "  OK   buy S1 -> 9  (want 21) | Station Form ... purchased\n")
    d = report.backfill(tmp_path / "actions.log")
    assert d["history"][0]["net"] == 102805 and d["actions"][0]["text"].startswith("buy S1 -> 9")


def test_runs_write_status(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(bot, "LOG", tmp_path / "actions.log")
    monkeypatch.delenv("PAUSE", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)
    game = MockGame(seed=1, advance_per_login=1.0)
    srv, base = serve(game)
    try:
        for i in range(3):
            assert bot.main(client=LittlefieldClient(game.team, "pw", base=base, pause=0),
                            now=f"2026-11-10T00:0{i}:00+00:00") == 0
    finally:
        srv.shutdown()
    d = json.loads((tmp_path / "status.json").read_text())
    assert len(d["history"]) == 3 and d["actions"]
