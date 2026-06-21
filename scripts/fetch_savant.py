#!/usr/bin/env python3
"""
fetch_savant.py — Baseball Savant data fetcher for The Lens
Runs daily via GitHub Actions, writes JSON files to data/
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

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
os.makedirs(DATA_DIR, exist_ok=True)

SEASON = datetime.now().year
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; TheLens/1.0)',
    'Accept': 'text/csv,application/json,*/*',
}

def fetch_url(url, retries=3, delay=4):
    """Fetch a URL with retries. Returns response text or None."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            print(f'  HTTP {e.code} on attempt {attempt+1}: {url[:80]}')
        except Exception as e:
            print(f'  Error on attempt {attempt+1}: {e}')
        if attempt < retries - 1:
            time.sleep(delay)
    return None

def parse_csv(text):
    """Parse CSV text into list of dicts."""
    if not text or not text.strip():
        return []
    reader = csv.DictReader(StringIO(text))
    return list(reader)

def safe_float(val, default=None):
    try:
        return float(val) if val not in (None, '', 'null') else default
    except (ValueError, TypeError):
        return default

def safe_int(val, default=None):
    try:
        return int(val) if val not in (None, '', 'null') else default
    except (ValueError, TypeError):
        return default

def write_json(filename, data):
    path = os.path.join(DATA_DIR, filename)
    with open(path, 'w') as f:
        json.dump(data, f, separators=(',', ':'))
    size = os.path.getsize(path)
    print(f'  Wrote {filename} ({size/1024:.1f} KB, {len(data) if isinstance(data, dict) else "?"} entries)')

# ── 1. PITCHER ARSENAL (pitch mix, velo, usage) ──
def fetch_pitcher_arsenal():
    print('\n[1/7] Pitcher arsenal...')
    url = (
        f'https://baseballsavant.mlb.com/statcast_search/csv'
        f'?all=true&hfPT=&hfAB=&hfGT=R%7C&hfPR=&hfZ=&hfStadium=&hfBBL=&hfNewZones=&hfPull='
        f'&hfC=&hfSea={SEASON}%7C&hfSit=&player_type=pitcher&hfOuts=&hfOpponent='
        f'&pitcher_throws=&batter_stands=&hfSA=&game_date_gt=&game_date_lt='
        f'&hfMo=&hfTeam=&home_road=&hfRO=&position=&hfInfield=&hfOutfield='
        f'&hfInn=&hfBBT=&hfFlag=&metric_1=&group_by=name&min_pitches=100'
        f'&min_results=0&min_pas=0&sort_col=pitches&player_event_sort=api_p_release_speed'
        f'&sort_order=desc&chk_stats_pa=on&chk_stats_abs=on&chk_stats_bip=on'
        f'&type=details&player_type=pitcher'
    )
    # Use the statcast pitch arsenal endpoint instead (more reliable)
    url2 = f'https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats?type=pitcher&pitchType=&year={SEASON}&team=&min=25&csv=true'
    text = fetch_url(url2)
    if not text:
        print('  Arsenal fetch failed')
        return {}

    rows = parse_csv(text)
    result = {}
    for row in rows:
        pid = safe_int(row.get('pitcher_id') or row.get('player_id'))
        if not pid:
            continue
        if pid not in result:
            result[pid] = {'pitches': []}
        result[pid]['pitches'].append({
            'pitch_type': row.get('pitch_type', ''),
            'pitch_name': row.get('pitch_name', ''),
            'run_value':  safe_float(row.get('run_value_per100') or row.get('run_value')),
            'pa':         safe_int(row.get('pa')),
            'usage_pct':  safe_float(row.get('pitch_usage') or row.get('pitch_percent')),
            'avg_speed':  safe_float(row.get('avg_speed') or row.get('release_speed')),
            'avg_spin':   safe_float(row.get('avg_spin')),
            'whiff_pct':  safe_float(row.get('whiff_percent')),
            'k_pct':      safe_float(row.get('k_percent')),
            'put_away':   safe_float(row.get('put_away')),
        })

    print(f'  {len(result)} pitchers with arsenal data')
    return result

# ── 2. PITCHER EXPECTED STATS (xERA, xFIP, xwOBA) ──
def fetch_pitcher_expected():
    print('\n[2/7] Pitcher expected stats...')
    url = f'https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=pitcher&year={SEASON}&position=&team=&min=25&csv=true'
    text = fetch_url(url)
    if not text:
        print('  Expected stats fetch failed')
        return {}

    rows = parse_csv(text)
    result = {}
    for row in rows:
        pid = safe_int(row.get('player_id'))
        if not pid:
            continue
        result[pid] = {
            'player_name':  row.get('player_name', ''),
            'xba':          safe_float(row.get('xba')),
            'xslg':         safe_float(row.get('xslg')),
            'xwoba':        safe_float(row.get('xwoba')),
            'xera':         safe_float(row.get('xera')),
            'xfip':         safe_float(row.get('xfip') or row.get('xfip-')),
            'barrel_pct':   safe_float(row.get('barrel_batted_rate') or row.get('barrel_pct')),
            'hard_hit_pct': safe_float(row.get('hard_hit_percent') or row.get('hard_hit_pct')),
            'exit_velo_avg':safe_float(row.get('avg_exit_velocity') or row.get('exit_velocity_avg')),
            'k_pct':        safe_float(row.get('k_percent')),
            'bb_pct':       safe_float(row.get('bb_percent')),
            'woba':         safe_float(row.get('woba')),
            'pa':           safe_int(row.get('pa')),
        }

    print(f'  {len(result)} pitchers with expected stats')
    return result

# ── 3. PITCHER PERCENTILES ──
def fetch_pitcher_percentiles():
    print('\n[3/7] Pitcher percentiles...')
    url = f'https://baseballsavant.mlb.com/leaderboard/percentile-rankings?type=pit&year={SEASON}&position=&team=&csv=true'
    text = fetch_url(url)
    if not text:
        print('  Percentiles fetch failed')
        return {}

    rows = parse_csv(text)
    result = {}
    SKIP = {'player_name','player_id','year','team_name_abbrev','position','pa'}
    for row in rows:
        pid = safe_int(row.get('player_id'))
        if not pid:
            continue
        percentiles = {}
        for k, v in row.items():
            if k in SKIP:
                continue
            fv = safe_float(v)
            if fv is not None:
                percentiles[k] = fv
        result[pid] = {
            'player_name': row.get('player_name', ''),
            'percentiles': percentiles,
        }

    print(f'  {len(result)} pitchers with percentiles')
    return result

# ── 4. HITTER EXPECTED STATS (xBA, xSLG, xwOBA, barrel%, HH%, EV, sprint speed) ──
def fetch_hitter_expected():
    print('\n[4/7] Hitter expected stats...')
    url = f'https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=batter&year={SEASON}&position=&team=&min=25&csv=true'
    text = fetch_url(url)
    if not text:
        print('  Hitter expected stats fetch failed')
        return {}

    rows = parse_csv(text)
    result = {}
    for row in rows:
        pid = safe_int(row.get('player_id'))
        if not pid:
            continue
        result[pid] = {
            'player_name':   row.get('player_name', ''),
            'xba':           safe_float(row.get('xba')),
            'xslg':          safe_float(row.get('xslg')),
            'xwoba':         safe_float(row.get('xwoba')),
            'barrel_pct':    safe_float(row.get('barrel_batted_rate') or row.get('barrel_pct')),
            'hard_hit_pct':  safe_float(row.get('hard_hit_percent') or row.get('hard_hit_pct')),
            'exit_velo_avg': safe_float(row.get('avg_exit_velocity') or row.get('exit_velocity_avg')),
            'k_pct':         safe_float(row.get('k_percent')),
            'bb_pct':        safe_float(row.get('bb_percent')),
            'woba':          safe_float(row.get('woba')),
            'pa':            safe_int(row.get('pa')),
        }

    print(f'  {len(result)} hitters with expected stats')
    return result

# ── 5. HITTER STATCAST / DISCIPLINE (whiff%, chase%, sprint speed) ──
def fetch_hitter_statcast():
    print('\n[5/7] Hitter statcast / discipline...')
    # Sprint speed
    sprint_url = f'https://baseballsavant.mlb.com/leaderboard/sprint_speed?year={SEASON}&position=&team=&min=10&csv=true'
    # Plate discipline
    discipline_url = f'https://baseballsavant.mlb.com/leaderboard/plate-discipline?year={SEASON}&position=&team=&min=25&csv=true'

    sprint_text     = fetch_url(sprint_url)
    discipline_text = fetch_url(discipline_url)

    result = {}

    # Sprint speed
    if sprint_text:
        rows = parse_csv(sprint_text)
        for row in rows:
            pid = safe_int(row.get('player_id'))
            if not pid:
                continue
            if pid not in result:
                result[pid] = {}
            result[pid]['sprint_speed'] = safe_float(row.get('sprint_speed') or row.get('hp_to_1b'))
            result[pid]['player_name'] = row.get('player_name') or result[pid].get('player_name','')
        print(f'  Sprint speed: {len(result)} players')

    # Discipline
    if discipline_text:
        rows = parse_csv(discipline_text)
        for row in rows:
            pid = safe_int(row.get('player_id'))
            if not pid:
                continue
            if pid not in result:
                result[pid] = {}
            result[pid].update({
                'chase_pct':         safe_float(row.get('o_swing_percent') or row.get('chase_percent')),
                'whiff_pct':         safe_float(row.get('whiff_percent') or row.get('swstr_percent')),
                'zone_swing':        safe_float(row.get('z_swing_percent')),
                'zone_contact':      safe_float(row.get('z_contact_percent')),
                'first_pitch_swing': safe_float(row.get('f_strike_percent') or row.get('first_pitch_swing_percent')),
                'swing_pct':         safe_float(row.get('swing_percent')),
                'contact_pct':       safe_float(row.get('contact_percent')),
                'gb_pct':            safe_float(row.get('gb_percent')),
                'fb_pct':            safe_float(row.get('fb_percent')),
                'ld_pct':            safe_float(row.get('ld_percent')),
            })
        print(f'  Discipline: {len(result)} players total')

    return result

# ── 6. HITTER PERCENTILES ──
def fetch_hitter_percentiles():
    print('\n[6/7] Hitter percentiles...')
    url = f'https://baseballsavant.mlb.com/leaderboard/percentile-rankings?type=bat&year={SEASON}&position=&team=&csv=true'
    text = fetch_url(url)
    if not text:
        print('  Hitter percentiles fetch failed')
        return {}

    rows = parse_csv(text)
    result = {}
    SKIP = {'player_name','player_id','year','team_name_abbrev','position','pa'}
    for row in rows:
        pid = safe_int(row.get('player_id'))
        if not pid:
            continue
        percentiles = {}
        for k, v in row.items():
            if k in SKIP:
                continue
            fv = safe_float(v)
            if fv is not None:
                percentiles[k] = fv
        result[pid] = {
            'player_name': row.get('player_name', ''),
            'percentiles': percentiles,
        }

    print(f'  {len(result)} hitters with percentiles')
    return result

# ── 7. META (timestamp) ──
def write_meta(files_updated):
    meta = {
        'updated':       datetime.now(timezone.utc).isoformat(),
        'season':        SEASON,
        'files_updated': files_updated,
    }
    write_json('savant-meta.json', meta)

# ── MAIN ──
def main():
    print(f'=== Baseball Savant Fetch — {SEASON} season ===')
    print(f'Output directory: {os.path.abspath(DATA_DIR)}')

    files_updated = []
    errors = []

    # Pitcher data
    try:
        arsenal = fetch_pitcher_arsenal()
        if arsenal:
            write_json('savant-arsenal.json', arsenal)
            files_updated.append('savant-arsenal.json')
    except Exception as e:
        print(f'  ERROR: {e}')
        errors.append('arsenal')

    try:
        expected = fetch_pitcher_expected()
        if expected:
            write_json('savant-expected.json', expected)
            files_updated.append('savant-expected.json')
    except Exception as e:
        print(f'  ERROR: {e}')
        errors.append('pitcher-expected')

    try:
        percentiles = fetch_pitcher_percentiles()
        if percentiles:
            write_json('savant-percentiles.json', percentiles)
            files_updated.append('savant-percentiles.json')
    except Exception as e:
        print(f'  ERROR: {e}')
        errors.append('pitcher-percentiles')

    # Hitter data
    try:
        hitter_exp = fetch_hitter_expected()
        if hitter_exp:
            write_json('savant-hitter-expected.json', hitter_exp)
            files_updated.append('savant-hitter-expected.json')
    except Exception as e:
        print(f'  ERROR: {e}')
        errors.append('hitter-expected')

    try:
        hitter_sc = fetch_hitter_statcast()
        if hitter_sc:
            write_json('savant-hitter-statcast.json', hitter_sc)
            files_updated.append('savant-hitter-statcast.json')
    except Exception as e:
        print(f'  ERROR: {e}')
        errors.append('hitter-statcast')

    try:
        hitter_pct = fetch_hitter_percentiles()
        if hitter_pct:
            write_json('savant-hitter-percentiles.json', hitter_pct)
            files_updated.append('savant-hitter-percentiles.json')
    except Exception as e:
        print(f'  ERROR: {e}')
        errors.append('hitter-percentiles')

    # Meta
    write_meta(files_updated)
    files_updated.append('savant-meta.json')

    print(f'\n=== Done ===')
    print(f'Updated: {", ".join(files_updated)}')
    if errors:
        print(f'Errors:  {", ".join(errors)}')
        sys.exit(1)
    else:
        print('All files updated successfully.')

if __name__ == '__main__':
    main()
