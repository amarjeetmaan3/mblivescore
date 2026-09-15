import requests, json, os, re, time
from datetime import datetime

FIREBASE_URL = os.environ.get('FIREBASE_DB_URL', '')

def extract_raw_json(html, key):
    # Fallback: Agar Cricbuzz data chupaye, toh raw HTML se nikaalna
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
    # ANTI-BOT HEADERS: Cricbuzz ko lagega ki ye asli Google Chrome hai
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/"
    }
    try:
        res = requests.get(url, headers=headers, timeout=15)
        html = res.text
        
        # Attempt 1: Next.js Data
        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
        if match:
            data = json.loads(match.group(1)).get("props", {}).get("pageProps", {}).get("page", {})
            if data and "matchHeader" in data: return data, html
            
        # Attempt 2: Raw HTML Extraction (Hybrid)
        m = extract_raw_json(html, "miniscore")
        h = extract_raw_json(html, "matchHeader")
        sc = extract_raw_json(html, "scoreCard")
        
        return {"miniscore": m or {}, "matchHeader": h or {}, "scoreCard": sc or []}, html
    except Exception as e:
        print("HTTP Fetch Error:", e, flush=True)
        return {}, ""

def get_playing_11(match_header, team_key):
    team_data = match_header.get(team_key, {})
    p_ids = team_data.get("playingXI", []) or team_data.get("squad", [])
    names = []
    players_meta = match_header.get("players", [])
    
    def find_name(pid):
        pid = str(pid)
        if isinstance(players_meta, list):
            for p in players_meta:
                if str(p.get("id")) == pid: return p.get("name") or p.get("shortName")
        elif isinstance(players_meta, dict):
            p = players_meta.get(pid, {})
            if isinstance(p, dict): return p.get("name") or p.get("shortName")
        return f"Player {pid}"

    for pid in p_ids: names.append(find_name(pid))
    while len(names) < 11: names.append(f"TBA {len(names)+1}")
    return names[:11]

def fetch_match_smart(match_url):
    # AUTO-URL CORRECTOR
    live_url = match_url.replace('/live-cricket-scorecard/', '/live-cricket-scores/').replace('/cricket-scorecard/', '/cricket-scores/')
    sc_url = match_url.replace('/live-cricket-scores/', '/live-cricket-scorecard/').replace('/cricket-scores/', '/cricket-scorecard/')
    
    live_data, _ = get_cricbuzz_data(live_url)
    m = live_data.get("miniscore", {})
    h = live_data.get("matchHeader", {})
    
    sc_data, _ = get_cricbuzz_data(sc_url)
    full_sc = sc_data.get("scoreCard", [])
    
    if not h:
        m = sc_data.get("miniscore", {})
        h = sc_data.get("matchHeader", {})
        
    if not h: return None

    playing11_A = get_playing_11(h, "team1")
    playing11_B = get_playing_11(h, "team2")

    batting_card_inn1, bowling_card_inn1, fow_inn1, part_inn1 = [], [], [], []
    batting_card_inn2, bowling_card_inn2, fow_inn2, part_inn2 = [], [], [], []
    extras_inn1, extras_inn2 = 0, 0
    
    if isinstance(full_sc, list):
        for idx, inn in enumerate(full_sc):
            bat_card, bowl_card, fow_list, past_parts = [], [], [], []
            
            for key, b in inn.get("batTeamDetails", {}).get("batsmenData", {}).items():
                out_desc = str(b.get("outDesc", "")).strip()
                is_out = bool(out_desc and out_desc.lower() not in ['not out', 'batting'])
                bat_card.append({"name": b.get("batName", "TBA"), "runs": int(b.get("runs", 0)), "balls": int(b.get("balls", 0)), "fours": int(b.get("fours", 0)), "sixes": int(b.get("sixes", 0)), "outDesc": out_desc, "isOut": is_out})
            
            for key, bw in inn.get("bowlTeamDetails", {}).get("bowlersData", {}).items():
                bowl_card.append({"name": bw.get("bowlName", "TBA"), "overs": float(bw.get("overs", 0)), "maidens": int(bw.get("maidens", 0)), "runs": int(bw.get("runs", 0)), "wickets": int(bw.get("wickets", 0))})
            
            for i, (key, f) in enumerate(inn.get("fowData", {}).items()):
                fow_list.append({"wktNo": i + 1, "score": f.get("score", 0), "overs": str(f.get("overs", "0.0")), "batterName": f.get("batName", "Unknown")})
                    
            for key, p in inn.get("partnershipsData", {}).items():
                past_parts.append({"wktNo": p.get("wicketNum", 0), "bat1Name": p.get("bat1Name", ""), "bat1Runs": p.get("bat1Runs", 0), "bat2Name": p.get("bat2Name", ""), "bat2Runs": p.get("bat2Runs", 0), "totalRuns": p.get("totalRuns", 0), "totalBalls": p.get("totalBalls", 0)})
            
            extras_val = int(inn.get("extrasData", {}).get("total", 0))

            if idx == 0: batting_card_inn1, bowling_card_inn1, fow_inn1, part_inn1, extras_inn1 = bat_card, bowl_card, fow_list, past_parts, extras_val
            elif idx == 1: batting_card_inn2, bowling_card_inn2, fow_inn2, part_inn2, extras_inn2 = bat_card, bowl_card, fow_list, past_parts, extras_val

    is_complete = str(h.get("state", "")) == "Complete" or h.get("complete", False)
    
    data = {
        "teamA": h.get("team1", {}).get("shortName", "TBA"), "teamB": h.get("team2", {}).get("shortName", "TBB"),
        "score": m.get("batTeam", {}).get("teamScore", 0), "wickets": m.get("batTeam", {}).get("teamWkts", 0),
        "overs": m.get("overs", "0.0"), "target": m.get("target", 0), "crr": m.get("currentRunRate", "0.00"),
        "matchStatus": h.get("status", ""), "isComplete": is_complete,
        
        "strikerName": m.get("batsmanStriker", {}).get("name", "—"), "strikerRuns": m.get("batsmanStriker", {}).get("runs", 0), "strikerBalls": m.get("batsmanStriker", {}).get("balls", 0),
        "nonStrikerName": m.get("batsmanNonStriker", {}).get("name", "—"), "nonStrikerRuns": m.get("batsmanNonStriker", {}).get("runs", 0), "nonStrikerBalls": m.get("batsmanNonStriker", {}).get("balls", 0),
        "bowlerName": m.get("bowlerStriker", {}).get("name", "—"), "bowlerOvers": m.get("bowlerStriker", {}).get("overs", "0.0"), "bowlerRuns": m.get("bowlerStriker", {}).get("runs", 0), "bowlerWickets": m.get("bowlerStriker", {}).get("wickets", 0),
        
        "currPartnershipRuns": m.get("partnerShip", {}).get("runs", 0), "currPartnershipBalls": m.get("partnerShip", {}).get("balls", 0),
        "recentOvs": m.get("recentOvsStats", ""),
        
        "playing11_A": playing11_A, "playing11_B": playing11_B,
        "extras_inn1": extras_inn1, "extras_inn2": extras_inn2,
        "battingCard_inn1": batting_card_inn1, "bowlingCard_inn1": bowling_card_inn1, "fow_inn1": fow_inn1, "pastParts_inn1": part_inn1,
        "battingCard_inn2": batting_card_inn2, "bowlingCard_inn2": bowling_card_inn2, "fow_inn2": fow_inn2, "pastParts_inn2": part_inn2
    }
    return data

start_time = time.time()
MAX_DURATION = 6 * 60 * 60
last_url = ""

print("Hybrid Script Started! Bypassing blocks...", flush=True)

while time.time() - start_time < MAX_DURATION:
    try:
        config_res = requests.get(f"{FIREBASE_URL}/auto_fetch_config.json", timeout=10)
        config_data = config_res.json()
        
        if not config_data or 'url' not in config_data:
            time.sleep(15)
            continue
            
        current_url = config_data['url']
        if current_url != last_url:
            last_url = current_url
            requests.delete(f"{FIREBASE_URL}/current_match_auto.json") 
            print(f"\n[NEW LINK] Wiping old data for: {current_url}", flush=True)
            
        data = fetch_match_smart(current_url)
        if data:
            requests.put(f"{FIREBASE_URL}/current_match_auto.json", json=data, timeout=10)
            print(f"[SUCCESS] Pushed: {data['teamA']} vs {data['teamB']} | Score: {data['score']}/{data['wickets']}", flush=True)
        else:
            print("[FAILED] Blocked by Cricbuzz or Invalid Link.", flush=True)
    except Exception as e: 
        print(f"[ERROR] Loop Exception: {e}", flush=True)
    
    time.sleep(15)
