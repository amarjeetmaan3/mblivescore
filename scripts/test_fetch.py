import requests, json, os, re, time
from datetime import datetime

FIREBASE_URL = os.environ.get('FIREBASE_DB_URL', '')

def extract_json_data(html, key):
    marker = f'\\"{key}\\":'
    idx = html.find(marker)
    if idx == -1: return None
    
    start = idx + len(marker)
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
                    raw = html[start:i+1].replace('\\"', '"').replace('\\\\', '\\')
                    return json.loads(raw)
                except:
                    return None
    return None

def fetch_match_smart(match_url):
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(match_url, headers=headers, timeout=15)
    html = res.text
    
    m = extract_json_data(html, "miniscore")
    h = extract_json_data(html, "matchHeader")
    
    if not m or not h:
        scorecard_url = match_url.replace('/live-cricket-scores/', '/live-cricket-scorecard/')
        res_sc = requests.get(scorecard_url, headers=headers, timeout=15)
        html = res_sc.text
        m = extract_json_data(html, "miniscore")
        h = extract_json_data(html, "matchHeader")
        
    if not m or not h: 
        return None, True

    data = {
        "teamA": h.get("team1", {}).get("shortName", "TBA"),
        "teamB": h.get("team2", {}).get("shortName", "TBB"),
        "score": m.get("batTeam", {}).get("teamScore", 0),
        "wickets": m.get("batTeam", {}).get("teamWkts", 0),
        "overs": m.get("overs", "0.0"),
        "status": m.get("status", "Match Completed"),
        "target": m.get("target", 0),
        "crr": m.get("currentRunRate", "0.00"),
        
        "strikerName": m.get("batsmanStriker", {}).get("name", "—"),
        "strikerRuns": m.get("batsmanStriker", {}).get("runs", 0),
        "strikerBalls": m.get("batsmanStriker", {}).get("balls", 0),
        "nonStrikerName": m.get("batsmanNonStriker", {}).get("name", "—"),
        "nonStrikerRuns": m.get("batsmanNonStriker", {}).get("runs", 0),
        "nonStrikerBalls": m.get("batsmanNonStriker", {}).get("balls", 0),
        
        "bowlerName": m.get("bowlerStriker", {}).get("name", "—"),
        "bowlerOvers": m.get("bowlerStriker", {}).get("overs", "0.0"),
        "bowlerRuns": m.get("bowlerStriker", {}).get("runs", 0),
        "bowlerWickets": m.get("bowlerStriker", {}).get("wickets", 0),
        
        "partnershipRuns": m.get("partnerShip", {}).get("runs", 0),
        "partnershipBalls": m.get("partnerShip", {}).get("balls", 0),
        "recentOvs": m.get("recentOvsStats", "")
    }
    
    # --- नया: Full Scorecard फेच करना ---
    try:
        sc_url = match_url.replace('/live-cricket-scores/', '/live-cricket-scorecard/')
        res_sc_full = requests.get(sc_url, headers=headers, timeout=15)
        full_sc_data = extract_json_data(res_sc_full.text, "scoreCard")
        if full_sc_data:
            data["fullScorecard"] = full_sc_data
    except Exception as e:
        print("Scorecard fetch error:", e)
    # -----------------------------------

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
            time.sleep(15)
            continue
            
        current_url = config_data['url']
        
        if current_url != last_url:
            last_url = current_url
            
        match_id_search = re.search(r'/live-cricket-scores/(\d+)/', current_url)
        MATCH_ID = match_id_search.group(1) if match_id_search else "unknown"
        
        data, is_complete = fetch_match_smart(current_url)
        
        if data:
            requests.put(f"{FIREBASE_URL}/current_match_auto.json", json=data, timeout=10)
            requests.put(f"{FIREBASE_URL}/auto_match_history/{MATCH_ID}.json", json=data, timeout=10)
            print(f"{datetime.now()}: {data['teamA']} {data['score']}/{data['wickets']} (Scorecard Appended)")
            
    except Exception as e:
        print("Loop Error:", e)
        
    time.sleep(15)
