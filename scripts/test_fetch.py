import requests, json, os, re, time
from datetime import datetime

FIREBASE_URL = os.environ.get('FIREBASE_DB_URL', '')

def extract_json_data(html, key):
    idx = html.find(f'\\"{key}\\":')
    start = idx + len(f'\\"{key}\\":') if idx != -1 else html.find(f'"{key}":') + len(f'"{key}":') if html.find(f'"{key}":') != -1 else -1
    if start == -1: return None
    
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

def get_playing_11(match_header, team_key):
    team_data = match_header.get(team_key, {})
    p_ids = team_data.get("playingXI", []) or team_data.get("squad", [])
    names, players_meta = [], match_header.get("players", [])
    
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
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(match_url, headers=headers, timeout=15)
    html = res.text
    
    m, h = extract_json_data(html, "miniscore"), extract_json_data(html, "matchHeader")
    
    if not m or not h:
        sc_url = match_url.replace('/live-cricket-scores/', '/live-cricket-scorecard/')
        res_sc = requests.get(sc_url, headers=headers, timeout=15)
        html = res_sc.text
        m, h = extract_json_data(html, "miniscore"), extract_json_data(html, "matchHeader")
        
    if not m or not h: return None, True

    # 1. PLAYING 11
    playing11_A, playing11_B = get_playing_11(h, "team1"), get_playing_11(h, "team2")

    batting_card_inn1, bowling_card_inn1, fow_inn1 = [], [], []
    batting_card_inn2, bowling_card_inn2, fow_inn2 = [], [], []
    
    # 2. FULL SCORECARD PARSING (No guesswork, strictly ordered)
    try:
        sc_url = match_url.replace('/live-cricket-scores/', '/live-cricket-scorecard/')
        res_sc_full = requests.get(sc_url, headers=headers, timeout=15)
        full_sc = extract_json_data(res_sc_full.text, "scoreCard")
        
        if full_sc and isinstance(full_sc, list):
            for idx, inn in enumerate(full_sc):
                bat_card, bowl_card, fow_list = [], [], []
                
                # Batting Card
                if "batTeamDetails" in inn and "batsmenData" in inn["batTeamDetails"]:
                    for key, b in inn["batTeamDetails"]["batsmenData"].items():
                        out_desc = str(b.get("outDesc", "")).strip()
                        is_out = bool(out_desc and out_desc.lower() not in ['not out', 'batting'])
                        bat_card.append({"name": b.get("batName", "TBA"), "runs": int(b.get("runs", 0)), "balls": int(b.get("balls", 0)), "fours": int(b.get("fours", 0)), "sixes": int(b.get("sixes", 0)), "outDesc": out_desc, "isOut": is_out})
                
                # Bowling Card
                if "bowlTeamDetails" in inn and "bowlersData" in inn["bowlTeamDetails"]:
                    for key, bw in inn["bowlTeamDetails"]["bowlersData"].items():
                        bowl_card.append({"name": bw.get("bowlName", "TBA"), "overs": float(bw.get("overs", 0)), "maidens": int(bw.get("maidens", 0)), "runs": int(bw.get("runs", 0)), "wickets": int(bw.get("wickets", 0))})
                
                # Fall of Wickets (FOW)
                if "fowData" in inn:
                    for i, (key, f) in enumerate(inn["fowData"].items()):
                        fow_list.append({"wktNo": i + 1, "score": f.get("score", 0), "overs": str(f.get("overs", "0.0")), "batterName": f.get("batName", "Unknown")})
                
                if idx == 0: batting_card_inn1, bowling_card_inn1, fow_inn1 = bat_card, bowl_card, fow_list
                elif idx == 1: batting_card_inn2, bowling_card_inn2, fow_inn2 = bat_card, bowl_card, fow_list
    except Exception as e: print("Scorecard Parsing Error:", e)

    match_state = str(h.get("state", ""))
    is_complete = match_state == "Complete" or h.get("complete", False)
    
    data = {
        "teamA": h.get("team1", {}).get("shortName", "TBA"),
        "teamB": h.get("team2", {}).get("shortName", "TBB"),
        "score": m.get("batTeam", {}).get("teamScore", 0),
        "wickets": m.get("batTeam", {}).get("teamWkts", 0),
        "overs": m.get("overs", "0.0"),
        "target": m.get("target", 0),
        "crr": m.get("currentRunRate", "0.00"),
        "matchStatus": h.get("status", ""), # Result Text (e.g. "IND won by 5 wkts")
        "isComplete": is_complete,
        
        "strikerName": m.get("batsmanStriker", {}).get("name", "—"), "strikerRuns": m.get("batsmanStriker", {}).get("runs", 0), "strikerBalls": m.get("batsmanStriker", {}).get("balls", 0),
        "nonStrikerName": m.get("batsmanNonStriker", {}).get("name", "—"), "nonStrikerRuns": m.get("batsmanNonStriker", {}).get("runs", 0), "nonStrikerBalls": m.get("batsmanNonStriker", {}).get("balls", 0),
        "bowlerName": m.get("bowlerStriker", {}).get("name", "—"), "bowlerOvers": m.get("bowlerStriker", {}).get("overs", "0.0"), "bowlerRuns": m.get("bowlerStriker", {}).get("runs", 0), "bowlerWickets": m.get("bowlerStriker", {}).get("wickets", 0),
        "partnershipRuns": m.get("partnerShip", {}).get("runs", 0), "partnershipBalls": m.get("partnerShip", {}).get("balls", 0), "recentOvs": m.get("recentOvsStats", ""),
        
        "playing11_A": playing11_A, "playing11_B": playing11_B,
        "battingCard_inn1": batting_card_inn1, "bowlingCard_inn1": bowling_card_inn1, "fow_inn1": fow_inn1,
        "battingCard_inn2": batting_card_inn2, "bowlingCard_inn2": bowling_card_inn2, "fow_inn2": fow_inn2
    }
    return data

start_time = time.time()
MAX_DURATION = 6 * 60 * 60
last_url = ""

while time.time() - start_time < MAX_DURATION:
    try:
        config_res = requests.get(f"{FIREBASE_URL}/auto_fetch_config.json", timeout=10)
        config_data = config_res.json()
        if not config_data or 'url' not in config_data:
            time.sleep(15); continue
            
        current_url = config_data['url']
        if current_url != last_url: last_url = current_url
            
        match_id_search = re.search(r'/live-cricket-scores/(\d+)/', current_url)
        MATCH_ID = match_id_search.group(1) if match_id_search else "unknown"
        
        data = fetch_match_smart(current_url)
        if data:
            requests.put(f"{FIREBASE_URL}/current_match_auto.json", json=data, timeout=10)
            print(f"Data Pushed: {data['teamA']} vs {data['teamB']} | Score: {data['score']}/{data['wickets']}")
    except Exception as e: print("Loop Error:", e)
    time.sleep(15)
