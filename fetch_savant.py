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
MLB_BASE = 'https://statsapi.mlb.com/api/v1'

SAVANT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'text/csv,application/json,*/*',
    'Referer': 'https://baseballsavant.mlb.com/',
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
            print(f'  HTTP {e.code} attempt {attempt+1}: {url[:80]}')
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
    return list(csv.DictReader(StringIO(text)))


def safe_float(val, default=None):
    try:
        return float(val) if val not in (None, '', 'null') else default
    except (ValueError, TypeError):
        return default


def safe_int(val, default=None):
    try:
        return int(float(val)) if val not in (None, '', 'null') else default
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
# SAVANT CSV FETCHERS  (unchanged — work fine)
# ══════════════════════════════════════════════════

def fetch_pitcher_arsenal():
    print('\n[1/11] Pitcher arsenal (Savant CSV)...')
    url = f'https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats?type=pitcher&pitchType=&year={SEASON}&team=&min=25&csv=true'
    text = fetch_url(url)
    if not text:
        return {}
    result = {}
    for row in parse_csv(text):
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
    print(f'  {len(result)} pitchers')
    return result


def fetch_pitcher_expected():
    print('\n[2/11] Pitcher expected stats (Savant CSV)...')
    url = f'https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=pitcher&year={SEASON}&position=&team=&min=25&csv=true'
    text = fetch_url(url)
    if not text:
        return {}
    result = {}
    for row in parse_csv(text):
        pid = safe_int(row.get('player_id'))
        if not pid:
            continue
        result[pid] = {
            'player_name':   row.get('player_name', ''),
            'xba':           safe_float(row.get('xba')),
            'xslg':          safe_float(row.get('xslg')),
            'xwoba':         safe_float(row.get('xwoba')),
            'xera':          safe_float(row.get('xera')),
            'barrel_pct':    safe_float(row.get('barrel_batted_rate') or row.get('barrel_pct')),
            'hard_hit_pct':  safe_float(row.get('hard_hit_percent') or row.get('hard_hit_pct')),
            'exit_velo_avg': safe_float(row.get('avg_exit_velocity') or row.get('exit_velocity_avg')),
            'k_pct':         safe_float(row.get('k_percent')),
            'bb_pct':        safe_float(row.get('bb_percent')),
            'woba':          safe_float(row.get('woba')),
            'pa':            safe_int(row.get('pa')),
        }
    print(f'  {len(result)} pitchers')
    return result


def fetch_pitcher_percentiles():
    print('\n[3/11] Pitcher percentiles (Savant CSV)...')
    url = f'https://baseballsavant.mlb.com/leaderboard/percentile-rankings?type=pit&year={SEASON}&position=&team=&csv=true'
    text = fetch_url(url)
    if not text:
        return {}
    SKIP = {'player_name', 'player_id', 'year', 'team_name_abbrev', 'position', 'pa'}
    result = {}
    for row in parse_csv(text):
        pid = safe_int(row.get('player_id'))
        if not pid:
            continue
        percentiles = {k: safe_float(v) for k, v in row.items()
                       if k not in SKIP and safe_float(v) is not None}
        result[pid] = {'player_name': row.get('player_name', ''), 'percentiles': percentiles}
    print(f'  {len(result)} pitchers')
    return result


def fetch_hitter_expected():
    print('\n[4/11] Hitter expected stats (Savant CSV)...')
    url = f'https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=batter&year={SEASON}&position=&team=&min=25&csv=true'
    text = fetch_url(url)
    if not text:
        return {}
    result = {}
    for row in parse_csv(text):
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
    print(f'  {len(result)} hitters')
    return result


def fetch_hitter_statcast():
    print('\n[5/11] Hitter statcast / discipline (Savant CSV)...')
    sprint_url     = f'https://baseballsavant.mlb.com/leaderboard/sprint_speed?year={SEASON}&position=&team=&min=10&csv=true'
    discipline_url = f'https://baseballsavant.mlb.com/leaderboard/plate-discipline?year={SEASON}&position=&team=&min=25&csv=true'
    result = {}
    for url, label in [(sprint_url, 'sprint'), (discipline_url, 'discipline')]:
        text = fetch_url(url)
        if not text:
            continue
        for row in parse_csv(text):
            pid = safe_int(row.get('player_id'))
            if not pid:
                continue
            if pid not in result:
                result[pid] = {}
            if label == 'sprint':
                result[pid]['sprint_speed'] = safe_float(row.get('sprint_speed') or row.get('hp_to_1b'))
                result[pid]['player_name']  = row.get('player_name', '')
            else:
                result[pid].update({
                    'chase_pct':         safe_float(row.get('o_swing_percent') or row.get('chase_percent')),
                    'whiff_pct':         safe_float(row.get('whiff_percent') or row.get('swstr_percent')),
                    'zone_swing':        safe_float(row.get('z_swing_percent')),
                    'zone_contact':      safe_float(row.get('z_contact_percent')),
                    'first_pitch_swing': safe_float(row.get('f_strike_percent')),
                    'swing_pct':         safe_float(row.get('swing_percent')),
                    'contact_pct':       safe_float(row.get('contact_percent')),
                    'gb_pct':            safe_float(row.get('gb_percent')),
                    'fb_pct':            safe_float(row.get('fb_percent')),
                    'ld_pct':            safe_float(row.get('ld_percent')),
                })
        print(f'  {label}: {len(result)} players')
    return result


def fetch_hitter_percentiles():
    print('\n[6/11] Hitter percentiles (Savant CSV)...')
    url = f'https://baseballsavant.mlb.com/leaderboard/percentile-rankings?type=bat&year={SEASON}&position=&team=&csv=true'
    text = fetch_url(url)
    if not text:
        return {}
    SKIP = {'player_name', 'player_id', 'year', 'team_name_abbrev', 'position', 'pa'}
    result = {}
    for row in parse_csv(text):
        pid = safe_int(row.get('player_id'))
        if not pid:
            continue
        percentiles = {k: safe_float(v) for k, v in row.items()
                       if k not in SKIP and safe_float(v) is not None}
        result[pid] = {'player_name': row.get('player_name', ''), 'percentiles': percentiles}
    print(f'  {len(result)} hitters')
    return result


# ══════════════════════════════════════════════════
# MLB STATS API — BULK SB MODEL DATA
# One request per stat type — no per-player loops
# ══════════════════════════════════════════════════

def fetch_pop_time():
    """
    ALL catchers CS% in a single bulk API call.
    /stats?stats=season&group=catching&season=YYYY&sportId=1&limit=100
    """
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

        # Derive pop-time proxy from CS%
        if cs_pct is not None:
            if cs_pct >= 35:
                pop_proxy, arm_tier = 2.00, 'strong'
            elif cs_pct >= 28:
                pop_proxy, arm_tier = 2.05, 'above_avg'
            elif cs_pct >= 22:
                pop_proxy, arm_tier = 2.10, 'avg'
            elif cs_pct >= 15:
                pop_proxy, arm_tier = 2.15, 'below_avg'
            else:
                pop_proxy, arm_tier = 2.22, 'weak'
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
    """
    ALL pitchers SB stats in ONE bulk API call.
    Uses /stats?stats=season&group=pitching to get SB allowed + IP for all pitchers.
    """
    print('\n[8/11] Pitcher SB metrics — BULK...')

    season_data = mlb_get(
        f'stats?stats=season&group=pitching&season={SEASON}&sportId=1&limit=1000'
    )

    if not season_data:
        print('  Bulk pitching stats call failed')
        return {}

    # Debug: show structure
    stats_arr = season_data.get('stats', [])
    print(f'  stats array has {len(stats_arr)} elements')
    all_splits = []
    for s in stats_arr:
        splits = s.get('splits', [])
        if splits:
            print(f'  type={s.get("type",{}).get("displayName")} splits={len(splits)}')
            # Show first split field names
            sample = splits[0].get('stat', {})
            sb_fields = [k for k in sample.keys() if 'stolen' in k.lower() or 'sb' in k.lower()]
            print(f'  SB-related fields: {sb_fields}')
            all_splits.extend(splits)

    print(f'  Total splits: {len(all_splits)}')

    result = {}
    for split in all_splits:
        pid  = split.get('player', {}).get('id')
        if not pid:
            continue
        st = split.get('stat', {})

        ip_raw = safe_float(st.get('inningsPitched'))
        if not ip_raw or ip_raw < 5:
            continue

        # Try multiple possible field names for SB allowed by pitcher
        sb_allowed = (
            safe_int(st.get('stolenBases')) or
            safe_int(st.get('stolenBasesAllowed')) or
            safe_int(st.get('sb')) or
            0
        )
        cs = safe_int(st.get('caughtStealing')) or 0

        # Convert MLB IP (5.1 = 5⅓) to decimal innings
        ip_whole = int(ip_raw)
        ip_frac  = round((ip_raw - ip_whole) * 10)
        ip_dec   = ip_whole + (ip_frac / 3)

        sb_per9 = round((sb_allowed / ip_dec) * 9, 2) if ip_dec > 0 else 0.0

        # Map SB/9 to a pace proxy (seconds to plate)
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
    """
    ALL hitters SB stats in ONE bulk API call.
    /stats?stats=season&group=hitting&season=YYYY&sportId=1&limit=1000
    """
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

        # XBT aggression proxy from attempt rate
        if attempt_rate is not None:
            if attempt_rate >= 0.12:   xbt_proxy = 0.58
            elif attempt_rate >= 0.08: xbt_proxy = 0.50
            elif attempt_rate >= 0.05: xbt_proxy = 0.43
            elif attempt_rate >= 0.02: xbt_proxy = 0.36
            else:                      xbt_proxy = 0.28
        else:
            xbt_proxy = None

        result[pid] = {
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
    """
    SB leaderboard — ONE bulk call.
    /stats/leaders?leaderCategories=stolenBases
    """
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

        # Sprint speed proxy from SB volume + implied success
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

def write_meta(files_updated):
    meta = {
        'updated':       datetime.now(timezone.utc).isoformat(),
        'season':        SEASON,
        'files_updated': files_updated,
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
        ('arsenal',          fetch_pitcher_arsenal,    'savant-arsenal.json'),
        ('pitcher-expected', fetch_pitcher_expected,   'savant-expected.json'),
        ('pitcher-pct',      fetch_pitcher_percentiles,'savant-percentiles.json'),
        ('hitter-expected',  fetch_hitter_expected,    'savant-hitter-expected.json'),
        ('hitter-statcast',  fetch_hitter_statcast,    'savant-hitter-statcast.json'),
        ('hitter-pct',       fetch_hitter_percentiles, 'savant-hitter-percentiles.json'),
        ('pop-time',         fetch_pop_time,           'savant-pop-time.json'),
        ('pitch-tempo',      fetch_pitch_tempo,        'savant-pitch-tempo.json'),
        ('baserunning',      fetch_baserunning,        'savant-baserunning.json'),
        ('sb-leaders',       fetch_sb_leaders,         'savant-sb-leaders.json'),
        ('pitcher-sb',       fetch_pitcher_sb_metrics, 'savant-pitcher-sb.json'),
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

    write_meta(files_updated)
    files_updated.append('savant-meta.json')

    print(f'\n=== Done — {len(files_updated)} files updated ===')
    for f in files_updated:
        print(f'  ✓ {f}')
    if errors:
        print(f'\nPartial failures: {", ".join(errors)}')
        core = [e for e in errors if e in ('pitcher-expected', 'hitter-expected', 'hitter-statcast')]
        if core:
            sys.exit(1)


if __name__ == '__main__':
    main()
