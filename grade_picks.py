# grade_picks.py -- Official Model Record grader for The Lens.
#
# Runs nightly via GitHub Actions. Fetches ungraded picks from Supabase
# (pick_history), pulls MLB Stats API box scores, grades them with the SAME
# rules the UI displays, and writes results using the SERVICE ROLE key.
#
# Grading rules (must stay in sync with gradePendingPicks in index.html):
#   pitcher: hit if IP >= 4 and K >= 5, else miss
#   hitter:  hit if HR >= 1, else miss
#
# Env vars (set as GitHub Actions secrets):
#   SUPABASE_URL          e.g. https://xxxx.supabase.co
#   SUPABASE_SERVICE_KEY  the service_role key (NEVER put this in index.html)

import os
import sys
import json
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
MLB_API = "https://statsapi.mlb.com/api/v1"

# "Today" in US/Eastern (matches todayStr() in the app). ET is UTC-4 or UTC-5;
# using -5 (EST) year-round is safe here because the action runs in the early
# morning ET, well after midnight in both DST states.
ET_TODAY = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%d")


def http_json(url, method="GET", body=None, headers=None, ok_codes=(200, 201, 204)):
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, data=data, method=method, headers=headers or {})
    try:
        with urlopen(req, timeout=30) as res:
            if res.status not in ok_codes:
                raise RuntimeError(f"{method} {url} -> HTTP {res.status}")
            raw = res.read().decode()
            return json.loads(raw) if raw else None
    except HTTPError as e:
        raise RuntimeError(f"{method} {url} -> HTTP {e.code}: {e.read().decode()[:300]}")
    except URLError as e:
        raise RuntimeError(f"{method} {url} -> {e.reason}")


def sb(path, method="GET", body=None, prefer="return=representation"):
    return http_json(
        f"{SUPABASE_URL}/rest/v1/{path}",
        method=method,
        body=body,
        headers={
            "apikey": SERVICE_KEY,
            "Authorization": f"Bearer {SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": prefer,
        },
    )


def parse_ip(val):
    """MLB IP strings like '5.2' mean 5 and 2/3 -- but the app's client-side
    grader uses parseFloat, so we mirror that exactly to keep grades identical."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def grade_pick(pick, players):
    name = (pick.get("player_name") or "").strip().lower()
    if pick["type"] == "pitcher":
        for pl in players:
            if (pl.get("person", {}).get("fullName", "").strip().lower() == name
                    and pl.get("stats", {}).get("pitching")):
                st = pl["stats"]["pitching"]
                ks = int(st.get("strikeOuts") or 0)
                ip = parse_ip(st.get("inningsPitched"))
                result = "hit" if (ip >= 4 and ks >= 5) else "miss"
                return {"result": result, "actual_ks": ks, "actual_hr": None}
        return {"result": "miss", "actual_ks": None, "actual_hr": None}

    if pick["type"] == "hitter":
        for pl in players:
            if (pl.get("person", {}).get("fullName", "").strip().lower() == name
                    and pl.get("stats", {}).get("batting")):
                hr = int(pl["stats"]["batting"].get("homeRuns") or 0)
                result = "hit" if hr >= 1 else "miss"
                return {"result": result, "actual_ks": None, "actual_hr": hr}
        return {"result": "miss", "actual_ks": None, "actual_hr": None}

    return None


def main():
    # Ungraded picks from BEFORE today (never grade in-progress days)
    picks = sb(
        f"pick_history?result=is.null&date=lt.{ET_TODAY}"
        f"&game_pk=not.is.null&order=date.desc&limit=500"
    ) or []
    print(f"[grade] {len(picks)} ungraded picks before {ET_TODAY}")
    if not picks:
        return

    by_game = {}
    for p in picks:
        by_game.setdefault(p["game_pk"], []).append(p)

    graded = failed = 0
    for game_pk, game_picks in by_game.items():
        try:
            box = http_json(f"{MLB_API}/game/{game_pk}/boxscore")
        except RuntimeError as e:
            print(f"[grade] boxscore failed for {game_pk}: {e}")
            failed += len(game_picks)
            continue

        # Only grade if the game actually went final -- a postponed/suspended
        # game shouldn't turn every pick into a 'miss'.
        teams = box.get("teams", {})
        players = list(teams.get("away", {}).get("players", {}).values()) + \
                  list(teams.get("home", {}).get("players", {}).values())
        if not players:
            print(f"[grade] no player data for {game_pk} (postponed?) -- skipping")
            failed += len(game_picks)
            continue

        for p in game_picks:
            g = grade_pick(p, players)
            if g is None:
                continue
            try:
                sb(
                    f"pick_history?id=eq.{p['id']}",
                    method="PATCH",
                    body=g,
                    prefer="return=minimal",
                )
                graded += 1
            except RuntimeError as e:
                print(f"[grade] write failed for pick {p['id']}: {e}")
                failed += 1

    print(f"[grade] done -- {graded} graded, {failed} skipped/failed")
    # Non-zero exit only on total failure so the Action surfaces real outages
    if graded == 0 and failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
