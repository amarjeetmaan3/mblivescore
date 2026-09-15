import requests, json, os, re, time

FIREBASE_URL = os.environ.get('FIREBASE_DB_URL', '')
session = requests.Session()

def extract_raw_json(html, key):
    idx = html.find(f'"{key}":')
    if idx == -1: idx = html.find(f'\\"{key}\\":')
    if idx == -1: return None
    start = html.find(':', idx) + 1
    while start < len(html) and html[start] not in ['{', '[']: start += 1
    if start >= len(html): return None
    open_char, close_char, depth = html[start], '}' if html[start] == '{' else ']', 0
    for i in range(start, len(html)):
        if html[i] == open_char: depth += 1
        elif html[i] == close_char:
            depth -= 1
            if depth == 0:
                try:
                    raw = html[start:i+1].replace('\\"', '"').replace('\\\\', '\\') if '\\"' in html[start:i+1] else html[start:i+1]
                    return json.loads(raw)
                except: return None
    return None

def get_cricbuzz_data(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    }
    try:
        res = session.get(url, headers=headers, timeout=10)
        html = res.text
        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
        if match:
            data = json.loads(match.group(1)).get("props", {}).get("pageProps", {}).get("page", {})
            if data and "matchHeader" in data: return data, html
        m, h, sc = extract_raw_json(html, "miniscore"), extract_raw_json(html, "matchHeader"), extract_raw_json(html, "scoreCard")
        return {"miniscore": m or {}, "matchHeader": h or {}, "scoreCard": sc or []}, html
    except Exception as e:
        print("HTTP Fetch Error:", e, flush=True)
        return {}, ""

def get_playing_11(match_header, team_key):
    team_data = match_header.get(team_key, {})
    p_xi = team_data.get("playingXI", "")
    squad = team_data.get("squad", "")
    
    names = []
    # Extract from comma-separated string if available
    if isinstance(p_xi, str) and p_xi.strip():
        names = [x.strip() for x in p_xi.split(',') if x.strip()]
    elif isinstance(p_xi, list) and len(p_xi) > 0:
        players_meta = match_header.get("players", [])
        for pid in p_xi:
            for p in players_meta:
                if str(p.get("id")) == str(pid):
                    names.append(p.get("name") or p.get("shortName"))
                    break
                    
    # Fallback to Squad if playing11 is empty
    if not names:
        if isinstance(squad, str) and squad.strip():
            names = [x.strip() for x in squad.split(',') if x.strip()]
        elif isinstance(squad, list) and len(squad) > 0:
            players_meta = match_header.get("players", [])
            for pid in squad:
                for p in players_meta:
                    if str(p.get("id")) == str(pid):
                        names.append(p.get("name") or p.get("shortName"))
                        break
                        
    return names

def fetch_match_smart(match_url):
    live_url = match_url.replace('/live-cricket-scorecard/', '/live-cricket-scores/').replace('/cricket-scorecard/', '/cricket-scores/')
    sc_url = match_url.replace('/live-cricket-scores/', '/live-cricket-scorecard/').replace('/cricket-scores/', '/cricket-scorecard/')
    
    live_data, _ = get_cricbuzz_data(live_url)
    m, h = live_data.get("miniscore", {}), live_data.get("matchHeader", {})
    sc_data, _ = get_cricbuzz_data(sc_url)
    full_sc = sc_data.get("scoreCard", [])
    
    if not h:
        m, h = sc_data.get("miniscore", {}), sc_data.get("matchHeader", {})
    if not h: return None

    # STRICT TEAM IDENTIFICATION
    t1_id = str(h.get("team1", {}).get("id", ""))
    t2_id = str(h.get("team2", {}).get("id", ""))
    
    # Identify who batted first exactly
    bat_team_inn1 = "A"
    if isinstance(full_sc, list) and len(full_sc) > 0:
        inn1_bat_id = str(full_sc[0].get("batTeamDetails", {}).get("batTeamId", ""))
        if inn1_bat_id == t2_id: bat_team_inn1 = "B"
    else:
        bat_id = str(m.get("batTeam", {}).get("teamId", ""))
        bat_team_inn1 = "B" if bat_id == t2_id else "A"

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

            if idx == 0: batting_card_inn1, bowling_card_inn1, fow_inn1, part_inn1, extras_inn1 = bat_card, bowl_card, fow_list, past_parts, extras_val
            elif idx == 1: batting_card_inn2, bowling_card_inn2, fow_inn2, part_inn2, extras_inn2 = bat_card, bowl_card, fow_list, past_parts, extras_val

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
    return data

start_time = time.time()
last_url = ""
print("Super Fast Script Started (Fixed Teams & Names)...", flush=True)

while time.time() - start_time < 6 * 60 * 60:
    try:
        config_data = session.get(f"{FIREBASE_URL}/auto_fetch_config.json", timeout=10).json()
        if not config_data or 'url' not in config_data:
            time.sleep(4); continue
            
        current_url = config_data['url']
        if current_url != last_url:
            last_url = current_url
            session.delete(f"{FIREBASE_URL}/current_match_auto.json") 
            print(f"New Link: {current_url}", flush=True)
            
        data = fetch_match_smart(current_url)
        if data:
            session.put(f"{FIREBASE_URL}/current_match_auto.json", json=data, timeout=5)
            print(f"Pushed: {data['teamA']} vs {data['teamB']} | Score: {data['score']}/{data['wickets']}", flush=True)
    except Exception as e: pass
    time.sleep(4)
