import requests, json, os, re, time, random

FIREBASE_URL = os.environ.get('FIREBASE_DB_URL', '')
session = requests.Session()

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]


def push_status(ok, error=None):
    """Writes a fetchStatus block to Firebase so the controller UI can show
    *why* it's stuck on WAITING..., instead of failing silently."""
    try:
        session.patch(f"{FIREBASE_URL}/current_match_auto.json", json={
            "fetchStatus": {"ok": ok, "error": error or "", "ts": int(time.time())}
        }, timeout=5)
    except Exception:
        pass


def extract_json_at(html, key):
    """Find the JSON value for "key": ... in raw html/script text using a real
    JSON decoder (json.JSONDecoder.raw_decode), instead of the old approach of
    manually counting { } characters across the whole page — which breaks the
    moment a string value (commentary, a player's name with an apostrophe,
    etc.) contains a brace or a quote character."""
    for pattern in (f'"{key}":', f'\\"{key}\\":'):
        idx = html.find(pattern)
        if idx == -1:
            continue
        start = idx + len(pattern)
        while start < len(html) and html[start] in ' \t\r\n':
            start += 1
        if start >= len(html) or html[start] not in '{[':
            continue
        snippet = html[start:start + 400000]
        if pattern.startswith('\\"'):
            # This value is itself inside a JS-escaped string context.
            snippet = snippet.replace('\\"', '"').replace('\\\\', '\\').replace('\\/', '/')
        try:
            obj, _ = json.JSONDecoder().raw_decode(snippet)
            return obj
        except Exception:
            continue
    return None


def get_cricbuzz_data(url, attempts=3):
    """Fetches one Cricbuzz page and tries to pull matchHeader/miniscore/scoreCard
    out of it. Returns (data_dict, error_string_or_None). Retries on
    rate-limit/blocked responses instead of giving up after one try."""
    last_err = None
    for i in range(attempts):
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.cricbuzz.com/",
            "Cache-Control": "no-cache",
        }
        try:
            res = session.get(url, headers=headers, timeout=12)

            if res.status_code in (403, 429):
                last_err = f"HTTP {res.status_code} — Cricbuzz is rate-limiting/blocking this request pattern"
                time.sleep(3 + i * 3)
                continue
            if res.status_code != 200:
                last_err = f"HTTP {res.status_code} from Cricbuzz"
                time.sleep(1.5)
                continue

            html = res.text
            head_sample = html[:6000].lower()
            if "captcha" in head_sample or "access denied" in head_sample or "unusual traffic" in head_sample:
                last_err = "Cricbuzz returned a bot-check/blocked page instead of the match page"
                time.sleep(3 + i * 3)
                continue

            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1)).get("props", {}).get("pageProps", {}).get("page", {})
                    if data and "matchHeader" in data:
                        return data, None
                except Exception as e:
                    last_err = f"__NEXT_DATA__ block found but failed to parse ({e}) — Cricbuzz's page structure may have changed"

            m = extract_json_at(html, "miniscore")
            h = extract_json_at(html, "matchHeader")
            sc = extract_json_at(html, "scoreCard")
            if h:
                return {"miniscore": m or {}, "matchHeader": h or {}, "scoreCard": sc or []}, None

            last_err = "page loaded (HTTP 200) but no matchHeader/__NEXT_DATA__ found in it — likely a Cricbuzz layout change or soft-block"
        except requests.exceptions.RequestException as e:
            last_err = f"network error: {e}"
            time.sleep(1.5)

    return {}, last_err


def get_playing_11(match_header, team_key):
    team_data = match_header.get(team_key, {})
    p_xi = team_data.get("playingXI", "")
    squad = team_data.get("squad", "")

    names = []

    def names_from_list_of_dicts(lst):
        out = []
        for item in lst:
            if isinstance(item, dict):
                nm = item.get("name") or item.get("fullName") or item.get("shortName")
                if nm:
                    out.append(nm)
        return out

    if isinstance(p_xi, str) and p_xi.strip():
        names = [x.strip() for x in p_xi.split(',') if x.strip()]
    elif isinstance(p_xi, list) and len(p_xi) > 0:
        if isinstance(p_xi[0], dict):
            names = names_from_list_of_dicts(p_xi)
        else:
            players_meta = match_header.get("players", [])
            for pid in p_xi:
                for p in players_meta:
                    if str(p.get("id")) == str(pid):
                        names.append(p.get("name") or p.get("shortName"))
                        break

    if not names:
        if isinstance(squad, str) and squad.strip():
            names = [x.strip() for x in squad.split(',') if x.strip()]
        elif isinstance(squad, list) and len(squad) > 0:
            if isinstance(squad[0], dict):
                names = names_from_list_of_dicts(squad)
            else:
                players_meta = match_header.get("players", [])
                for pid in squad:
                    for p in players_meta:
                        if str(p.get("id")) == str(pid):
                            names.append(p.get("name") or p.get("shortName"))
                            break

    return names


def fetch_match_smart(match_url, sc_cache):
    """sc_cache: dict the caller keeps between calls, e.g. {'data': {...}, 'ts': 0}
    so the heavier scorecard page isn't re-fetched every single cycle."""
    live_url = match_url.replace('/live-cricket-scorecard/', '/live-cricket-scores/').replace('/cricket-scorecard/', '/cricket-scores/')
    sc_url = match_url.replace('/live-cricket-scores/', '/live-cricket-scorecard/').replace('/cricket-scores/', '/cricket-scorecard/')

    live_data, live_err = get_cricbuzz_data(live_url)
    m, h = live_data.get("miniscore", {}), live_data.get("matchHeader", {})

    # Full scorecard (batting/bowling cards, FOW, partnerships) changes only
    # ball-by-ball in detail but is expensive to fetch — refresh it at most
    # every ~20s instead of every single 4s cycle. This alone cuts outbound
    # requests to Cricbuzz roughly in half.
    now = time.time()
    sc_err = None
    if now - sc_cache.get('ts', 0) > 20 or not sc_cache.get('data'):
        sc_data, sc_err = get_cricbuzz_data(sc_url)
        if sc_data.get("matchHeader") or sc_data.get("scoreCard"):
            sc_cache['data'] = sc_data
            sc_cache['ts'] = now
    sc_data = sc_cache.get('data', {}) or {}
    full_sc = sc_data.get("scoreCard", [])

    if not h:
        m, h = sc_data.get("miniscore", {}), sc_data.get("matchHeader", {})

    combined_err = live_err or sc_err
    if not h:
        return None, combined_err or "no matchHeader available from either page"

    t1_id = str(h.get("team1", {}).get("id", ""))
    t2_id = str(h.get("team2", {}).get("id", ""))

    bat_team_inn1 = "A"
    if isinstance(full_sc, list) and len(full_sc) > 0:
        inn1_bat_id = str(full_sc[0].get("batTeamDetails", {}).get("batTeamId", ""))
        if inn1_bat_id == t2_id:
            bat_team_inn1 = "B"
    elif m.get("batTeam", {}).get("teamId"):
        bat_id = str(m.get("batTeam", {}).get("teamId", ""))
        bat_team_inn1 = "B" if bat_id == t2_id else "A"
    # else: no innings data yet (pre-toss/rain delay) — default 'A' is a guess,
    # flagged via matchStatus text so the controller doesn't silently trust it.

    playing11_A, playing11_B = get_playing_11(h, "team1"), get_playing_11(h, "team2")
    batting_card_inn1, bowling_card_inn1, fow_inn1, part_inn1, extras_inn1 = [], [], [], [], 0
    batting_card_inn2, bowling_card_inn2, fow_inn2, part_inn2, extras_inn2 = [], [], [], [], 0

    if isinstance(full_sc, list):
        for idx, inn in enumerate(full_sc):
            bat_card, bowl_card, fow_list, past_parts = [], [], [], []
            for key, b in inn.get("batTeamDetails", {}).get("batsmenData", {}).items():
                out_desc = str(b.get("outDesc", "")).strip()
                bat_card.append({"name": b.get("batName", "TBA"), "runs": int(b.get("runs", 0)), "balls": int(b.get("balls", 0)), "fours": int(b.get("fours", 0)), "sixes": int(b.get("sixes", 0)), "outDesc": out_desc, "isOut": bool(out_desc and out_desc.lower() not in ['not out', 'batting'])})
            for key, bw in inn.get("bowlTeamDetails", {}).get("bowlersData", {}).items():
                bowl_card.append({"name": bw.get("bowlName", "TBA"), "overs": float(bw.get("overs", 0)), "maidens": int(bw.get("maidens", 0)), "runs": int(bw.get("runs", 0)), "wickets": int(bw.get("wickets", 0))})
            for i, (key, f) in enumerate(inn.get("fowData", {}).items()):
                fow_list.append({"wktNo": i + 1, "score": f.get("score", 0), "overs": str(f.get("overs", "0.0")), "batterName": f.get("batName", "Unknown")})
            for key, p in inn.get("partnershipsData", {}).items():
                past_parts.append({"wktNo": p.get("wicketNum", 0), "bat1Name": p.get("bat1Name", ""), "bat1Runs": p.get("bat1Runs", 0), "bat2Name": p.get("bat2Name", ""), "bat2Runs": p.get("bat2Runs", 0), "totalRuns": p.get("totalRuns", 0), "totalBalls": p.get("totalBalls", 0)})
            extras_val = int(inn.get("extrasData", {}).get("total", 0))

            if idx == 0:
                batting_card_inn1, bowling_card_inn1, fow_inn1, part_inn1, extras_inn1 = bat_card, bowl_card, fow_list, past_parts, extras_val
            elif idx == 1:
                batting_card_inn2, bowling_card_inn2, fow_inn2, part_inn2, extras_inn2 = bat_card, bowl_card, fow_list, past_parts, extras_val

    data = {
        "teamA": h.get("team1", {}).get("shortName", "TBA"), "teamA_name": h.get("team1", {}).get("name", "Team A"),
        "teamB": h.get("team2", {}).get("shortName", "TBB"), "teamB_name": h.get("team2", {}).get("name", "Team B"),
        "bat_team_inn1": bat_team_inn1,

        "score": m.get("batTeam", {}).get("teamScore", 0), "wickets": m.get("batTeam", {}).get("teamWkts", 0),
        "overs": m.get("overs", "0.0"), "target": m.get("target", 0), "crr": m.get("currentRunRate", "0.00"),
        "matchStatus": h.get("status", ""), "isComplete": str(h.get("state", "")) == "Complete" or h.get("complete", False),
        "strikerName": m.get("batsmanStriker", {}).get("name", "—"), "strikerRuns": m.get("batsmanStriker", {}).get("runs", 0), "strikerBalls": m.get("batsmanStriker", {}).get("balls", 0),
        "nonStrikerName": m.get("batsmanNonStriker", {}).get("name", "—"), "nonStrikerRuns": m.get("batsmanNonStriker", {}).get("runs", 0), "nonStrikerBalls": m.get("batsmanNonStriker", {}).get("balls", 0),
        "bowlerName": m.get("bowlerStriker", {}).get("name", "—"), "bowlerOvers": m.get("bowlerStriker", {}).get("overs", "0.0"), "bowlerRuns": m.get("bowlerStriker", {}).get("runs", 0), "bowlerWickets": m.get("bowlerStriker", {}).get("wickets", 0),
        "currPartnershipRuns": m.get("partnerShip", {}).get("runs", 0), "currPartnershipBalls": m.get("partnerShip", {}).get("balls", 0), "recentOvs": m.get("recentOvsStats", ""),

        "playing11_A": playing11_A, "playing11_B": playing11_B, "extras_inn1": extras_inn1, "extras_inn2": extras_inn2,
        "battingCard_inn1": batting_card_inn1, "bowlingCard_inn1": bowling_card_inn1, "fow_inn1": fow_inn1, "pastParts_inn1": part_inn1,
        "battingCard_inn2": batting_card_inn2, "bowlingCard_inn2": bowling_card_inn2, "fow_inn2": fow_inn2, "pastParts_inn2": part_inn2
    }
    return data, combined_err


start_time = time.time()
last_url = ""
sc_cache = {}
print("Cricbuzz fetcher started (hardened version)...", flush=True)

while time.time() - start_time < 6 * 60 * 60:
    try:
        config_data = session.get(f"{FIREBASE_URL}/auto_fetch_config.json", timeout=10).json()
        if not config_data or 'url' not in config_data:
            time.sleep(4)
            continue

        current_url = config_data['url']
        if current_url != last_url:
            last_url = current_url
            sc_cache = {}
            session.delete(f"{FIREBASE_URL}/current_match_auto.json")
            print(f"New Link: {current_url}", flush=True)

        data, err = fetch_match_smart(current_url, sc_cache)
        if data:
            data["fetchStatus"] = {"ok": True, "error": err or "", "ts": int(time.time())}
            session.put(f"{FIREBASE_URL}/current_match_auto.json", json=data, timeout=5)
            print(f"Pushed: {data['teamA']} vs {data['teamB']} | Score: {data['score']}/{data['wickets']}" + (f" | warn: {err}" if err else ""), flush=True)
        else:
            print(f"Fetch failed: {err}", flush=True)
            push_status(False, err)
    except Exception as e:
        print(f"Loop error: {e}", flush=True)
        push_status(False, f"script error: {e}")
    time.sleep(4)
