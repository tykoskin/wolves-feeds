# West Shore Wolves game feeds

RSS and calendar (ICS) feeds for the West Shore Wolves. 12U and 10U games come from the public GameSheet schedule pages (Adray Community Hockey, season 15537). 8U has no GameSheet feed; that schedule comes from the team's public SportsEngine calendar.

## Feeds
| Team | RSS | Calendar |
|---|---|---|
| 12U | https://tykoskin.github.io/wolves-feeds/wolves-12u.rss | https://tykoskin.github.io/wolves-feeds/wolves-12u.ics |
| 10U | https://tykoskin.github.io/wolves-feeds/wolves-10u.rss | https://tykoskin.github.io/wolves-feeds/wolves-10u.ics |
| 8U | https://tykoskin.github.io/wolves-feeds/wolves-8u.rss | https://tykoskin.github.io/wolves-feeds/wolves-8u.ics |
| All (12U and 10U games) | https://tykoskin.github.io/wolves-feeds/wolves.rss | https://tykoskin.github.io/wolves-feeds/wolves.ics |

Schedule page: https://events.westshorewolves.com/ (built by `site/build_site.py`; `/schedule/` redirects there).
Feed links page: https://events.westshorewolves.com/feeds/

The feeds are also reachable at https://events.westshorewolves.com/<file> (same file names). `docs/CNAME` holds the custom domain and is rewritten by the build; do not delete it.

## How it works
- `gamesheet_feed.py` (Python 3.9+, standard library only) downloads
  `https://gamesheetstats.com/seasons/15537/teams/<team>/schedule`, reads the game data embedded in the page, and writes the feeds into `docs/`.
- 8U is downloaded from SportsEngine's public iCal
  (`https://ical.sportngin.com/v3/calendar/ical?team_ids=11f18a93-0d45-5b32-b4f0-7ec931f1172f`, the `webcal://` link over https). Events before the current season (August 1 through the next August 1) are dropped, including last season's January and February entries. A summary that says "time is TBD" is cleaned, and that event is marked all-day. The script writes `docs/wolves-8u.rss`, `docs/wolves-8u.ics`, and `docs/sportsengine-8u.json`.
- `site/build_site.py` puts those 8U events on the schedule page with the club Outlook calendar. An 8U-only Outlook row with the same day and title (practices, jamborees) is replaced by the SportsEngine row. An event Outlook lists for 8U and another team (Try Hockey, Picture Day, fundraisers) stays one club card. Outlook 8U rows that SportsEngine does not list are kept.
- Times are shown in America/Detroit. GameSheet calendar events use UTC start times and a 75 minute default length. 8U events use the SportsEngine end time.
- If one GameSheet page or the SportsEngine calendar cannot be fetched, that feed's old files are kept. The schedule page keeps the previous 8U events instead of dropping them. The script exits with an error only if every GameSheet fetch fails.
- `.github/workflows/update-feeds.yml` runs every 2 hours (and on demand from the Actions tab) and commits any changed files in `docs/`.

## Setup
1. Create a public repo `tykoskin/wolves-feeds` and upload these files (including `.github/` and `docs/`).
2. Settings > Pages > Source: Deploy from a branch, Branch `main`, folder `/docs`.
3. Settings > Actions > General > Workflow permissions: Read and write.
4. Actions tab > Update feeds > Run workflow, to test.

## Changing teams or season
Edit `SEASON` and `TEAMS` at the top of `gamesheet_feed.py`. Team IDs are in the GameSheet stats URL (`/seasons/<season>/teams/<team>/...`).
