#!/usr/bin/env python3
"""Build RSS + ICS feeds of West Shore Wolves games from public schedules.

12U and 10U: the server-rendered game JSON embedded in
https://gamesheetstats.com/seasons/<season>/teams/<team>/schedule
8U has no GameSheet page (age). Its feed is the public SportsEngine iCal
calendar (SPORTSENGINE_8U). A failed SportsEngine fetch leaves
docs/wolves-8u.rss, docs/wolves-8u.ics, and docs/sportsengine-8u.json in place.
Standard library only. Writes into docs/ (served by GitHub Pages).
If a GameSheet team fetch fails, that team's old files are kept; exits nonzero
only if every GameSheet fetch fails.
"""
import html
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from zoneinfo import ZoneInfo

SEASON = 15537  # Adray Community Hockey Regular Season 2026/27
TEAMS = [  # (team id, label, file slug)
    (576127, "12U", "wolves-12u"),
    (576206, "10U", "wolves-10u"),
]
BASE_URL = "https://events.westshorewolves.com/"
CUSTOM_DOMAIN = "events.westshorewolves.com"  # GitHub Pages custom domain, kept in docs/CNAME
GAME_MINUTES = 75
TZ = ZoneInfo("America/Detroit")
# 8U is not on GameSheet. SportsEngine publishes the team calendar as iCal
# (the club shares it as webcal://; fetch the https URL).
SPORTSENGINE_8U = (
    "https://ical.sportngin.com/v3/calendar/ical?team_ids=11f18a93-0d45-5b32-b4f0-7ec931f1172f")
SLUG_8U = "wolves-8u"
CACHE_8U = "sportsengine-8u.json"
TBD_RE = re.compile(r"\s*time is TBD\s*", re.I)
TOURNAMENT_RE = re.compile(r"tournament|district|states|jamboree|showcase|classic|cup", re.I)
GAME_RE = re.compile(r"\b(?:at|vs\.?)\b", re.I)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")
CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.S)


def schedule_url(team):
    return f"https://gamesheetstats.com/seasons/{SEASON}/teams/{team}/schedule"


def fetch(url, tries=3, accept="text/html"):
    last = None
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
            if "Just a moment" in body[:5000] and "__next_f" not in body:
                raise RuntimeError("Cloudflare challenge page returned")
            return body
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (n + 1))
    raise RuntimeError(f"fetch failed for {url}: {last}")


def extract_games(page, team):
    payload = "".join(json.loads('"' + c + '"') for c in CHUNK_RE.findall(page))
    dec, games, i = json.JSONDecoder(), {}, 0
    while (i := payload.find('{"gameId":', i)) != -1:
        try:
            g, end = dec.raw_decode(payload, i)
        except ValueError:
            i += 10
            continue
        ids = (g.get("home", {}).get("id"), g.get("visitor", {}).get("id"))
        if team in ids and g.get("timeStampZulu"):
            games[g["gameId"]] = g
        i = end
    return sorted(games.values(), key=lambda g: g["timeStampZulu"])


def to_item(g, team, label):
    home, vis = g["home"], g["visitor"]
    is_home = home.get("id") == team
    us, them = (home, vis) if is_home else (vis, home)
    start = datetime.fromisoformat(g["timeStampZulu"].replace("Z", "+00:00"))
    local = start.astimezone(TZ)
    when = f"{local:%a %b} {local.day} {local:%I:%M %p}".replace(" 0", " ")
    loc = g.get("location") or "TBD"
    title = f"{when}: {label} {'vs' if is_home else 'at'} {them.get('title', 'TBD')} ({loc})"
    final = (g.get("status") or "").lower() in ("final", "completed", "complete", "closed")
    if final:
        res = (us.get("result") or "").upper()
        title += f" Final {us.get('goals', '?')}-{them.get('goals', '?')}" + (f" {res}" if res else "")
    desc = (f"{label} West Shore Wolves {'home vs' if is_home else 'away at'} {them.get('title', 'TBD')}. "
            f"{local:%A, %B} {local.day}, {local:%Y} at {local:%I:%M %p %Z}".replace(" 0", " ")
            + f", {loc}. Game #{g.get('number', '')}, status: {g.get('status', '')}.")
    if final:
        desc += f" Score: {vis.get('title')} {vis.get('goals')} - {home.get('title')} {home.get('goals')}."
    lb = ((g.get("data") or {}).get("broadcasters") or {}).get("livebarn") or {}
    if lb.get("broadcastUrl"):
        desc += f" LiveBarn: {lb['broadcastUrl']}"
    return {
        "uid": f"gamesheet-{g['gameId']}-{team}@tykoskin.github.io",
        "title": title, "desc": desc, "start": start, "local": local,
        "loc": loc, "link": schedule_url(team), "final": final, "label": label,
    }


def ics_esc(t):
    return t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line):
    out, b = [], line.encode("utf-8")
    while len(b) > 75:
        cut = 75
        while (b[cut] & 0xC0) == 0x80:
            cut -= 1
        out.append(b[:cut].decode())
        b = b" " + b[cut:]
    out.append(b.decode())
    return "\r\n".join(out)


def write_atomic(path, text, newline="\n"):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)
    os.replace(tmp, path)


def season_bounds(now=None):
    """Current hockey season: August 1 through the following August 1.

    Before August, the season that opened last August is still current.
    GameSheet is already limited to the current season id. SportsEngine's
    public calendar also carries last season (the sample has Jan/Feb 2026),
    and those events are dropped.
    """
    now = now.astimezone(TZ) if now else datetime.now(TZ)
    year = now.year if now.month >= 8 else now.year - 1
    return datetime(year, 8, 1, tzinfo=TZ), datetime(year + 1, 8, 1, tzinfo=TZ)


def unfold_ics(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        elif line:
            lines.append(line)
    return lines


def ics_unesc(text):
    out, i = [], 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            n = text[i + 1]
            out.append("\n" if n in "nN" else n)
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def ics_vevents(text):
    """List of VEVENT property lists: (NAME, params, unescaped value)."""
    vevents, cur = [], None
    for line in unfold_ics(text):
        if line == "BEGIN:VEVENT":
            cur = []
        elif line == "END:VEVENT" and cur is not None:
            vevents.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            left, val = line.split(":", 1)
            bits = left.split(";")
            params = {}
            for bit in bits[1:]:
                if "=" in bit:
                    k, v = bit.split("=", 1)
                    params[k.upper()] = v.strip().strip('"')
            cur.append((bits[0].upper(), params, ics_unesc(val)))
    return vevents


def parse_ics_dt(params, val):
    """Return (aware datetime, date_only). Unknown timezones fall back to Detroit."""
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", val or ""):
        return datetime.strptime(val[:8], "%Y%m%d").replace(tzinfo=TZ), True
    zulu = val.endswith("Z")
    raw = val[:-1] if zulu else val
    if len(raw) == 13:  # YYYYMMDDTHHMM
        raw += "00"
    dt = datetime.strptime(raw[:15], "%Y%m%dT%H%M%S")
    if zulu:
        return dt.replace(tzinfo=timezone.utc), False
    try:
        tz = ZoneInfo(params.get("TZID") or "America/Detroit")
    except Exception:  # noqa: BLE001
        tz = TZ
    return dt.replace(tzinfo=tz), False


def classify_title(title, team="8U"):
    if re.search(r"practice", title, re.I):
        return "practice"
    if TOURNAMENT_RE.search(title):
        return "tournament"
    if GAME_RE.search(title) and team in ("12U", "10U", "8U"):
        return "game"
    return "event"


def clean_summary(summary):
    """Strip a trailing 'time is TBD' note. The flag is separate from the clock."""
    text = summary or ""
    tbd = bool(TBD_RE.search(text))
    title = re.sub(r"\s{2,}", " ", TBD_RE.sub(" ", text)).strip(" -")
    return title or "8U", tbd


def parse_sportsengine(text, now=None, team="8U"):
    """SportsEngine VEVENTs in the current season, in the site's event shape."""
    start_bound, end_bound = season_bounds(now)
    events = []
    for props in ics_vevents(text):
        bag = {}
        for name, params, val in props:
            bag.setdefault(name, (params, val))
        if "DTSTART" not in bag or "SUMMARY" not in bag:
            continue
        start, date_only = parse_ics_dt(*bag["DTSTART"])
        start = start.astimezone(TZ)
        if not (start_bound <= start < end_bound):
            continue
        end = None
        if "DTEND" in bag:
            end_dt, _date_only = parse_ics_dt(*bag["DTEND"])
            end_dt = end_dt.astimezone(TZ)
            if end_dt > start:
                end = end_dt
        title, tbd = clean_summary(bag["SUMMARY"][1])
        loc = (bag.get("LOCATION") or (None, ""))[1].strip() or None
        uid = (bag.get("UID") or (None, ""))[1].strip() or f"sportsengine-{team}-{start:%Y%m%dT%H%M%S}"
        kind = classify_title(title, team)
        ev = {"src": "sportsengine", "team": team, "kind": kind, "title": title,
              "start": start.isoformat(), "end": end.isoformat() if end else None,
              "tbd": tbd or date_only, "address": loc, "uid": uid}
        if kind == "game":
            home = re.match(rf"^(.+?)\s+at\s+{team}$", title, re.I)
            away = re.match(rf"^(?:{team}\s+)?at\s+(.+)$", title, re.I)
            versus = re.match(rf"^(?:{team}\s+)?vs\.?\s+(.+)$", title, re.I)
            if home:
                ev.update(home=True, opponent=home.group(1).strip())
            elif versus:
                ev.update(home=True, opponent=versus.group(1).strip())
            elif away:
                ev.update(home=False, opponent=away.group(1).strip())
        events.append(ev)
    events.sort(key=lambda e: (e["start"], e["title"]))
    return events


def cache_8u_path():
    return os.path.join(OUT, CACHE_8U)


def load_8u_cache():
    try:
        with open(cache_8u_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def eight_u_events(now=None):
    """Return (events, fresh). A failed fetch keeps the previous cache instead of []."""
    try:
        body = fetch(SPORTSENGINE_8U, accept="text/calendar, text/plain, */*")
        events = parse_sportsengine(body, now)
        if not events:
            raise RuntimeError("no current-season events in SportsEngine feed")
        write_atomic(cache_8u_path(), json.dumps(events, indent=2) + "\n")
        return events, True
    except Exception as e:  # noqa: BLE001
        cached = load_8u_cache()
        print(f"WARNING 8U SportsEngine: {e}; keeping {len(cached)} cached events", file=sys.stderr)
        return cached, False


def _when_label(local, tbd=False):
    if tbd:
        return f"{local:%a %b} {local.day} TBD".replace(" 0", " ")
    return f"{local:%a %b} {local.day} {local:%I:%M %p}".replace(" 0", " ")


def sportsengine_feed_items(events):
    items = []
    for ev in events:
        start = datetime.fromisoformat(ev["start"])
        local = start.astimezone(TZ)
        end = datetime.fromisoformat(ev["end"]) if ev.get("end") else None
        summary = f"{ev['team']} {ev['title']}"
        when = _when_label(local, ev.get("tbd"))
        detail = f"{local:%A, %B} {local.day}, {local:%Y}".replace(" 0", " ")
        if ev.get("tbd"):
            detail += " (time TBD)"
        else:
            clock = f"{local:%I:%M %p %Z}".replace(" 0", " ")
            if end:
                clock += " to " + f"{end.astimezone(TZ):%I:%M %p}".replace(" 0", " ")
            detail += f" at {clock}"
        desc = f"{ev['team']} West Shore Wolves: {ev['title']}. {detail}."
        if ev.get("address"):
            desc += f" {ev['address']}."
        desc += " From the SportsEngine 8U calendar."
        items.append({
            "uid": ev.get("uid") or f"sportsengine-8u-{start:%Y%m%dT%H%M%S}",
            "title": f"{when}: {summary}", "desc": desc, "start": start, "end": end,
            "local": local, "loc": ev.get("rink") or ev.get("address") or "TBD",
            "link": BASE_URL, "tbd": bool(ev.get("tbd")), "label": ev["team"],
        })
    return items


def write_feeds(items, slug, title, page, source_note=None, prodid=None):
    now = datetime.now(timezone.utc)
    source_note = source_note or f"{title}, from GameSheet. Times are Eastern (America/Detroit)."
    prodid = prodid or "-//West Shore Wolves//GameSheet feed//EN"
    rss = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>',
           f"<title>{html.escape(title)}</title>",
           f"<link>{html.escape(page)}</link>",
           f'<atom:link href="{BASE_URL}{slug}.rss" rel="self" type="application/rss+xml"/>',
           f"<description>{html.escape(source_note)}</description>",
           f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>", "<ttl>120</ttl>"]
    for it in items:
        rss.append("<item>"
                   f"<title>{html.escape(it['title'])}</title>"
                   f"<link>{html.escape(it['link'])}</link>"
                   f'<guid isPermaLink="false">{html.escape(it["uid"])}</guid>'
                   f"<pubDate>{format_datetime(it['start'])}</pubDate>"
                   f"<description>{html.escape(it['desc'])}</description>"
                   "</item>")
    rss.append("</channel></rss>")
    stamp = f"{now:%Y%m%dT%H%M%SZ}"
    ics = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{prodid}",
           "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{ics_esc(title)}",
           "X-WR-TIMEZONE:America/Detroit", "REFRESH-INTERVAL;VALUE=DURATION:PT2H",
           "X-PUBLISHED-TTL:PT2H"]
    for it in items:
        end = it.get("end") or (it["start"] + timedelta(minutes=GAME_MINUTES))
        summary = it["title"].split(": ", 1)[1] if ": " in it["title"] else it["title"]
        body = ["BEGIN:VEVENT", f"UID:{it['uid']}", f"DTSTAMP:{stamp}"]
        if it.get("tbd"):
            local = it.get("local") or it["start"].astimezone(TZ)
            nxt = local.date() + timedelta(days=1)
            body += [f"DTSTART;VALUE=DATE:{local:%Y%m%d}", f"DTEND;VALUE=DATE:{nxt:%Y%m%d}"]
        else:
            # SportsEngine instants are America/Detroit. GameSheet instants are already UTC.
            # Both must be written as Zulu; formatting a local datetime and appending Z is wrong.
            body += [f"DTSTART:{it['start'].astimezone(timezone.utc):%Y%m%dT%H%M%SZ}",
                     f"DTEND:{end.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"]
        body += [f"SUMMARY:{ics_esc(summary)}", f"LOCATION:{ics_esc(it['loc'])}",
                 f"DESCRIPTION:{ics_esc(it['desc'])}", f"URL:{it['link']}", "END:VEVENT"]
        ics += body
    ics.append("END:VCALENDAR")
    write_atomic(os.path.join(OUT, f"{slug}.rss"), "\n".join(rss) + "\n")
    write_atomic(os.path.join(OUT, f"{slug}.ics"), "\r\n".join(fold(l) for l in ics) + "\r\n", newline="")


def write_index(items, status):
    now = datetime.now(TZ)
    feeds = "".join(
        f"<li>{lbl}: <a href='../{s}.rss'>RSS</a> | <a href='../{s}.ics'>Calendar (ICS)</a> "
        f"<code>{BASE_URL}{s}.ics</code></li>"
        for lbl, s in [("12U", "wolves-12u"), ("10U", "wolves-10u"),
                       ("8U", "wolves-8u"), ("All teams", "wolves")])
    up = [it for it in items if it["start"] + timedelta(minutes=GAME_MINUTES) >= datetime.now(timezone.utc)]
    rows = "".join(
        f"<tr><td>{it['label']}</td><td>{html.escape(it['title'].split(': ', 1)[0])}</td>"
        f"<td>{html.escape(it['title'].split(': ', 1)[1])}</td></tr>" for it in up) or \
        "<tr><td colspan='3'>No upcoming games listed.</td></tr>"
    note = "".join(f"<li>{html.escape(k)}: {html.escape(v)}</li>" for k, v in status.items())
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>West Shore Wolves Game Feeds</title>
<link rel="alternate" type="application/rss+xml" title="Wolves games" href="../wolves.rss">
<style>body{{font-family:system-ui,sans-serif;max-width:860px;margin:2em auto;padding:0 1em}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:6px;text-align:left}}
code{{font-size:.8em;color:#555}}</style></head><body>
<p><a href="../">&larr; Back to the schedule</a></p>
<h1>West Shore Wolves Game Feeds</h1>
<p>12U and 10U schedules from GameSheet (Adray Community Hockey 2026/27). 8U schedule from SportsEngine. Updated every 2 hours. Times are Eastern.</p>
<h2>Subscribe</h2><ul>{feeds}</ul>
<p>Calendar: copy an ICS link and add it as a subscribed calendar (Google: Other calendars, From URL; iPhone: Settings, Calendar, Accounts, Add Subscribed Calendar).</p>
<h2>Upcoming games</h2><table><tr><th>Team</th><th>When</th><th>Game</th></tr>{rows}</table>
<p><small>Last updated {now:%b} {now.day}, {now:%Y} {now.hour % 12 or 12}:{now:%M %p %Z}.</small></p><ul><small>{note}</small></ul>
</body></html>
"""
    os.makedirs(os.path.join(OUT, "feeds"), exist_ok=True)
    write_atomic(os.path.join(OUT, "feeds", "index.html"), page)


def write_cname():
    """Keep the Pages custom domain file in place (never removed by this script)."""
    path = os.path.join(OUT, "CNAME")
    try:
        if not os.path.isfile(path) or open(path).read().strip() != CUSTOM_DOMAIN:
            write_atomic(path, CUSTOM_DOMAIN + "\n")
    except Exception as e:  # noqa: BLE001
        print(f"WARNING CNAME: {e}", file=sys.stderr)


def main():
    os.makedirs(OUT, exist_ok=True)
    write_cname()
    all_items, status, ok = [], {}, 0
    for team, label, slug in TEAMS:
        try:
            games = extract_games(fetch(schedule_url(team)), team)
            if not games:
                raise RuntimeError("no games found in page")
            items = [to_item(g, team, label) for g in games]
            write_feeds(items, slug, f"West Shore Wolves {label} Games", schedule_url(team))
            all_items += items
            ok += 1
            status[label] = f"{len(items)} games"
            print(f"{label}: {len(items)} games written to docs/{slug}.rss/.ics")
        except Exception as e:  # noqa: BLE001
            status[label] = "fetch failed, kept previous files"
            print(f"WARNING {label}: {e}; keeping previous files", file=sys.stderr)
    try:
        se, fresh = eight_u_events()
        if fresh:
            items = sportsengine_feed_items(se)
            n = len(items)
            write_feeds(items, SLUG_8U, "West Shore Wolves 8U", BASE_URL,
                        source_note=("West Shore Wolves 8U schedule from SportsEngine. "
                                     "Times are Eastern (America/Detroit)."),
                        prodid="-//West Shore Wolves//SportsEngine feed//EN")
            status["8U"] = f"{n} events from SportsEngine"
            print(f"8U: {n} SportsEngine events written to docs/{SLUG_8U}.rss/.ics")
        else:
            status["8U"] = "fetch failed, kept previous files"
    except Exception as e:  # noqa: BLE001
        status["8U"] = "fetch failed, kept previous files"
        print(f"WARNING 8U: {e}; keeping previous files", file=sys.stderr)
    if ok == 0:
        print("ERROR: all fetches failed; nothing updated", file=sys.stderr)
        return 1
    if ok == len(TEAMS):  # only rebuild the combined feed when every team is fresh
        all_items.sort(key=lambda x: x["start"])
        write_feeds(all_items, "wolves", "West Shore Wolves Games (all teams)",
                    f"https://gamesheetstats.com/seasons/{SEASON}/schedule")
        write_index(all_items, status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
