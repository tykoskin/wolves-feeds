#!/usr/bin/env python3
"""Build the West Shore Wolves schedule page (index.html) from real data.

Sources (standard library only, same approach as gamesheet_feed.py):
  1. GameSheet public schedule pages for 12U and 10U (games, rinks, opponents, logos, scores, LiveBarn).
  2. The club's published Outlook calendar "West Shore Wolves Events" (the calendar behind
     events.westshorewolves.com): practices, tournaments, 8U, Learn to Play and WSCC rink sessions.
  3. SportsEngine's public iCal for 8U (no GameSheet at that age). See gamesheet_feed.SPORTSENGINE_8U.
GameSheet wins for any 12U/10U game it lists; the matching club calendar entry only adds its street address.
SportsEngine wins for an 8U-only item Outlook also lists (same day, same title). A club event posted
for 8U and another team stays a single Outlook card. If the SportsEngine fetch fails, the previous
8U events are kept (docs/sportsengine-8u.json, or the last built page).

Usage: python3 build_site.py [output_dir]   (default: folder of this script)
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import shutil
import uuid
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.dirname(HERE)
FEEDS_REPO = os.environ.get("FEEDS_REPO") or (
    _parent if os.path.isfile(os.path.join(_parent, "gamesheet_feed.py")) else "/workspace/wolves-feeds")
sys.path.insert(0, FEEDS_REPO if os.path.isdir(FEEDS_REPO) else HERE)
import gamesheet_feed as gs  # noqa: E402  (reuse SEASON, TEAMS, fetch, extract_games, TZ)

OUT = sys.argv[1] if len(sys.argv) > 1 else HERE
SITE = "https://events.westshorewolves.com/"
FEED_BASE = SITE  # feeds live at the site root
OWA = ("https://outlook.office365.com/owa/published/951e74ceea36492c9dc438c625a09bb9@westshorewolves.com/"
       "11d17ff53239408aa6ab470c991b280b7746953958344761427/service.svc")
CLUB_PAGE = ("https://outlook.office365.com/calendar/published/951e74ceea36492c9dc438c625a09bb9@westshorewolves.com/"
             "11d17ff53239408aa6ab470c991b280b7746953958344761427/calendar.html")
CUSTOM_DOMAIN = "events.westshorewolves.com"
TEAM_ORDER = ["12U", "10U", "8U", "Learn to Play"]
FINAL = ("final", "completed", "complete", "closed")


# ---------- club calendar (Outlook published calendar, anonymous JSON service) ----------
def owa_call(action, body):
    h = {"User-Agent": gs.UA, "Accept": "*/*", "Content-Type": "application/json; charset=utf-8",
         "Action": action, "X-Req-Source": "PublishedCalendar",
         "X-OWA-UrlPostData": urllib.parse.quote(json.dumps(body, separators=(",", ":"))),
         "X-OWA-CANARY": "X-OWA-CANARY_cookie_is_null_or_empty",
         "Origin": "https://outlook.office365.com", "Cookie": "ClientId=" + uuid.uuid4().hex.upper()}
    req = urllib.request.Request(f"{OWA}?action={action}&app=PublishedCalendar&n=0", data=b"",
                                 headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def club_items(start, end):
    hdr = {"__type": "JsonRequestHeaders:#Exchange", "RequestServerVersion": "V2018_01_08",
           "TimeZoneContext": {"__type": "TimeZoneContext:#Exchange", "TimeZoneDefinition": {
               "__type": "TimeZoneDefinitionType:#Exchange", "Id": "Eastern Standard Time"}}}
    s = owa_call("GetAnonymousCalendarSessionData",
                 {"__type": "GetAnonymousCalendarSessionDataJsonRequest:#Exchange", "Header": hdr})
    fid = s["Body"]["CalendarFolder"]["FolderId"]["Id"]
    r = owa_call("FindItem", {
        "__type": "FindItemJsonRequest:#Exchange", "Header": hdr,
        "Body": {"__type": "FindItemRequest:#Exchange",
                 "ParentFolderIds": [{"__type": "FolderId:#Exchange", "Id": fid}],
                 "ItemShape": {"__type": "ItemResponseShape:#Exchange", "BaseShape": "IdOnly"},
                 "Traversal": "Shallow",
                 "Paging": {"__type": "CalendarPageView:#Exchange",
                            "StartDate": f"{start:%Y-%m-%d}T00:00:00.000",
                            "EndDate": f"{end:%Y-%m-%d}T23:59:59.999"}}})
    return r["Body"]["ResponseMessages"]["Items"][0]["RootFolder"]["Items"]


def classify_club(it):
    subj = re.sub(r"\s+", " ", it.get("Subject") or "").strip()
    loc = ((it.get("Location") or {}).get("DisplayName") or "").strip()
    start = datetime.fromisoformat(it["Start"])
    end = datetime.fromisoformat(it["End"]) if it.get("End") else None
    m = re.match(r"^\[([^\]]+)\]\s*(.*)$", subj)
    if m:
        team, title = m.group(1).strip(), m.group(2).strip()
    else:
        m2 = re.match(r"^(\d+U)\b\s*(.*)$", subj)
        team, title = (m2.group(1), subj) if m2 else ("Club", subj)
    tbd = bool(re.search(r"time is TBD", title, re.I)) or (start.hour == 0 and start.minute == 0)
    title = re.sub(r"\s*time is TBD\s*", " ", title, flags=re.I).strip()
    if team == "WSCC":
        kind, team = "rink", "Open Rink"
    elif re.search(r"practice", title, re.I):
        kind = "practice"
    elif re.search(r"tournament|district|states|jamboree|showcase|classic|cup", title, re.I):
        kind = "tournament"
    elif re.search(r"\b(at|vs\.?)\b", title, re.I) and team in ("12U", "10U", "8U"):
        kind = "game"
    else:
        kind = "event"
    ev = {"src": "club", "team": team, "kind": kind, "title": title, "start": start.isoformat(),
          "end": end.isoformat() if end else None, "tbd": tbd, "address": loc or None}
    if kind == "game":  # "[12U] Fox Red at 12U" (home) or "12U at Grand valley" (away)
        a = re.match(rf"^(?:{team}\s+)?at\s+(.+)$", title, re.I)
        h = re.match(rf"^(.+?)\s+at\s+{team}$", title, re.I)
        if h:
            ev.update(home=True, opponent=h.group(1).strip())
        elif a:
            ev.update(home=False, opponent=a.group(1).strip())
    if kind == "tournament":
        ev["title"] = re.sub(rf"\s+at\s+{team}$", "", re.sub(rf"^{team}\s+at\s+", "", title, flags=re.I), flags=re.I)
    return ev


# ---------- GameSheet ----------
def gamesheet_events():
    out, ok = [], []
    for team, label, _slug in gs.TEAMS:
        try:
            games = gs.extract_games(gs.fetch(gs.schedule_url(team)), team)
        except Exception as e:  # noqa: BLE001
            print(f"WARNING GameSheet {label}: {e}", file=sys.stderr)
            continue
        ok.append(label)
        for g in games:
            home, vis = g["home"], g["visitor"]
            is_home = home.get("id") == team
            us, them = (home, vis) if is_home else (vis, home)
            final = (g.get("status") or "").lower() in FINAL
            start = datetime.fromisoformat(g["timeStampZulu"].replace("Z", "+00:00"))
            lb = ((g.get("data") or {}).get("broadcasters") or {}).get("livebarn") or {}
            ev = {"src": "gamesheet", "team": label, "kind": "game", "home": is_home,
                  "opponent": them.get("title") or "TBD", "oppLogo": them.get("logo") or None,
                  "rink": g.get("location") or None, "start": start.isoformat(),
                  "end": (start + timedelta(minutes=gs.GAME_MINUTES)).isoformat(),
                  "gameType": (g.get("gameType") or "").replace("_", " ") or None,
                  "number": g.get("number") or None, "status": g.get("status") or None,
                  "division": (us.get("division") or {}).get("title") or None,
                  "livebarn": lb.get("broadcastUrl") or None,
                  "link": f"https://gamesheetstats.com/seasons/{gs.SEASON}/games/{g['gameId']}"}
            if final:
                ev.update(final=True, us=us.get("goals"), them=them.get("goals"),
                          result=(us.get("result") or "").upper() or None)
            out.append(ev)
    return out, ok


def merge(gsev, club, gs_ok):
    """Drop club 12U/10U game entries that GameSheet covers; borrow their address."""
    def close(a, b, hours):
        return abs(datetime.fromisoformat(a) - datetime.fromisoformat(b)) <= timedelta(hours=hours)
    keep = []
    for c in club:
        if c["kind"] == "game" and c["team"] in gs_ok:
            twin = next((g for g in gsev if g["team"] == c["team"] and close(g["start"], c["start"], 3)), None)
            if twin:
                twin.setdefault("address", c.get("address"))
                continue
        keep.append(c)
    return gsev + keep


def name_addresses(evs):
    """Give club entries a rink name when GameSheet already pairs that street address with one."""
    norm = lambda a: re.sub(r"\W", "", (a or "").lower().replace(", us", ""))
    names = {}
    for e in evs:
        if e["src"] == "gamesheet" and e.get("address") and e.get("rink"):
            n = re.sub(r"\s+Rink\s*#?\d+$", "", e["rink"])
            k = norm(e["address"])
            if len(n) > len(names.get(k, "")):
                names[k] = n
    for e in evs:
        if e["src"] in ("club", "sportsengine") and not e.get("rink") and norm(e.get("address")) in names:
            e["rink"] = names[norm(e["address"])]
    return evs


def norm_title(title):
    """Lowercase, punctuation-free, leading team name removed. Used to pair calendars."""
    text = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
    return re.sub(r"^(?:12u|10u|8u|learn to play)\s+", "", text)


def event_day(iso):
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=gs.TZ)
    return dt.astimezone(gs.TZ).date()


def merge_sportsengine(evs, se_events):
    """Add SportsEngine 8U events without duplicating the club calendar.

    Same item: same America/Detroit calendar day and the same title (punctuation
    and a leading "8U" ignored). Each SportsEngine event consumes at most one
    Outlook 8U row, the one with the closest start, so two practices on one day
    stay two practices.

    8U-only rows (practices, jamborees) keep the SportsEngine copy and drop the
    Outlook copy. The rink name is filled in afterward from the street address,
    the same way other club events get one. Rows Outlook lists for 8U and for
    another team (Try Hockey, Picture Day, the Halloween skate) are one shared
    event: the Outlook rows stay, so they can collapse into a single multi-team
    card, and the SportsEngine copy is not added.

    An Outlook 8U row with no SportsEngine twin is kept. A SportsEngine row with
    no Outlook twin is added.
    """
    if not se_events:
        return evs
    used = set()
    kept = []
    for se in sorted(se_events, key=lambda e: e["start"]):
        day, title = event_day(se["start"]), norm_title(se.get("title"))
        if se.get("kind") in ("event", "tournament"):
            shared = any(
                c.get("src") == "club" and c.get("team") not in (None, "8U")
                and event_day(c["start"]) == day and norm_title(c.get("title")) == title
                for c in evs)
            if shared:
                continue
        cands = [i for i, c in enumerate(evs)
                 if i not in used and c.get("src") == "club" and c.get("team") == "8U"
                 and event_day(c["start"]) == day and norm_title(c.get("title")) == title]
        if cands:
            se_dt = datetime.fromisoformat(se["start"])
            best = min(cands, key=lambda i: abs(_aware(evs[i]["start"]) - se_dt))
            used.add(best)
            twin = evs[best]
            if twin.get("rink") and not se.get("rink"):
                se["rink"] = twin["rink"]
            if twin.get("address") and not se.get("address"):
                se["address"] = twin["address"]
        kept.append(dict(se))
    return [e for i, e in enumerate(evs) if i not in used] + kept


def _aware(iso):
    dt = datetime.fromisoformat(iso)
    return dt if dt.tzinfo else dt.replace(tzinfo=gs.TZ)


def previous_sportsengine(path):
    """8U events already embedded in a built page, used when the feed and cache are both empty."""
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return []
    m = re.search(r"const D=(\{.*?\});\nconst TZ", text)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    return [e for e in data.get("events", []) if e.get("src") == "sportsengine"]


def dedupe(evs):
    seen, out = set(), []
    for e in sorted(evs, key=lambda e: e["start"]):
        k = (e["team"], e["start"], (e.get("title") or e.get("opponent") or "").lower(),
             re.sub(r"\W", "", (e.get("address") or "").lower()))
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


def combine_shared(evs):
    """One card for a club event listed once per team at the same time and place (fundraisers, try hockey)."""
    groups, out = {}, []
    for e in evs:
        if e["src"] == "club" and e["kind"] in ("event", "tournament") and e["team"] in TEAM_ORDER:
            k = (e["start"], e["title"].lower().rstrip("!"), re.sub(r"\W", "", (e.get("address") or "").lower()))
            if k in groups:
                groups[k]["teams"].append(e["team"])
                continue
            e["teams"] = [e["team"]]
            groups[k] = e
        out.append(e)
    for e in groups.values():
        e["teams"] = [t for t in TEAM_ORDER if t in e["teams"]]
        if len(e["teams"]) > 1:
            e["team"] = "All teams" if len(e["teams"]) == len(TEAM_ORDER) else " + ".join(e["teams"])
    return out


REDIRECT = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>West Shore Wolves Schedule</title><link rel="canonical" href="%s">
<meta http-equiv="refresh" content="0; url=../"><meta name="robots" content="noindex">
<script>location.replace("../"+location.search+location.hash)</script></head>
<body><p>The schedule moved to <a href="../">%s</a>.</p></body></html>
""" % (SITE, SITE)


def write_cname():
    """GitHub Pages custom domain. Written every build so docs/CNAME is always present."""
    if os.path.abspath(OUT) == os.path.abspath(HERE):
        return  # local preview build, not the published docs/ folder
    path = os.path.join(OUT, "CNAME")
    if not os.path.isfile(path) or open(path).read().strip() != CUSTOM_DOMAIN:
        gs.write_atomic(path, CUSTOM_DOMAIN + "\n")


def write_schedule_redirect():
    """Old /schedule/ URL now forwards to the site root. Only touches OUT/schedule/."""
    sched = os.path.join(OUT, "schedule")
    if os.path.abspath(sched) == os.path.abspath(HERE):
        return
    os.makedirs(sched, exist_ok=True)
    old_assets = os.path.join(sched, "assets")  # left over from the first /schedule/ build
    if os.path.isdir(old_assets):
        shutil.rmtree(old_assets)
    gs.write_atomic(os.path.join(sched, "index.html"), REDIRECT)


def main():
    now = datetime.now(gs.TZ)
    gsev, gs_ok = gamesheet_events()
    try:
        raw = club_items(now.date() - timedelta(days=90), now.date() + timedelta(days=240))
        club = [classify_club(i) for i in raw if i.get("Subject")]
        club_ok = True
    except Exception as e:  # noqa: BLE001
        print(f"WARNING club calendar: {e}", file=sys.stderr)
        club, club_ok = [], False
    se, se_ok = gs.eight_u_events(now)
    if not se_ok and not se:
        se = previous_sportsengine(os.path.join(OUT, "index.html"))
        if se:
            print(f"8U SportsEngine: restored {len(se)} events from the previous page", file=sys.stderr)
    if not gsev and not club and not se:
        print("ERROR: no data from any source; page not rebuilt", file=sys.stderr)
        return 1
    merged = merge_sportsengine(merge(gsev, club, gs_ok), se)
    events = combine_shared(dedupe(name_addresses(merged)))
    present = {t for e in events for t in e.get("teams", [e["team"]])}
    teams = [t for t in TEAM_ORDER if t in present]
    teams += sorted(present - set(teams) - {"Open Rink"})
    if any(e["team"] == "Open Rink" for e in events):
        teams.append("Open Rink")
    data = {"built": now.isoformat(), "teams": teams, "events": events, "clubOk": club_ok,
            "gamesheetOk": gs_ok, "sportsengineOk": se_ok, "season": gs.SEASON,
            "feedBase": FEED_BASE, "clubPage": CLUB_PAGE,
            "seasonUrl": f"https://gamesheetstats.com/seasons/{gs.SEASON}"}
    tpl = open(os.path.join(HERE, "template.html"), encoding="utf-8").read()
    page = tpl.replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":")).replace("</", "<\\/"))
    os.makedirs(OUT, exist_ok=True)
    src_assets, dst_assets = os.path.join(HERE, "assets"), os.path.join(OUT, "assets")
    if os.path.abspath(src_assets) != os.path.abspath(dst_assets):
        shutil.copytree(src_assets, dst_assets, dirs_exist_ok=True)
    gs.write_atomic(os.path.join(OUT, "index.html"), page)
    write_schedule_redirect()
    n_se = sum(e.get("src") == "sportsengine" for e in events)
    print(f"Wrote {OUT}/index.html: {len(events)} events "
          f"({sum(e['src'] == 'gamesheet' for e in events)} GameSheet games, "
          f"{n_se} SportsEngine 8U, club calendar ok={club_ok}, sportsengine fresh={se_ok})")
    return 0


if __name__ == "__main__":
    # Fail-soft: the schedule page must never break feed generation. Any error leaves the
    # previously published page in place and exits 0 so the workflow keeps going.
    try:
        write_cname()
    except Exception as e:  # noqa: BLE001
        print(f"WARNING CNAME: {e}", file=sys.stderr)
    try:
        rc = main()
    except Exception as e:  # noqa: BLE001
        print(f"WARNING schedule page not rebuilt: {e!r}", file=sys.stderr)
        rc = 0
    sys.exit(0 if os.environ.get("SCHEDULE_FAIL_SOFT", "1") == "1" else rc)
