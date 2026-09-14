import requests, json, os, re, time
from datetime import datetime

FIREBASE_URL = os.environ.get('FIREBASE_DB_URL', '')

def get_next_data(url):
    # Cricbuzz ki website se direct master JSON nikalne ka sabse tagda tarika
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        res = requests.get(url, headers=headers, timeout=15)
        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', res.text, re.DOTALL)
        if match:
            raw_json = match.group(1)
            data = json.loads(raw_json)
            return data.get("props", {}).get("pageProps", {}).get("page", {})
    except Exception as e:
        print("Fetch Error:", e)
    return {}

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
    # 1. Live Data Fetch
    live_data = get_next_data(match_url)
    m = live_data.get("miniscore", {})
    h = live_data.get("matchHeader", {})
    
    # Fallback agar match complete ho gaya ho aur link redirect ho raha ho
    if not m or not h:
        sc_url = match_url.replace('/live-cricket-scores/', '/live-cricket-scorecard/')
        live_data = get_next_data(sc_url)
        m = live_data.get("miniscore", {})
        h = live_data.get("matchHeader", {})
        
    if not h: 
        print("Match Header missing, checking API failsafe...")
        return None

    playing11_A = get_playing_11(h, "team1")
    playing11_B = get_playing_11(h, "team2")

    batting_card_inn1, bowling_card_inn1, fow_inn1, part_inn1 = [], [], [], []
    batting_card_inn2, bowling_card_inn2, fow_inn2, part_inn2 = [], [], [], []
    
    # 2. Scorecard Fetch
    sc_url = match_url.replace('/live-cricket-scores/', '/live-cricket-scorecard/')
    sc_data = get_next_data(sc_url)
    full_sc = sc_data.get("scoreCard", [])
    
    if isinstance(full_sc, list):
        for idx, inn in enumerate(full_sc):
            bat_card, bowl_card, fow_list, past_parts = [], [], [], []
            
            bat_details = inn.get("batTeamDetails", {}).get("batsmenData", {})
            for key, b in bat_details.items():
                out_desc = str(b.get("outDesc", "")).strip()
                is_out = bool(out_desc and out_desc.lower() not in ['not out', 'batting'])
                bat_card.append({"name": b.get("batName", "TBA"), "runs": int(b.get("runs", 0)), "balls": int(b.get("balls", 0)), "fours": int(b.get("fours", 0)), "sixes": int(b.get("sixes", 0)), "outDesc": out_desc, "isOut": is_out})
            
            bowl_details = inn.get("bowlTeamDetails", {}).get("bowlersData", {})
            for key, bw in bowl_details.items():
                bowl_card.append({"name": bw.get("bowlName", "TBA"), "overs": float(bw.get("overs", 0)), "maidens": int(bw.get("maidens", 0)), "runs": int(bw.get("runs", 0)), "wickets": int(bw.get("wickets", 0))})
            
            fow_details = inn.get("fowData", {})
            for i, (key, f) in enumerate(fow_details.items()):
                fow_list.append({"wktNo": i + 1, "score": f.get("score", 0), "overs": str(f.get("overs", "0.0")), "batterName": f.get("batName", "Unknown")})
                    
            parts_details = inn.get("partnershipsData", {})
            for key, p in parts_details.items():
                past_parts.append({"wktNo": p.get("wicketNum", 0), "bat1Name": p.get("bat1Name", ""), "bat1Runs": p.get("bat1Runs", 0), "bat2Name": p.get("bat2Name", ""), "bat2Runs": p.get("bat2Runs", 0), "totalRuns": p.get("totalRuns", 0), "totalBalls": p.get("totalBalls", 0)})
            
            if idx == 0: batting_card_inn1, bowling_card_inn1, fow_inn1, part_inn1 = bat_card, bowl_card, fow_list, past_parts
            elif idx == 1: batting_card_inn2, bowling_card_inn2, fow_inn2, part_inn2 = bat_card, bowl_card, fow_list, past_parts

    match_state = str(h.get("state", ""))
    is_complete = match_state == "Complete" or h.get("complete", False)
    
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
        "battingCard_inn1": batting_card_inn1, "bowlingCard_inn1": bowling_card_inn1, "fow_inn1": fow_inn1, "pastParts_inn1": part_inn1,
        "battingCard_inn2": batting_card_inn2, "bowlingCard_inn2": bowling_card_inn2, "fow_inn2": fow_inn2, "pastParts_inn2": part_inn2
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
            print(f"Pushed: {data['teamA']} vs {data['teamB']} | SquadA: {len(data['playing11_A'])} | Bat1: {len(data['battingCard_inn1'])}")
        else:
            print("No Match Data Found!")
    except Exception as e: print("Loop Error:", e)
    time.sleep(15)
