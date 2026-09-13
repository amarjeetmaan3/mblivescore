import requests, json, os, re, time
from datetime import datetime

FIREBASE_URL = os.environ['FIREBASE_DB_URL']

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

def extract_json_block(html, key):
    marker = f'\\"{key}\\":{{'
    idx = html.find(marker)
    if idx == -1: return None
    start = idx + len(marker) - 1
    depth = 0
    for i in range(start, len(html)):
        if html[i] == '{': depth += 1
        elif html[i] == '}':
            depth -= 1
            if depth == 0:
                return json.loads(html[start:i+1].replace('\\"', '"'))
    return None

def fetch_and_parse():
    res = requests.get(MATCH_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    html = res.text
    m = extract_json_block(html, "miniscore")
    h = extract_json_block(html, "matchHeader")
    if not m or not h: return None, None
    
    # Safe parsing using .get() to avoid KeyErrors on completed matches
    data = {
        "teamA": h.get("team1", {}).get("shortName", "TBA"),
        "teamB": h.get("team2", {}).get("shortName", "TBB"),
        "score": m.get("batTeam", {}).get("teamScore", 0),
        "wickets": m.get("batTeam", {}).get("teamWkts", 0),
        "overs": m.get("overs", "0.0"),
        "status": m.get("status", ""),
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
        "target": m.get("target", 0),
        "crr": m.get("currentRunRate", "0.00"),
    }
    is_complete = h.get("state", "") == "Complete" or h.get("complete", False)
    return data, is_complete

def push_to_firebase(data):
    requests.put(f"{FIREBASE_URL}/current_match_auto.json", json=data, timeout=10)
    requests.put(f"{FIREBASE_URL}/auto_match_history/{MATCH_ID}.json", json=data, timeout=10)

start_time = time.time()
MAX_DURATION = 6 * 60 * 60

while time.time() - start_time < MAX_DURATION:
    try:
        data, is_complete = fetch_and_parse()
        if data:
            push_to_firebase(data)
            print(f"{datetime.now()}: {data['teamA']} {data['score']}/{data['wickets']}")
            if is_complete:
                print("Match completed. Final data pushed. Exiting.")
                break
        else:
            print("Data nahi mila.")
            break
    except Exception as e:
        print("Error:", e)
    time.sleep(15)
