#!/usr/bin/env python3
"""Build RSS + ICS feeds of West Shore Wolves games from GameSheet's public stats pages.

Source: the server-rendered game JSON embedded in
https://gamesheetstats.com/seasons/<season>/teams/<team>/schedule
Standard library only. Writes into docs/ (served by GitHub Pages).
If a team fetch fails, that team's old files are kept; exits nonzero only if every fetch fails.
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
BASE_URL = "https://tykoskin.github.io/wolves-feeds/"
GAME_MINUTES = 75
TZ = ZoneInfo("America/Detroit")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")
CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.S)


def schedule_url(team):
    return f"https://gamesheetstats.com/seasons/{SEASON}/teams/{team}/schedule"


def fetch(url, tries=3):
    last = None
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
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


def write_feeds(items, slug, title, page):
    now = datetime.now(timezone.utc)
    rss = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>',
           f"<title>{html.escape(title)}</title>",
           f"<link>{html.escape(page)}</link>",
           f'<atom:link href="{BASE_URL}{slug}.rss" rel="self" type="application/rss+xml"/>',
           f"<description>{html.escape(title)}, from GameSheet. Times are Eastern (America/Detroit).</description>",
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
    ics = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//West Shore Wolves//GameSheet feed//EN",
           "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{ics_esc(title)}",
           "X-WR-TIMEZONE:America/Detroit", "REFRESH-INTERVAL;VALUE=DURATION:PT2H",
           "X-PUBLISHED-TTL:PT2H"]
    for it in items:
        end = it["start"] + timedelta(minutes=GAME_MINUTES)
        ics += ["BEGIN:VEVENT", f"UID:{it['uid']}", f"DTSTAMP:{stamp}",
                f"DTSTART:{it['start']:%Y%m%dT%H%M%SZ}", f"DTEND:{end:%Y%m%dT%H%M%SZ}",
                f"SUMMARY:{ics_esc(it['title'].split(': ', 1)[1])}",
                f"LOCATION:{ics_esc(it['loc'])}", f"DESCRIPTION:{ics_esc(it['desc'])}",
                f"URL:{it['link']}", "END:VEVENT"]
    ics.append("END:VCALENDAR")
    write_atomic(os.path.join(OUT, f"{slug}.rss"), "\n".join(rss) + "\n")
    write_atomic(os.path.join(OUT, f"{slug}.ics"), "\r\n".join(fold(l) for l in ics) + "\r\n", newline="")


def write_index(items, status):
    now = datetime.now(TZ)
    feeds = "".join(
        f"<li>{lbl}: <a href='{s}.rss'>RSS</a> | <a href='{s}.ics'>Calendar (ICS)</a> "
        f"<code>{BASE_URL}{s}.ics</code></li>"
        for lbl, s in [("12U", "wolves-12u"), ("10U", "wolves-10u"), ("All teams", "wolves")])
    up = [it for it in items if it["start"] + timedelta(minutes=GAME_MINUTES) >= datetime.now(timezone.utc)]
    rows = "".join(
        f"<tr><td>{it['label']}</td><td>{html.escape(it['title'].split(': ', 1)[0])}</td>"
        f"<td>{html.escape(it['title'].split(': ', 1)[1])}</td></tr>" for it in up) or \
        "<tr><td colspan='3'>No upcoming games listed.</td></tr>"
    note = "".join(f"<li>{html.escape(k)}: {html.escape(v)}</li>" for k, v in status.items())
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>West Shore Wolves Game Feeds</title>
<link rel="alternate" type="application/rss+xml" title="Wolves games" href="wolves.rss">
<style>body{{font-family:system-ui,sans-serif;max-width:860px;margin:2em auto;padding:0 1em}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:6px;text-align:left}}
code{{font-size:.8em;color:#555}}</style></head><body>
<h1>West Shore Wolves Game Feeds</h1>
<p>Schedules from GameSheet (Adray Community Hockey 2026/27). Updated every 2 hours. Times are Eastern.</p>
<h2>Subscribe</h2><ul>{feeds}</ul>
<p>Calendar: copy an ICS link and add it as a subscribed calendar (Google: Other calendars, From URL; iPhone: Settings, Calendar, Accounts, Add Subscribed Calendar).</p>
<h2>Upcoming games</h2><table><tr><th>Team</th><th>When</th><th>Game</th></tr>{rows}</table>
<p><small>Last updated {now:%b} {now.day}, {now:%Y} {now.hour % 12 or 12}:{now:%M %p %Z}.</small></p><ul><small>{note}</small></ul>
</body></html>
"""
    write_atomic(os.path.join(OUT, "index.html"), page)


def main():
    os.makedirs(OUT, exist_ok=True)
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
