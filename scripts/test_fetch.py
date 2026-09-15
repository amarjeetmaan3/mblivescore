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
    try:
        session.patch(f"{FIREBASE_URL}/current_match_auto.json", json={"fetchStatus": {"ok": ok, "error": error or "", "ts": int(time.time())}}, timeout=5)
    except Exception: pass

def extract_json_at(html, key):
    for pattern in (f'"{key}":', f'\\"{key}\\":'):
        idx = html.find(pattern)
        if idx == -1: continue
        start = idx + len(pattern)
        while start < len(html) and html[start] in ' \t\r\n': start += 1
        if start >= len(html) or html[start] not in '{[': continue
        snippet = html[start:start + 400000]
        if pattern.startswith('\\"'): snippet = snippet.replace('\\"', '"').replace('\\\\', '\\').replace('\\/', '/')
        try:
            obj, _ = json.JSONDecoder().raw_decode(snippet)
            return obj
        except Exception: continue
    return None

def get_cricbuzz_data(url, attempts=3):
    last_err = None
    for i in range(attempts):
        headers = { "User-Agent": random.choice(USER_AGENTS), "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.cricbuzz.com/", "Cache-Control": "no-cache" }
        try:
            res = session.get(url, headers=headers, timeout=12)
            if res.status_code in (403, 429):
                last_err = f"HTTP {res.status_code} — Rate limiting"
                time.sleep(3 + i * 3); continue
            if res.status_code != 200:
                last_err = f"HTTP {res.status_code}"
                time.sleep(1.5); continue

            html = res.text
            head_sample = html[:6000].lower()
            if "captcha" in head_sample or "access denied" in head_sample or "unusual traffic" in head_sample:
                last_err = "Bot check page"
                time.sleep(3 + i * 3); continue

            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1)).get("props", {}).get("pageProps", {}).get("page", {})
                    if data and "matchHeader" in data: return data, None
                except Exception as e: last_err = f"Parse err: {e}"

            m, h, sc = extract_json_at(html, "miniscore"), extract_json_at(html, "matchHeader"), extract_json_at(html, "scoreCard")
            if h: return {"miniscore": m or {}, "matchHeader": h or {}, "scoreCard": sc or []}, None
            last_err = "No match data found"
        except requests.exceptions.RequestException as e:
            last_err = f"Network err: {e}"
            time.sleep(1.5)
    return {}, last_err

def get_playing_11(match_header, team_key):
    team_data = match_header.get(team_key, {})
    p_xi, squad = team_data.get("playingXI", ""), team_data.get("squad", "")
    names = []

    def names_from_list_of_dicts(lst):
        out = []
        for item in lst:
            if isinstance(item, dict):
                nm = item.get("name") or item.get("fullName") or item.get("shortName")
                if nm: out.append(nm)
        return out

    if isinstance(p_xi, str) and p_xi.strip(): names = [x.strip() for x in p_xi.split(',') if x.strip()]
    elif isinstance(p_xi, list) and len(p_xi) > 0:
        if isinstance(p_xi[0], dict): names = names_from_list_of_dicts(p_xi)
        else:
            for pid in p_xi:
                for p in match_header.get("players", []):
                    if str(p.get("id")) == str(pid):
                        names.append(p.get("name") or p.get("shortName")); break

    if not names:
        if isinstance(squad, str) and squad.strip(): names = [x.strip() for x in squad.split(',') if x.strip()]
        elif isinstance(squad, list) and len(squad) > 0:
            if isinstance(squad[0], dict): names = names_from_list_of_dicts(squad)
            else:
                for pid in squad:
                    for p in match_header.get("players", []):
                        if str(p.get("id")) == str(pid):
                            names.append(p.get("name") or p.get("shortName")); break
    return names

def fetch_match_smart(match_url, sc_cache):
    live_url = match_url.replace('/live-cricket-scorecard/', '/live-cricket-scores/').replace('/cricket-scorecard/', '/cricket-scores/')
    sc_url = match_url.replace('/live-cricket-scores/', '/live-cricket-scorecard/').replace('/cricket-scores/', '/cricket-scorecard/')

    live_data, live_err = get_cricbuzz_data(live_url)
    m, h = live_data.get("miniscore", {}), live_data.get("matchHeader", {})

    now = time.time()
    sc_err = None
    if now - sc_cache.get('ts', 0) > 20 or not sc_cache.get('data'):
        sc_data, sc_err = get_cricbuzz_data(sc_url)
        if sc_data.get("matchHeader") or sc_data.get("scoreCard"):
            sc_cache['data'], sc_cache['ts'] = sc_data, now
            
    sc_data = sc_cache.get('data', {}) or {}
    full_sc = sc_data.get("scoreCard", [])

    if not h: m, h = sc_data.get("miniscore", {}), sc_data.get("matchHeader", {})
    combined_err = live_err or sc_err
    if not h: return None, combined_err or "no data"

    t1_id, t2_id = str(h.get("team1", {}).get("id", "")), str(h.get("team2", {}).get("id", ""))

    toss_res = h.get("tossResults", {})
    toss_winner = "A" if str(toss_res.get("tossWinnerId", "")) == t1_id else ("B" if str(toss_res.get("tossWinnerId", "")) == t2_id else "A")
    match_format = str(h.get("matchFormat", "")).upper()
    max_overs = 50 if match_format == "ODI" else (90 if match_format == "TEST" else (10 if match_format == "T10" else 20))

    bat_team_inn1 = "A"
    if isinstance(full_sc, list) and len(full_sc) > 0:
        if str(full_sc[0].get("batTeamDetails", {}).get("batTeamId", "")) == t2_id: bat_team_inn1 = "B"
    elif m.get("batTeam", {}).get("teamId"):
        bat_team_inn1 = "B" if str(m.get("batTeam", {}).get("teamId", "")) == t2_id else "A"

    playing11_A, playing11_B = get_playing_11(h, "team1"), get_playing_11(h, "team2")
    
    batting_card_inn1, bowling_card_inn1, fow_inn1, part_inn1 = [], [], [], []
    batting_card_inn2, bowling_card_inn2, fow_inn2, part_inn2 = [], [], [], []
    inn1_details = {"score": 0, "wickets": 0, "overs": "0.0", "extras": {}, "extrasString": ""}
    inn2_details = {"score": 0, "wickets": 0, "overs": "0.0", "extras": {}, "extrasString": ""}

    if isinstance(full_sc, list):
        for idx, inn in enumerate(full_sc):
            bat_card, bowl_card, fow_list, past_parts = [], [], [], []
            
            # PERFECT BATTING CARD & DISMISSALS
            for key, b in inn.get("batTeamDetails", {}).get("batsmenData", {}).items():
                out_desc = str(b.get("outDesc", "")).strip()
                bat_card.append({
                    "name": b.get("batName", "TBA"), "runs": int(b.get("runs", 0)), "balls": int(b.get("balls", 0)), 
                    "fours": int(b.get("fours", 0)), "sixes": int(b.get("sixes", 0)), "outDesc": out_desc, "dismissalInfo": out_desc,
                    "isOut": bool(out_desc and out_desc.lower() not in ['not out', 'batting', 'retired hurt'])
                })
                
            # PERFECT BOWLING CARD
            for key, bw in inn.get("bowlTeamDetails", {}).get("bowlersData", {}).items():
                bowl_card.append({
                    "name": bw.get("bowlName", "TBA"), "overs": float(bw.get("overs", 0)), "maidens": int(bw.get("maidens", 0)), 
                    "runs": int(bw.get("runs", 0)), "wickets": int(bw.get("wickets", 0)), "wides": int(bw.get("wides", 0)),
                    "noBalls": int(bw.get("no_balls", 0)), "economy": str(bw.get("economy", "0.0"))
                })
                
            # FOW 
            for i, (key, f) in enumerate(inn.get("fowData", {}).items()):
                fow_list.append({"wktNo": f.get("wicketNum") or (i + 1), "score": f.get("score", 0), "overs": str(f.get("overs", "0.0")), "batterName": f.get("batName", "Unknown")})
                
            # ALL PARTNERSHIPS
            for key, p in inn.get("partnershipsData", {}).items():
                past_parts.append({
                    "wktNo": p.get("wicketNum", 0), "bat1Name": p.get("bat1Name", ""), "bat1Runs": p.get("bat1Runs", 0), "bat1Balls": p.get("bat1Balls", 0),
                    "bat2Name": p.get("bat2Name", ""), "bat2Runs": p.get("bat2Runs", 0), "bat2Balls": p.get("bat2Balls", 0),
                    "totalRuns": p.get("totalRuns", 0), "totalBalls": p.get("totalBalls", 0)
                })

            # TOTAL & EXTRAS FOR BOTTOM STRIP
            score_det = inn.get("scoreDetails", {})
            extras_det = inn.get("extrasData", {})
            ex_total = extras_det.get("total", 0)
            ex_b = extras_det.get("byes", 0)
            ex_lb = extras_det.get("legByes", 0)
            ex_w = extras_det.get("wides", 0)
            ex_nb = extras_det.get("noBalls", 0)
            ex_p = extras_det.get("penalty", 0)
            
            details = {
                "score": score_det.get("runs", 0), "wickets": score_det.get("wickets", 0), "overs": str(score_det.get("overs", "0.0")),
                "extras": {"total": ex_total, "byes": ex_b, "legByes": ex_lb, "wides": ex_w, "noBalls": ex_nb, "penalty": ex_p},
                "extrasString": f"{ex_total} (b {ex_b}, lb {ex_lb}, w {ex_w}, nb {ex_nb}, p {ex_p})"
            }

            if idx == 0: 
                batting_card_inn1, bowling_card_inn1, fow_inn1, part_inn1 = bat_card, bowl_card, fow_list, past_parts
                inn1_details = details
            elif idx == 1: 
                batting_card_inn2, bowling_card_inn2, fow_inn2, part_inn2 = bat_card, bowl_card, fow_list, past_parts
                inn2_details = details

    data = {
        "teamA": h.get("team1", {}).get("shortName", "TBA"), "teamA_name": h.get("team1", {}).get("name", "Team A"),
        "teamB": h.get("team2", {}).get("shortName", "TBB"), "teamB_name": h.get("team2", {}).get("name", "Team B"),
        "bat_team_inn1": bat_team_inn1, "maxOvers": max_overs, "tossWinner": toss_winner, "tossDecision": str(toss_res.get("decision", "BAT")).upper(),
        "score": m.get("batTeam", {}).get("teamScore", 0), "wickets": m.get("batTeam", {}).get("teamWkts", 0),
        "overs": m.get("overs", "0.0"), "target": m.get("target", 0), "crr": m.get("currentRunRate", "0.00"),
        "matchStatus": h.get("status", ""), "isComplete": str(h.get("state", "")) == "Complete" or h.get("complete", False),
        "strikerName": m.get("batsmanStriker", {}).get("name", "—"), "strikerRuns": m.get("batsmanStriker", {}).get("runs", 0), "strikerBalls": m.get("batsmanStriker", {}).get("balls", 0),
        "nonStrikerName": m.get("batsmanNonStriker", {}).get("name", "—"), "nonStrikerRuns": m.get("batsmanNonStriker", {}).get("runs", 0), "nonStrikerBalls": m.get("batsmanNonStriker", {}).get("balls", 0),
        "bowlerName": m.get("bowlerStriker", {}).get("name", "—"), "bowlerOvers": m.get("bowlerStriker", {}).get("overs", "0.0"), "bowlerRuns": m.get("bowlerStriker", {}).get("runs", 0), "bowlerWickets": m.get("bowlerStriker", {}).get("wickets", 0),
        "currPartnershipRuns": m.get("partnerShip", {}).get("runs", 0), "currPartnershipBalls": m.get("partnerShip", {}).get("balls", 0), "recentOvs": m.get("recentOvsStats", ""),
        "playing11_A": playing11_A, "playing11_B": playing11_B, 
        
        "inn1_details": inn1_details, "inn2_details": inn2_details,
        "battingCard_inn1": batting_card_inn1, "bowlingCard_inn1": bowling_card_inn1, "fow_inn1": fow_inn1, "pastParts_inn1": part_inn1,
        "battingCard_inn2": batting_card_inn2, "bowlingCard_inn2": bowling_card_inn2, "fow_inn2": fow_inn2, "pastParts_inn2": part_inn2
    }
    return data, combined_err

start_time = time.time()
last_url = ""
sc_cache = {}
print("Cricbuzz fetcher started (Full 1st Innings Data Extraction)...", flush=True)

while time.time() - start_time < 6 * 60 * 60:
    try:
        config_data = session.get(f"{FIREBASE_URL}/auto_fetch_config.json", timeout=10).json()
        if not config_data or 'url' not in config_data:
            time.sleep(4); continue
        current_url = config_data['url']
        if current_url != last_url:
            last_url = current_url; sc_cache = {}; session.delete(f"{FIREBASE_URL}/current_match_auto.json")
            print(f"New Link: {current_url}", flush=True)
        data, err = fetch_match_smart(current_url, sc_cache)
        if data:
            data["fetchStatus"] = {"ok": True, "error": err or "", "ts": int(time.time())}
            session.put(f"{FIREBASE_URL}/current_match_auto.json", json=data, timeout=5)
            print(f"Pushed: {data['teamA']} vs {data['teamB']} | Score: {data['score']}/{data['wickets']}" + (f" | warn: {err}" if err else ""), flush=True)
        else:
            push_status(False, err)
    except Exception as e: push_status(False, f"script error: {e}")
    time.sleep(4)
