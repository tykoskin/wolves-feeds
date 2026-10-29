# West Shore Wolves game feeds

RSS and calendar (ICS) feeds of West Shore Wolves 12U and 10U games, built from the public GameSheet schedule pages (Adray Community Hockey, season 15537).

## Feeds
| Team | RSS | Calendar |
|---|---|---|
| 12U | https://tykoskin.github.io/wolves-feeds/wolves-12u.rss | https://tykoskin.github.io/wolves-feeds/wolves-12u.ics |
| 10U | https://tykoskin.github.io/wolves-feeds/wolves-10u.rss | https://tykoskin.github.io/wolves-feeds/wolves-10u.ics |
| All | https://tykoskin.github.io/wolves-feeds/wolves.rss | https://tykoskin.github.io/wolves-feeds/wolves.ics |

Schedule page: https://events.westshorewolves.com/ (built by `site/build_site.py`; `/schedule/` redirects there).
Feed links page: https://events.westshorewolves.com/feeds/

The feeds are also reachable at https://events.westshorewolves.com/<file> (same file names). `docs/CNAME` holds the custom domain and is rewritten by the build; do not delete it.

## How it works
- `gamesheet_feed.py` (Python 3.9+, standard library only) downloads
  `https://gamesheetstats.com/seasons/15537/teams/<team>/schedule`, reads the game data embedded in the page, and writes the feeds into `docs/`.
- Times are shown in America/Detroit. Calendar events use UTC start times and a 75 minute default length.
- If one team's page cannot be fetched, its old files are kept. The script exits with an error only if every fetch fails.
- `.github/workflows/update-feeds.yml` runs every 2 hours (and on demand from the Actions tab) and commits any changed files in `docs/`.

## Setup
1. Create a public repo `tykoskin/wolves-feeds` and upload these files (including `.github/` and `docs/`).
2. Settings > Pages > Source: Deploy from a branch, Branch `main`, folder `/docs`.
3. Settings > Actions > General > Workflow permissions: Read and write.
4. Actions tab > Update feeds > Run workflow, to test.

## Changing teams or season
Edit `SEASON` and `TEAMS` at the top of `gamesheet_feed.py`. Team IDs are in the GameSheet stats URL (`/seasons/<season>/teams/<team>/...`).
