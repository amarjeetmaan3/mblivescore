import requests, json, os, re, time
from datetime import datetime

FIREBASE_URL = os.environ.get('FIREBASE_DB_URL', '')

def extract_json_data(html, key):
    idx = html.find(f'\\"{key}\\":')
    if idx != -1:
        start = idx + len(f'\\"{key}\\":')
    else:
        idx = html.find(f'"{key}":')
        if idx != -1:
            start = idx + len(f'"{key}":')
        else:
            return None
            
    while start < len(html) and html[start] not in ['{', '[']:
        start += 1
        
    if start >= len(html): return None
    open_char = html[start]
    close_char = '}' if open_char == '{' else ']'
    
    depth = 0
    for i in range(start, len(html)):
        if html[i] == open_char: depth += 1
        elif html[i] == close_char:
            depth -= 1
            if depth == 0:
                try:
                    raw = html[start:i+1]
                    if '\\"' in raw: raw = raw.replace('\\"', '"').replace('\\\\', '\\')
                    return json.loads(raw)
                except:
                    return None
    return None

def get_playing_11(match_header, team_key):
    team_data = match_header.get(team_key, {})
    p_ids = team_data.get("playingXI", [])
    if not p_ids:
        p_ids = team_data.get("squad", [])
        
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

    for pid in p_ids:
        names.append(find_name(pid))
        
    while len(names) < 11:
        names.append(f"TBA {len(names)+1}")
    return names[:11]

def fetch_match_smart(match_url):
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(match_url, headers=headers, timeout=15)
    html = res.text
    
    m = extract_json_data(html, "miniscore")
    h = extract_json_data(html, "matchHeader")
    
    if not m or not h:
        sc_url = match_url.replace('/live-cricket-scores/', '/live-cricket-scorecard/')
        res_sc = requests.get(sc_url, headers=headers, timeout=15)
        html = res_sc.text
        m = extract_json_data(html, "miniscore")
        h = extract_json_data(html, "matchHeader")
        
    if not m or not h: return None, True

    playing11_A = get_playing_11(h, "team1")
    playing11_B = get_playing_11(h, "team2")

    batting_card_inn1 = []
    batting_card_inn2 = []
    try:
        sc_url = match_url.replace('/live-cricket-scores/', '/live-cricket-scorecard/')
        res_sc_full = requests.get(sc_url, headers=headers, timeout=15)
        full_sc = extract_json_data(res_sc_full.text, "scoreCard")
        
        if full_sc and isinstance(full_sc, list):
            for idx, inn in enumerate(full_sc):
                bat_card = []
                if "batTeamDetails" in inn and "batsmenData" in inn["batTeamDetails"]:
                    batsmen = inn["batTeamDetails"]["batsmenData"]
                    for key, b in batsmen.items():
                        out_desc = b.get("outDesc", "")
                        is_out = out_desc.lower() not in ['not out', 'batting', '']
                        bat_card.append({
                            "name": b.get("batName", "TBA"),
                            "runs": int(b.get("runs", 0)),
                            "balls": int(b.get("balls", 0)),
                            "fours": int(b.get("fours", 0)),
                            "sixes": int(b.get("sixes", 0)),
                            "outDesc": out_desc,
                            "isOut": is_out
                        })
                if idx == 0: batting_card_inn1 = bat_card
                elif idx == 1: batting_card_inn2 = bat_card
    except Exception as e:
        print("Scorecard Error:", e)

    data = {
        "teamA": h.get("team1", {}).get("shortName", "TBA"),
        "teamB": h.get("team2", {}).get("shortName", "TBB"),
        "score": m.get("batTeam", {}).get("teamScore", 0),
        "wickets": m.get("batTeam", {}).get("teamWkts", 0),
        "overs": m.get("overs", "0.0"),
        "target": m.get("target", 0),
        "playing11_A": playing11_A,
        "playing11_B": playing11_B,
        "battingCard_inn1": batting_card_inn1,
        "battingCard_inn2": batting_card_inn2
    }
    
    is_complete = h.get("state", "") == "Complete" or h.get("complete", False) or True
    return data, is_complete

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
        
        data, is_complete = fetch_match_smart(current_url)
        if data:
            requests.put(f"{FIREBASE_URL}/current_match_auto.json", json=data, timeout=10)
            print(f"Data Pushed: {data['teamA']} vs {data['teamB']} | BatCard 1: {len(data['battingCard_inn1'])} players")
    except Exception as e:
        print("Loop Error:", e)
    time.sleep(15)
