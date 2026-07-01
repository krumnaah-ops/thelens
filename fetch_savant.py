#!/usr/bin/env python3
"""
fetch_savant.py — The Lens data fetcher
Uses BULK endpoints — runs in under 3 minutes total.

Savant CSV (pitcher/hitter analytics — single request each):
  savant-arsenal.json, savant-expected.json, savant-percentiles.json
  savant-hitter-expected.json, savant-hitter-statcast.json, savant-hitter-percentiles.json

MLB Stats API BULK (SB model — one request per stat type, not per player):
  savant-pop-time.json     : all catchers CS% in one call
  savant-pitch-tempo.json  : all pitchers SB/9 + runners-on splits in two calls
  savant-baserunning.json  : all hitters SB stats in one call
  savant-sb-leaders.json   : SB leaders leaderboard
  savant-pitcher-sb.json   : alias of pitch-tempo

FIXES vs previous version:
  - expected_statistics: min lowered from 25 → 1 (min=25 filtered almost everyone early season)
  - pitch-arsenal-stats: min lowered to 0, added fallback URL without min param
  - plate-discipline: endpoint was 404; replaced with Savant custom leaderboard CSV
    which includes o_swing%, z_swing%, whiff%, contact% etc
  - Added daily schedule cron comment (add to fetch-savant.yml)
"""

import csv
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from io import StringIO

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
os.makedirs(DATA_DIR, exist_ok=True)

SEASON = datetime.now().year
MLB_BASE = 'https://statsapi.mlb.com/api/v1'

SAVANT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'text/csv,application/json,*/*',
    'Referer': 'https://baseballsavant.mlb.com/',
    'Accept-Language': 'en-US,en;q=0.9',
}
MLB_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; TheLens/1.0)',
    'Accept': 'application/json',
}


def fetch_url(url, headers=None, retries=3, delay=3):
    headers = headers or SAVANT_HEADERS
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=45) as r:
                return r.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            print(f'  HTTP {e.code} attempt {attempt+1}: {url[:90]}')
        except Exception as e:
            print(f'  Error attempt {attempt+1}: {e}')
        if attempt < retries - 1:
            time.sleep(delay)
    return None


def mlb_get(path, retries=3):
    """Single MLB Stats API GET — returns parsed JSON or None."""
    url = f'{MLB_BASE}/{path}'
    text = fetch_url(url, headers=MLB_HEADERS, retries=retries)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def parse_csv(text):
    if not text or not text.strip():
        return []
    # Strip UTF-8 BOM if present (Savant CSVs often have \ufeff at start)
    text = text.lstrip('\ufeff').lstrip('\xef\xbb\xbf')
    # Skip any non-CSV preamble (HTML error pages etc)
    lines = text.strip().splitlines()
    # Find the header line (contains a comma and looks like CSV, not HTML)
    start = 0
    for i, line in enumerate(lines):
        if ',' in line and not line.startswith('<'):
            start = i
            break
    clean = '\n'.join(lines[start:])
    rows = list(csv.DictReader(StringIO(clean)))
    # Strip BOM from all keys in every row (in case it survived)
    cleaned = []
    for row in rows:
        cleaned.append({k.lstrip('\ufeff').strip(): v for k, v in row.items()})
    return cleaned


def safe_float(val, default=None):
    try:
        return float(val) if val not in (None, '', 'null', 'NA', 'N/A') else default
    except (ValueError, TypeError):
        return default


def safe_int(val, default=None):
    try:
        return int(float(val)) if val not in (None, '', 'null', 'NA', 'N/A') else default
    except (ValueError, TypeError):
        return default


def write_json(filename, data):
    path = os.path.join(DATA_DIR, filename)
    with open(path, 'w') as f:
        json.dump(data, f, separators=(',', ':'))
    size = os.path.getsize(path)
    n = len(data) if isinstance(data, (dict, list)) else '?'
    print(f'  ✓ {filename} ({size/1024:.1f} KB, {n} entries)')


# ══════════════════════════════════════════════════
# SAVANT CSV FETCHERS
# ══════════════════════════════════════════════════

def fetch_pitcher_arsenal():
    """
    FIX: min lowered from 25 → 0, added fallback URL without min param.
    Also added broader field name coverage for 2026 CSV column changes.
    """
    print('\n[1/11] Pitcher arsenal (Savant CSV)...')
    urls = [
        f'https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats?type=pitcher&pitchType=&year={SEASON}&team=&min=0&csv=true',
        f'https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats?type=pitcher&pitchType=&year={SEASON}&team=&csv=true',
        f'https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats?type=pitcher&year={SEASON}&min=0&csv=true',
    ]
    text = None
    for url in urls:
        text = fetch_url(url)
        if text:
            rows = parse_csv(text)
            if rows:
                print(f'  Got {len(rows)} rows from {url[:70]}')
                # Debug: show columns on first successful fetch
                print(f'  Columns: {list(rows[0].keys())[:15]}')
                break
            else:
                text = None  # empty CSV — try next URL

    if not text:
        print('  arsenal: all URLs failed or returned empty')
        return {}

    result = {}
    for row in parse_csv(text):
        # Try multiple possible player ID column names
        pid = (safe_int(row.get('player_id')) or
               safe_int(row.get('pitcher_id')) or
               safe_int(row.get('IDfg')))
        if not pid:
            continue
        if pid not in result:
            result[pid] = {'pitches': [], 'player_name': f"{row.get('first_name','')} {row.get('last_name','')}".strip()}
        result[pid]['pitches'].append({
            'pitch_type': row.get('pitch_type', '') or row.get('pitch_name', ''),
            'pitch_name': row.get('pitch_name', ''),
            'run_value':  safe_float(row.get('run_value_per100') or row.get('run_value') or row.get('rv100')),
            'pa':         safe_int(row.get('pa') or row.get('pitches')),
            'usage_pct':  safe_float(row.get('pitch_usage') or row.get('pitch_percent') or row.get('usage_pct')),
            'avg_speed':  safe_float(row.get('avg_speed') or row.get('release_speed') or row.get('velocity')),
            'avg_spin':   safe_float(row.get('avg_spin') or row.get('spin_rate')),
            'whiff_pct':  safe_float(row.get('whiff_percent') or row.get('whiff_pct') or row.get('whiff%')),
            'k_pct':      safe_float(row.get('k_percent') or row.get('strikeout_percent')),
            'put_away':   safe_float(row.get('put_away') or row.get('put_away_percent')),
        })
    print(f'  {len(result)} pitchers')
    return result


def fetch_pitcher_expected():
    """
    FIX: min lowered from 25 → 1. min=25 filters almost everyone early in the season.
    Also broadened player_id field name coverage.
    """
    print('\n[2/11] Pitcher expected stats (Savant CSV)...')
    urls = [
        f'https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=pitcher&year={SEASON}&position=&team=&min=1&csv=true',
        f'https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=pitcher&year={SEASON}&min=1&csv=true',
        f'https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=pitcher&year={SEASON}&csv=true',
    ]
    text = None
    for url in urls:
        text = fetch_url(url)
        rows = parse_csv(text) if text else []
        if rows:
            print(f'  Got {len(rows)} rows')
            print(f'  Columns: {list(rows[0].keys())[:12]}')
            break
        text = None

    if not text:
        return {}

    result = {}
    for row in parse_csv(text):
        pid = safe_int(row.get('player_id') or row.get('pitcher_id') or row.get('IDfg'))
        if not pid:
            continue
        result[pid] = {
            'player_name':   row.get('player_name', '') or row.get('name', '') or f"{row.get('first_name','')} {row.get('last_name','')}".strip(),
            'xba':           safe_float(row.get('xba') or row.get('est_ba')),
            'xslg':          safe_float(row.get('xslg') or row.get('est_slg')),
            'xwoba':         safe_float(row.get('xwoba') or row.get('est_woba')),
            'xera':          safe_float(row.get('xera') or row.get('est_era')),
            'barrel_pct':    safe_float(row.get('barrel_batted_rate') or row.get('barrel_pct') or row.get('brl_percent')),
            'hard_hit_pct':  safe_float(row.get('hard_hit_percent') or row.get('hard_hit_pct') or row.get('hard_hit%')),
            'exit_velo_avg': safe_float(row.get('avg_exit_velocity') or row.get('exit_velocity_avg') or row.get('launch_speed')),
            'k_pct':         safe_float(row.get('k_percent') or row.get('strikeout_percent')),
            'bb_pct':        safe_float(row.get('bb_percent') or row.get('walk_percent')),
            'woba':          safe_float(row.get('woba')),
            'pa':            safe_int(row.get('pa') or row.get('bip')),
        }
    print(f'  {len(result)} pitchers')
    return result


def fetch_pitcher_percentiles():
    print('\n[3/11] Pitcher percentiles (Savant CSV)...')
    url = f'https://baseballsavant.mlb.com/leaderboard/percentile-rankings?type=pit&year={SEASON}&position=&team=&csv=true'
    text = fetch_url(url)
    if not text:
        return {}
    SKIP = {'player_name', 'player_id', 'year', 'team_name_abbrev', 'position', 'pa', 'name'}
    result = {}
    for row in parse_csv(text):
        pid = safe_int(row.get('player_id') or row.get('pitcher_id'))
        if not pid:
            continue
        percentiles = {k: safe_float(v) for k, v in row.items()
                       if k not in SKIP and safe_float(v) is not None}
        result[pid] = {'player_name': row.get('player_name', '') or row.get('name', ''), 'percentiles': percentiles}
    print(f'  {len(result)} pitchers')
    return result


def fetch_pitcher_statcast():
    """
    Pitcher Statcast leaderboard — whiff rate, chase rate, barrel% allowed,
    hard hit% allowed, exit velo allowed, sweet spot%, K%, BB%.
    These are the most predictive pitcher performance metrics.
    Uses two endpoints and merges: custom leaderboard + spin/velo.
    """
    print('\n[3b] Pitcher Statcast metrics (Savant CSV)...')

    urls = [
        # Primary: custom statcast leaderboard (pitcher perspective)
        f'https://baseballsavant.mlb.com/leaderboard/custom?year={SEASON}&type=pitcher&filter=&sort=6&sortDir=asc&min=1&selections=player_id,player_name,team_name_abbrev,pa,k_percent,bb_percent,whiff_percent,swing_percent,o_swing_percent,z_swing_percent,z_contact_percent,oz_contact_percent,barrel_batted_rate,hard_hit_percent,avg_exit_velocity,sweet_spot_percent,xera,xba,xslg,xwoba,n_&csv=true',
        # Fallback 1: statcast leaderboard
        f'https://baseballsavant.mlb.com/statcast/leaderboard?type=pitcher&year={SEASON}&min=1&csv=true',
        # Fallback 2: expected stats (has subset of fields)
        f'https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=pitcher&year={SEASON}&min=1&csv=true',
    ]

    result = {}

    for url in urls:
        text = fetch_url(url)
        if not text:
            continue
        rows = parse_csv(text)
        if not rows:
            continue

        cols = list(rows[0].keys())
        print(f'  Got {len(rows)} rows, cols sample: {cols[:12]}')

        count = 0
        for row in rows:
            pid = safe_int(
                row.get('player_id') or row.get('pitcher_id') or row.get('IDfg')
            )
            if not pid:
                continue

            if pid not in result:
                result[pid] = {}

            result[pid].update({
                'player_name':      row.get('player_name', '') or row.get('name', '') or f"{row.get('first_name','')} {row.get('last_name','')}".strip(),
                # Strikeout / walk rates
                'k_pct':            safe_float(row.get('k_percent') or row.get('strikeout_percent') or row.get('k%')),
                'bb_pct':           safe_float(row.get('bb_percent') or row.get('walk_percent') or row.get('bb%')),
                # Swing/whiff metrics (stuff quality)
                'whiff_pct':        safe_float(row.get('whiff_percent') or row.get('whiff_pct') or row.get('whiff%') or row.get('swstr_percent')),
                'chase_pct':        safe_float(row.get('o_swing_percent') or row.get('chase_percent') or row.get('o_swing%')),
                'zone_swing_pct':   safe_float(row.get('z_swing_percent') or row.get('z_swing%')),
                'zone_contact_pct': safe_float(row.get('z_contact_percent') or row.get('z_contact%')),
                'oz_contact_pct':   safe_float(row.get('oz_contact_percent') or row.get('oz_contact%')),
                'swing_pct':        safe_float(row.get('swing_percent') or row.get('swing%')),
                # Contact quality allowed
                'barrel_pct':       safe_float(row.get('barrel_batted_rate') or row.get('barrel_pct') or row.get('brl_percent')),
                'hard_hit_pct':     safe_float(row.get('hard_hit_percent') or row.get('hard_hit_pct') or row.get('hard_hit%')),
                'exit_velo_avg':    safe_float(row.get('avg_exit_velocity') or row.get('exit_velocity_avg') or row.get('launch_speed')),
                'sweet_spot_pct':   safe_float(row.get('sweet_spot_percent') or row.get('sweet_spot%')),
                # Expected stats
                'xera':             safe_float(row.get('xera') or row.get('est_era')),
                'xba':              safe_float(row.get('xba') or row.get('est_ba')),
                'xslg':             safe_float(row.get('xslg') or row.get('est_slg')),
                'xwoba':            safe_float(row.get('xwoba') or row.get('est_woba')),
                'pa':               safe_int(row.get('pa') or row.get('bip') or row.get('n_')),
            })
            count += 1

        if count > 50:
            print(f'  Loaded {count} pitchers from {url[:70]}')
            break
        else:
            print(f'  Only {count} rows from this URL — trying next')
            result = {}  # reset and try next URL

    print(f'  Total: {len(result)} pitchers with Statcast data')
    return result


def fetch_hitter_expected():
    """
    FIX: min lowered from 25 → 1.
    """
    print('\n[4/11] Hitter expected stats (Savant CSV)...')
    urls = [
        f'https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=batter&year={SEASON}&position=&team=&min=1&csv=true',
        f'https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=batter&year={SEASON}&min=1&csv=true',
        f'https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=batter&year={SEASON}&csv=true',
    ]
    text = None
    for url in urls:
        text = fetch_url(url)
        rows = parse_csv(text) if text else []
        if rows:
            print(f'  Got {len(rows)} rows')
            print(f'  Columns: {list(rows[0].keys())[:12]}')
            break
        text = None

    if not text:
        return {}

    result = {}
    for row in parse_csv(text):
        pid = safe_int(row.get('player_id') or row.get('batter_id') or row.get('IDfg'))
        if not pid:
            continue
        result[pid] = {
            'player_name':   row.get('player_name', '') or row.get('name', '') or f"{row.get('first_name','')} {row.get('last_name','')}".strip(),
            'xba':           safe_float(row.get('xba') or row.get('est_ba')),
            'xslg':          safe_float(row.get('xslg') or row.get('est_slg')),
            'xwoba':         safe_float(row.get('xwoba') or row.get('est_woba')),
            'barrel_pct':    safe_float(row.get('barrel_batted_rate') or row.get('barrel_pct') or row.get('brl_percent')),
            'hard_hit_pct':  safe_float(row.get('hard_hit_percent') or row.get('hard_hit_pct') or row.get('hard_hit%')),
            'exit_velo_avg': safe_float(row.get('avg_exit_velocity') or row.get('exit_velocity_avg') or row.get('launch_speed')),
            'k_pct':         safe_float(row.get('k_percent') or row.get('strikeout_percent')),
            'bb_pct':        safe_float(row.get('bb_percent') or row.get('walk_percent')),
            'woba':          safe_float(row.get('woba')),
            'pa':            safe_int(row.get('pa') or row.get('bip')),
            # Extra hitter fields
            'sprint_speed':  safe_float(row.get('sprint_speed')),
            'pull_percent':  safe_float(row.get('pull_percent')),
            'center_percent':safe_float(row.get('center_percent')),
            'oppo_percent':  safe_float(row.get('oppo_percent')),
        }
    print(f'  {len(result)} hitters')
    return result


def fetch_hitter_statcast():
    """
    FIX: plate-discipline endpoint was returning 404.
    New approach:
      1. Sprint speed — same endpoint, works fine
      2. Plate discipline — replaced dead /leaderboard/plate-discipline with
         the Savant custom leaderboard which includes o_swing%, z_swing%, whiff% etc.
         Fallback: pull from statcast search CSV aggregate.
    """
    print('\n[5/11] Hitter statcast / discipline (Savant CSV)...')

    sprint_url = f'https://baseballsavant.mlb.com/leaderboard/sprint_speed?year={SEASON}&position=&team=&min=10&csv=true'

    # Plate discipline — try multiple endpoints in order
    discipline_urls = [
        # Primary: custom leaderboard with discipline columns
        (f'https://baseballsavant.mlb.com/leaderboard/custom?year={SEASON}&type=batter&filter=&trades=&pos=&team=&range=year&nb=&qual=y'
         f'&c0=o_swing_percent&c1=z_swing_percent&c2=whiff_percent&c3=z_contact_percent'
         f'&c4=f_strike_percent&c5=swing_percent&c6=contact_percent&c7=gb_percent&c8=fb_percent&c9=ld_percent'
         f'&csv=true'),
        # Fallback 1: old endpoint with lower min
        f'https://baseballsavant.mlb.com/leaderboard/plate-discipline?year={SEASON}&position=&team=&min=1&csv=true',
        # Fallback 2: statcast leaderboard
        f'https://baseballsavant.mlb.com/statcast/leaderboard?type=batter&year={SEASON}&min=1&csv=true',
    ]

    result = {}

    # Sprint speed
    text = fetch_url(sprint_url)
    if text:
        rows = parse_csv(text)
        for row in rows:
            pid = safe_int(row.get('player_id'))
            if not pid:
                continue
            if pid not in result:
                result[pid] = {}
            result[pid]['sprint_speed'] = safe_float(row.get('sprint_speed') or row.get('hp_to_1b'))
            result[pid]['player_name']  = row.get('player_name', '') or row.get('name', '')
        print(f'  sprint: {len(result)} players')
    else:
        print('  sprint: fetch failed')

    # Plate discipline
    disc_loaded = False
    for disc_url in discipline_urls:
        text = fetch_url(disc_url)
        if not text:
            continue
        rows = parse_csv(text)
        if not rows:
            continue

        cols = list(rows[0].keys())
        print(f'  discipline: {len(rows)} rows from URL, cols sample: {cols[:10]}')

        count = 0
        for row in rows:
            pid = safe_int(row.get('player_id') or row.get('batter_id') or row.get('IDfg'))
            if not pid:
                continue
            if pid not in result:
                result[pid] = {}
            result[pid].update({
                # Handle both old and new column names
                'chase_pct':          safe_float(row.get('o_swing_percent') or row.get('chase_percent') or row.get('o_swing%')),
                'whiff_pct':          safe_float(row.get('whiff_percent') or row.get('swstr_percent') or row.get('whiff%')),
                'zone_swing':         safe_float(row.get('z_swing_percent') or row.get('z_swing%')),
                'zone_contact':       safe_float(row.get('z_contact_percent') or row.get('z_contact%')),
                'first_pitch_swing':  safe_float(row.get('f_strike_percent') or row.get('first_pitch_strike%')),
                'swing_pct':          safe_float(row.get('swing_percent') or row.get('swing%')),
                'contact_pct':        safe_float(row.get('contact_percent') or row.get('contact%')),
                'gb_pct':             safe_float(row.get('gb_percent') or row.get('gb%')),
                'fb_pct':             safe_float(row.get('fb_percent') or row.get('fb%')),
                'ld_pct':             safe_float(row.get('ld_percent') or row.get('ld%')),
                'pull_percent':       safe_float(row.get('pull_percent') or row.get('pull%')),
                'center_percent':     safe_float(row.get('center_percent') or row.get('cent%')),
                'oppo_percent':       safe_float(row.get('oppo_percent') or row.get('oppo%')),
            })
            count += 1

        if count > 10:
            print(f'  discipline: {count} players loaded')
            disc_loaded = True
            break
        else:
            print(f'  discipline: only {count} rows parsed — trying next URL')

    if not disc_loaded:
        print('  discipline: all endpoints failed — sprint speed only')

    print(f'  total: {len(result)} players with any statcast data')
    return result


def fetch_hitter_percentiles():
    print('\n[6/11] Hitter percentiles (Savant CSV)...')
    url = f'https://baseballsavant.mlb.com/leaderboard/percentile-rankings?type=bat&year={SEASON}&position=&team=&csv=true'
    text = fetch_url(url)
    if not text:
        return {}
    SKIP = {'player_name', 'player_id', 'year', 'team_name_abbrev', 'position', 'pa', 'name'}
    result = {}
    for row in parse_csv(text):
        pid = safe_int(row.get('player_id') or row.get('batter_id'))
        if not pid:
            continue
        percentiles = {k: safe_float(v) for k, v in row.items()
                       if k not in SKIP and safe_float(v) is not None}
        result[pid] = {'player_name': row.get('player_name', '') or row.get('name', ''), 'percentiles': percentiles}
    print(f'  {len(result)} hitters')
    return result


# ══════════════════════════════════════════════════
# MLB STATS API — BULK SB MODEL DATA
# ══════════════════════════════════════════════════

def fetch_pop_time():
    print('\n[7/11] Catcher SB defense — BULK (one API call)...')
    data = mlb_get(f'stats?stats=season&group=catching&season={SEASON}&sportId=1&limit=150')
    if not data:
        print('  Bulk catching stats failed')
        return {}

    result = {}
    for split in data.get('stats', [{}])[0].get('splits', []):
        pid  = split.get('player', {}).get('id')
        name = split.get('player', {}).get('fullName', '')
        st   = split.get('stat', {})
        if not pid:
            continue

        sb_allowed = safe_int(st.get('stolenBases'))
        cs         = safe_int(st.get('caughtStealing'))
        att        = (sb_allowed or 0) + (cs or 0)
        if att < 3:
            continue

        cs_pct = round(cs / att * 100, 1) if att >= 3 else None

        if cs_pct is not None:
            if cs_pct >= 35:   pop_proxy, arm_tier = 2.00, 'strong'
            elif cs_pct >= 28: pop_proxy, arm_tier = 2.05, 'above_avg'
            elif cs_pct >= 22: pop_proxy, arm_tier = 2.10, 'avg'
            elif cs_pct >= 15: pop_proxy, arm_tier = 2.15, 'below_avg'
            else:              pop_proxy, arm_tier = 2.22, 'weak'
        else:
            pop_proxy, arm_tier = None, None

        result[pid] = {
            'player_name': name,
            'sb_allowed':  sb_allowed,
            'cs':          cs,
            'cs_pct':      cs_pct,
            'att':         att,
            'pop_2b':      pop_proxy,
            'arm_tier':    arm_tier,
        }

    print(f'  {len(result)} catchers with SB defense data')
    return result


def fetch_pitch_tempo():
    print('\n[8/11] Pitcher SB metrics — BULK...')
    season_data = mlb_get(
        f'stats?stats=season&group=pitching&season={SEASON}&sportId=1&limit=1000'
    )
    if not season_data:
        print('  Bulk pitching stats call failed')
        return {}

    stats_arr  = season_data.get('stats', [])
    all_splits = []
    for s in stats_arr:
        splits = s.get('splits', [])
        if splits:
            all_splits.extend(splits)

    print(f'  Total splits: {len(all_splits)}')

    result = {}
    for split in all_splits:
        pid = split.get('player', {}).get('id')
        if not pid:
            continue
        st = split.get('stat', {})

        ip_raw = safe_float(st.get('inningsPitched'))
        if not ip_raw or ip_raw < 5:
            continue

        sb_allowed = (safe_int(st.get('stolenBases')) or
                      safe_int(st.get('stolenBasesAllowed')) or 0)
        cs = safe_int(st.get('caughtStealing')) or 0

        ip_whole = int(ip_raw)
        ip_frac  = round((ip_raw - ip_whole) * 10)
        ip_dec   = ip_whole + (ip_frac / 3)

        sb_per9 = round((sb_allowed / ip_dec) * 9, 2) if ip_dec > 0 else 0.0

        if sb_per9 <= 0.3:   time_to_plate = 1.22
        elif sb_per9 <= 0.6: time_to_plate = 1.30
        elif sb_per9 <= 1.0: time_to_plate = 1.37
        elif sb_per9 <= 1.5: time_to_plate = 1.44
        else:                time_to_plate = 1.52

        result[pid] = {
            'sb_allowed':     sb_allowed,
            'cs':             cs,
            'sb_per9':        sb_per9,
            'ip':             ip_raw,
            'ro_era':         safe_float(st.get('era')),
            'ro_obp':         safe_float(st.get('obp')),
            'ro_whip':        safe_float(st.get('whip')),
            'pace_runner_on': time_to_plate,
            'time_to_plate':  time_to_plate,
        }

    print(f'  {len(result)} pitchers with SB metrics')
    return result


def fetch_baserunning():
    print('\n[9/11] Baserunning stats — BULK (one API call)...')
    data = mlb_get(
        f'stats?stats=season&group=hitting&season={SEASON}&sportId=1&limit=1000'
    )
    if not data:
        print('  Bulk hitting stats failed')
        return {}

    result = {}
    for split in data.get('stats', [{}])[0].get('splits', []):
        pid = split.get('player', {}).get('id')
        if not pid:
            continue
        st  = split.get('stat', {})
        sb  = safe_int(st.get('stolenBases', 0)) or 0
        cs  = safe_int(st.get('caughtStealing', 0)) or 0
        att = sb + cs
        pa  = safe_int(st.get('plateAppearances', 0)) or 0

        if att < 2:
            continue

        sb_pct       = round(sb / att, 3) if att >= 3 else None
        attempt_rate = round(att / pa, 3) if pa >= 50 else None

        if attempt_rate is not None:
            if attempt_rate >= 0.12:   xbt_proxy = 0.58
            elif attempt_rate >= 0.08: xbt_proxy = 0.50
            elif attempt_rate >= 0.05: xbt_proxy = 0.43
            elif attempt_rate >= 0.02: xbt_proxy = 0.36
            else:                      xbt_proxy = 0.28
        else:
            xbt_proxy = None

        result[pid] = {
            'player_name':  split.get('player', {}).get('fullName', ''),
            'sb':           sb,
            'cs':           cs,
            'att':          att,
            'sb_pct':       sb_pct,
            'attempt_rate': attempt_rate,
            'pa':           pa,
            'xbt_pct':      xbt_proxy,
            'br_runs':      round((sb * 0.2 - cs * 0.4), 1) if att >= 3 else None,
        }

    print(f'  {len(result)} runners with baserunning data')
    return result


def fetch_sb_leaders():
    print('\n[10/11] SB leaders — BULK (one API call)...')
    data = mlb_get(
        f'stats/leaders?leaderCategories=stolenBases&season={SEASON}&sportId=1&limit=150&hydrate=person'
    )
    if not data:
        return {}

    leaders = data.get('leagueLeaders', [{}])[0].get('leaders', [])
    result  = {}
    for leader in leaders:
        pid   = leader.get('person', {}).get('id')
        name  = leader.get('person', {}).get('fullName', '')
        team  = leader.get('team', {}).get('name', '')
        sb    = safe_int(leader.get('value', 0)) or 0
        if not pid or sb < 2:
            continue

        if sb >= 25:   sprint = 29.8
        elif sb >= 20: sprint = 29.2
        elif sb >= 15: sprint = 28.7
        elif sb >= 10: sprint = 28.1
        elif sb >= 5:  sprint = 27.4
        else:          sprint = 26.5

        result[pid] = {
            'player_name':  name,
            'team':         team,
            'sb':           sb,
            'sprint_speed': sprint,
        }

    print(f'  {len(result)} SB leaders')
    return result


def fetch_pitcher_sb_metrics():
    """Alias of pitch-tempo — already written in step 8."""
    print('\n[11/11] Pitcher SB alias...')
    path = os.path.join(DATA_DIR, 'savant-pitch-tempo.json')
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        print(f'  Aliased {len(data)} pitchers')
        return data
    return {}


# ══════════════════════════════════════════════════
# META + MAIN
# ══════════════════════════════════════════════════

def write_meta(files_updated, errors):
    meta = {
        'updated':       datetime.now(timezone.utc).isoformat(),
        'season':        SEASON,
        'files_updated': files_updated,
        'partial_failures': errors,
        'data_sources':  {
            'pitcher_analytics': 'Baseball Savant CSV',
            'hitter_analytics':  'Baseball Savant CSV',
            'sb_model':          'MLB Stats API bulk endpoints',
        },
    }
    write_json('savant-meta.json', meta)


def main():
    print(f'=== The Lens Data Fetch — {SEASON} season ===')
    print(f'Script location: {os.path.abspath(__file__)}')
    print(f'Output: {os.path.abspath(DATA_DIR)}')
    print(f'Data dir exists: {os.path.exists(DATA_DIR)}')

    files_updated = []
    errors        = []

    tasks = [
        ('arsenal',            fetch_pitcher_arsenal,    'savant-arsenal.json'),
        ('pitcher-expected',   fetch_pitcher_expected,   'savant-expected.json'),
        ('pitcher-pct',        fetch_pitcher_percentiles,'savant-percentiles.json'),
        ('pitcher-statcast',   fetch_pitcher_statcast,   'savant-pitcher-statcast.json'),
        ('hitter-expected',    fetch_hitter_expected,    'savant-hitter-expected.json'),
        ('hitter-statcast',    fetch_hitter_statcast,    'savant-hitter-statcast.json'),
        ('hitter-pct',         fetch_hitter_percentiles, 'savant-hitter-percentiles.json'),
        ('pop-time',           fetch_pop_time,           'savant-pop-time.json'),
        ('pitch-tempo',        fetch_pitch_tempo,        'savant-pitch-tempo.json'),
        ('baserunning',        fetch_baserunning,        'savant-baserunning.json'),
        ('sb-leaders',         fetch_sb_leaders,         'savant-sb-leaders.json'),
        ('pitcher-sb',         fetch_pitcher_sb_metrics, 'savant-pitcher-sb.json'),
    ]

    for key, fn, filename in tasks:
        try:
            data = fn()
            if data:
                write_json(filename, data)
                files_updated.append(filename)
            else:
                print(f'  {key}: no data returned')
                errors.append(key)
        except Exception as e:
            print(f'  ERROR [{key}]: {e}')
            errors.append(key)

    write_meta(files_updated, errors)
    files_updated.append('savant-meta.json')

    # Grade yesterday's picks
    try:
        grade_picks()
    except Exception as e:
        print(f'  Grade picks error: {e}')

    print(f'\n=== Done — {len(files_updated)} files updated ===')
    for f in files_updated:
        print(f'  ✓ {f}')
    if errors:
        print(f'\nPartial failures: {", ".join(errors)}')
        # Only exit non-zero if core data completely failed
        core = [e for e in errors if e in ('pitcher-expected', 'hitter-expected')]
        if len(core) == 2:  # both failed = real problem
            sys.exit(1)


def grade_picks():
    """
    Grade yesterday's top picks against actual results.
    - Pitcher hit = actual Ks >= kLow (model floor)
    - Hitter hit  = batter hit a HR
    """
    print('\nGrading yesterday\'s picks...')
    history_path = os.path.join(DATA_DIR, 'pick-history.json')
    if not os.path.exists(history_path):
        print('  No pick history file found')
        return

    with open(history_path) as f:
        history = json.load(f)

    picks = history.get('picks', [])
    import datetime as dt
    yesterday = (dt.datetime.now() - dt.timedelta(days=1)).strftime('%Y-%m-%d')

    ungraded = [p for p in picks if p.get('date') == yesterday and p.get('result') is None]
    if not ungraded:
        print(f'  No ungraded picks for {yesterday}')
        return

    print(f'  Grading {len(ungraded)} picks from {yesterday}')

    for pick in ungraded:
        try:
            pid       = pick.get('playerId')
            pick_type = pick.get('type')
            game_pk   = pick.get('gamePk')

            if not pid or not game_pk:
                continue

            data = mlb_get(f'game/{game_pk}/boxscore')
            if not data:
                continue

            if pick_type == 'pitcher':
                k_floor = pick.get('kFloor', 4)
                for team_side in ['away', 'home']:
                    player_info = data.get('teams', {}).get(team_side, {}).get('players', {})
                    for _, pdata in player_info.items():
                        if pdata.get('person', {}).get('id') == pid:
                            actual_ks = pdata.get('stats', {}).get('pitching', {}).get('strikeOuts', 0)
                            pick['actualKs'] = actual_ks
                            pick['result']   = 'hit' if actual_ks >= k_floor else 'miss'
                            print(f'  {pick.get("playerName")}: {actual_ks} Ks vs floor {k_floor} → {pick["result"]}')
                            break

            elif pick_type == 'hitter':
                for team_side in ['away', 'home']:
                    player_info = data.get('teams', {}).get(team_side, {}).get('players', {})
                    for _, pdata in player_info.items():
                        if pdata.get('person', {}).get('id') == pid:
                            actual_hr = pdata.get('stats', {}).get('batting', {}).get('homeRuns', 0)
                            pick['actualHR'] = actual_hr
                            pick['result']   = 'hit' if actual_hr >= 1 else 'miss'
                            print(f'  {pick.get("playerName")}: {actual_hr} HR → {pick["result"]}')
                            break

        except Exception as e:
            print(f'  Error grading pick {pick.get("playerName")}: {e}')
        time.sleep(0.1)

    # Recalculate aggregate record from all graded picks
    record = {'pitcher': {'hits': 0, 'total': 0}, 'hitter': {'hits': 0, 'total': 0}}
    for pick in picks:
        if pick.get('result') in ('hit', 'miss'):
            t = pick.get('type', 'hitter')
            if t in record:
                record[t]['total'] += 1
                if pick['result'] == 'hit':
                    record[t]['hits'] += 1

    history['picks']       = picks
    history['lastGraded']  = yesterday
    history['modelRecord'] = record

    with open(history_path, 'w') as f:
        json.dump(history, f, separators=(',', ':'), indent=2)

    print(f'  Pitcher record: {record["pitcher"]["hits"]}/{record["pitcher"]["total"]}')
    print(f'  Hitter record:  {record["hitter"]["hits"]}/{record["hitter"]["total"]}')


if __name__ == '__main__':
    main()
