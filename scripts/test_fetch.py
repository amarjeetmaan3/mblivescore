import requests, json, os, re, time
from datetime import datetime

FIREBASE_URL = os.environ.get('FIREBASE_DB_URL', '')

# 1. Firebase से क्रिकबज का इकलौता लिंक निकालना
try:
    config_res = requests.get(f"{FIREBASE_URL}/auto_fetch_config.json", timeout=10)
    config_data = config_res.json()
    if not config_data or 'url' not in config_data:
        print("Error: Firebase mein URL nahi mila.")
        exit()
    MATCH_URL = config_data['url']
    print(f"Target Match: {MATCH_URL}")
except Exception as e:
    print("Error fetching config:", e)
    exit()

match_id_search = re.search(r'/live-cricket-scores/(\d+)/', MATCH_URL)
MATCH_ID = match_id_search.group(1) if match_id_search else "unknown"

# यह एडवांस्ड फंक्शन क्रिकबज के कोड से Object {} और Array [] दोनों तरह का डेटा निकाल सकता है
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

def fetch_full_match_data():
    headers = {"User-Agent": "Mozilla/5.0"}
    
    # --- A. LIVE SCORE DATA FETCH ---
    res_live = requests.get(MATCH_URL, headers=headers, timeout=15)
    html_live = res_live.text
    m = extract_json_data(html_live, "miniscore")
    h = extract_json_data(html_live, "matchHeader")
    
    if not m or not h: return None, False
    
    data = {
        "teamA": h.get("team1", {}).get("shortName", "TBA"),
        "teamB": h.get("team2", {}).get("shortName", "TBB"),
        "score": m.get("batTeam", {}).get("teamScore", 0),
        "wickets": m.get("batTeam", {}).get("teamWkts", 0),
        "overs": m.get("overs", "0.0"),
        "status": m.get("status", ""),
        "target": m.get("target", 0),
        "crr": m.get("currentRunRate", "0.00"),
        
        # Current Crease Data
        "strikerName": m.get("batsmanStriker", {}).get("name", "—"),
        "strikerRuns": m.get("batsmanStriker", {}).get("runs", 0),
        "strikerBalls": m.get("batsmanStriker", {}).get("balls", 0),
        "nonStrikerName": m.get("batsmanNonStriker", {}).get("name", "—"),
        "nonStrikerRuns": m.get("batsmanNonStriker", {}).get("runs", 0),
        "nonStrikerBalls": m.get("batsmanNonStriker", {}).get("balls", 0),
        
        # Bowler Data
        "bowlerName": m.get("bowlerStriker", {}).get("name", "—"),
        "bowlerOvers": m.get("bowlerStriker", {}).get("overs", "0.0"),
        "bowlerRuns": m.get("bowlerStriker", {}).get("runs", 0),
        "bowlerWickets": m.get("bowlerStriker", {}).get("wickets", 0),
        
        # Partnership & Recent
        "partnershipRuns": m.get("partnerShip", {}).get("runs", 0),
        "partnershipBalls": m.get("partnerShip", {}).get("balls", 0),
        "recentOvs": m.get("recentOvsStats", ""),
        "lastWicket": m.get("lastWicket", "")
    }

    # --- B. FULL SCORECARD FETCH (Auto-generated URL) ---
    scorecard_url = MATCH_URL.replace('/live-cricket-scores/', '/live-cricket-scorecard/')
    try:
        res_sc = requests.get(scorecard_url, headers=headers, timeout=15)
        sc_data = extract_json_data(res_sc.text, "scoreCard")
        if sc_data: data["fullScorecard"] = sc_data
    except Exception as e:
        print("Scorecard fetch error:", e)

    # --- C. SQUADS / PLAYING XI FETCH (Auto-generated URL) ---
    squads_url = MATCH_URL.replace('/live-cricket-scores/', '/cricket-match-squads/')
    try:
        res_sq = requests.get(squads_url, headers=headers, timeout=15)
        # Playing XI ya Squads ka block nikalna
        team1_squad = extract_json_data(res_sq.text, "team1")
        team2_squad = extract_json_data(res_sq.text, "team2")
        if team1_squad and team2_squad:
            data["squadsData"] = {"team1": team1_squad, "team2": team2_squad}
    except Exception as e:
        print("Squads fetch error:", e)

    is_complete = h.get("state", "") == "Complete" or h.get("complete", False)
    return data, is_complete

def push_to_firebase(data):
    # यह भारी-भरकम JSON सीधा Firebase में जाएगा
    requests.put(f"{FIREBASE_URL}/current_match_auto.json", json=data, timeout=10)
    requests.put(f"{FIREBASE_URL}/auto_match_history/{MATCH_ID}.json", json=data, timeout=10)

start_time = time.time()
MAX_DURATION = 6 * 60 * 60

while time.time() - start_time < MAX_DURATION:
    try:
        data, is_complete = fetch_full_match_data()
        if data:
            push_to_firebase(data)
            print(f"{datetime.now()}: {data['teamA']} {data['score']}/{data['wickets']}")
            if is_complete:
                print("Match completed. Final full data pushed. Exiting.")
                break
        else:
            print("Live Data nahi mila.")
            break
    except Exception as e:
        print("Error:", e)
    time.sleep(15)
