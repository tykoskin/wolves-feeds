"""Lakeshore Badgers games: filter, home rink, TBD, and fetch fallback."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "site"))
import build_site as site  # noqa: E402

STATIC = os.path.join(ROOT, "data", "badgers-static.ics")
TZ = site.gs.TZ


def _cal(*events):
    return "BEGIN:VCALENDAR\n" + "".join(events) + "END:VCALENDAR\n"


def _event(summary, start, end, location="Away Arena", uid="uid@test"):
    return (
        "BEGIN:VEVENT\n"
        f"SUMMARY:{summary}\n"
        f"LOCATION:{location}\n"
        f"DTSTART:{start}\n"
        f"DTEND:{end}\n"
        f"UID:{uid}\n"
        "END:VEVENT\n"
    )


def _local(ev):
    return datetime.fromisoformat(ev["start"]).astimezone(TZ)


class ParseBadgersTests(unittest.TestCase):
    def test_static_file_is_the_six_games(self):
        with open(STATIC, encoding="utf-8") as f:
            events = site.parse_badgers(f.read())
        self.assertEqual(len(events), 6)
        got = []
        for ev in events:
            local = _local(ev)
            got.append((f"{local:%Y-%m-%d %H:%M}", ev["home"], ev["tbd"], ev["opponent"]))
            self.assertEqual(ev["team"], "Badgers")
            self.assertEqual(ev["src"], "crossbar")
            self.assertEqual(ev["kind"], "game")
            self.assertNotIn("[TBD]", ev["title"])
            self.assertNotIn("Wolves", ev["title"])
        self.assertEqual(got, [
            ("2026-11-07 19:00", False, False, "Hudsonville"),
            ("2026-11-11 16:00", True, False, "Cadillac"),
            ("2026-11-14 17:00", True, False, "Kalamazoo Eagles"),
            ("2026-11-25 00:00", False, True, "Kentwood"),
            ("2026-11-27 00:00", False, True, "Big Rapids"),
            ("2026-11-28 00:00", False, True, "Big Rapids"),
        ])
        self.assertIn("West Shore Community", events[1]["address"])
        self.assertIn("Scottville", events[1]["address"])
        self.assertNotIn("West Shore Community", events[0]["address"])

    def test_live_feed_shape_uses_rink_not_the_word_vs(self):
        text = _cal(
            _event("Lakeshore Badgers Practice", "20261107T230000Z", "20261108T000000Z"),
            _event("Lakeshore Badgers Other", "20261108T010000Z", "20261108T020000Z"),
            _event("Lakeshore Badgers vs Hudsonville", "20261108T000000Z", "20261108T020000Z",
                   "8500 48th Ave", "h@test"),
            _event("Lakeshore Badgers vs Cadillac", "20261111T210000Z", "20261111T230000Z",
                   "West Shore Community College", "c@test"),
            _event("Lakeshore Badgers vs Kalamazoo Eagles", "20261114T220000Z", "20261115T000000Z",
                   "West Shore Community College", "k@test"),
            _event("Lakeshore Badgers vs Kentwood", "20261125T050000Z", "20261125T070000Z",
                   "Kentwood Ice Arena", "ke@test"),
            _event("Lakeshore Badgers vs Big Rapids", "20261127T050000Z", "20261127T070000Z",
                   "R.L. Ewigleben Sports Complex", "b1@test"),
            _event("Lakeshore Badgers vs Big Rapids", "20261128T050000Z", "20261128T060000Z",
                   "R.L. Ewigleben Sports Complex", "b2@test"),
        )
        events = site.parse_badgers(text)
        self.assertEqual([ev["opponent"] for ev in events],
                         ["Hudsonville", "Cadillac", "Kalamazoo Eagles", "Kentwood", "Big Rapids", "Big Rapids"])
        self.assertEqual([ev["home"] for ev in events], [False, True, True, False, False, False])
        self.assertEqual([ev["tbd"] for ev in events], [False, False, False, True, True, True])
        self.assertEqual(_local(events[0]).strftime("%Y-%m-%d %H:%M"), "2026-11-07 19:00")
        self.assertEqual(_local(events[1]).strftime("%Y-%m-%d %H:%M"), "2026-11-11 16:00")
        self.assertEqual(_local(events[2]).strftime("%Y-%m-%d %H:%M"), "2026-11-14 17:00")
        # Midnight placeholder must stay on the calendar the whole day.
        self.assertEqual(_local(events[3]).strftime("%H:%M"), "00:00")
        self.assertGreater(datetime.fromisoformat(events[3]["end"]), datetime.fromisoformat(events[3]["start"]))
        self.assertEqual(datetime.fromisoformat(events[3]["end"]).astimezone(TZ).strftime("%Y-%m-%d %H:%M"),
                         "2026-11-26 00:00")

    def test_midnight_follows_america_detroit_not_a_fixed_zulu_hour(self):
        # 05:00Z is midnight only while Detroit is on standard time.
        est = site.parse_badgers(_cal(_event("Lakeshore Badgers vs Kentwood", "20261125T050000Z", "20261125T070000Z")))
        edt_late = site.parse_badgers(_cal(_event("Lakeshore Badgers vs Kentwood", "20260725T050000Z", "20260725T070000Z")))
        edt_midnight = site.parse_badgers(_cal(_event("Lakeshore Badgers vs Kentwood", "20260725T040000Z", "20260725T060000Z")))
        self.assertTrue(est[0]["tbd"])
        self.assertFalse(edt_late[0]["tbd"])
        self.assertEqual(_local(edt_late[0]).strftime("%H:%M"), "01:00")
        self.assertTrue(edt_midnight[0]["tbd"])

    def test_bracket_tbd_is_stripped_from_all_day_games(self):
        text = _cal(
            "BEGIN:VEVENT\n"
            "SUMMARY:[TBD] Lakeshore Badgers @ Big Rapids\n"
            "LOCATION:R.L. Ewigleben Sports Complex\n"
            "DTSTART;VALUE=DATE:20261127\n"
            "DTEND;VALUE=DATE:20261128\n"
            "END:VEVENT\n"
        )
        ev = site.parse_badgers(text)[0]
        self.assertTrue(ev["tbd"])
        self.assertFalse(ev["home"])
        self.assertEqual(ev["opponent"], "Big Rapids")
        self.assertEqual(ev["title"], "Lakeshore Badgers @ Big Rapids")
        self.assertNotIn("[TBD]", ev["title"])

    def test_html_entities_in_the_rink_name_are_unescaped(self):
        text = _cal(_event(
            "Lakeshore Badgers vs Gladwin", "20261205T170000Z", "20261205T190000Z",
            "Gladwin Community Arena &amp; Fitness Center"))
        ev = site.parse_badgers(text)[0]
        self.assertEqual(ev["address"], "Gladwin Community Arena & Fitness Center")
        self.assertEqual(_local(ev).strftime("%Y-%m-%d %H:%M"), "2026-12-05 12:00")
        self.assertFalse(ev["tbd"])
        self.assertFalse(ev["home"])


class FallbackTests(unittest.TestCase):
    def test_failed_fetch_uses_cache_then_static_and_does_not_raise(self):
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, "badgers.json")
            with open(cache, "w", encoding="utf-8") as f:
                json.dump([{"team": "Badgers", "opponent": "Cached"}], f)
            with mock.patch.object(site.gs, "fetch", side_effect=RuntimeError("down")):
                events, fresh = site.badgers_events(cache, "/no/such/static.ics")
            self.assertFalse(fresh)
            self.assertEqual(events, [{"team": "Badgers", "opponent": "Cached"}])

            missing = os.path.join(d, "missing.json")
            with mock.patch.object(site.gs, "fetch", side_effect=RuntimeError("down")):
                events, fresh = site.badgers_events(missing, STATIC)
            self.assertFalse(fresh)
            self.assertEqual(len(events), 6)
            self.assertFalse(os.path.exists(missing))

    def test_non_calendar_response_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, "nope.json")
            with mock.patch.object(site.gs, "fetch", return_value="<html>unavailable</html>"):
                events, fresh = site.badgers_events(cache, STATIC)
            self.assertFalse(fresh)
            self.assertEqual(len(events), 6)

    def test_good_parse_is_cached_and_not_replaced_by_static(self):
        body = _cal(_event("Lakeshore Badgers vs Cadillac", "20261111T210000Z", "20261111T230000Z",
                           "West Shore Community College"))
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, "badgers.json")
            with mock.patch.object(site.gs, "fetch", return_value=body):
                events, fresh = site.badgers_events(cache, STATIC)
            self.assertTrue(fresh)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["opponent"], "Cadillac")
            with open(cache, encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved[0]["opponent"], "Cadillac")


class TabOrderTests(unittest.TestCase):
    def test_badgers_is_last_after_open_rink_and_rink_info(self):
        events = [
            {"team": "12U"}, {"team": "8U"}, {"team": "Club"},
            {"team": "Rink Info"}, {"team": "Open Rink"}, {"team": "Badgers"},
        ]
        self.assertEqual(site.team_tabs(events),
                         ["12U", "8U", "Club", "Rink Info", "Open Rink", "Badgers"])

    def test_badgers_are_not_merged_into_all_teams(self):
        badger = {"src": "crossbar", "team": "Badgers", "kind": "game",
                  "title": "Lakeshore Badgers vs Cadillac", "start": "2026-11-11T16:00:00-05:00",
                  "address": "West Shore Community College"}
        club = {"src": "club", "team": "12U", "kind": "event", "title": "Try Hockey",
                "start": "2026-11-11T16:00:00-05:00", "address": "West Shore Community College"}
        out = site.combine_shared([badger, club])
        self.assertEqual(out[0]["team"], "Badgers")
        self.assertNotIn("teams", out[0])
        self.assertNotIn("Badgers", site.TEAM_ORDER)


if __name__ == "__main__":
    unittest.main()
