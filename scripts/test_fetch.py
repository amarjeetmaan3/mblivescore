import requests, json, os, re, time
from datetime import datetime

MATCH_URL = "https://www.cricbuzz.com/live-cricket-scores/170103/afg-vs-ind-1st-t20i-afghanistan-vs-india-in-india-2026"
FIREBASE_URL = os.environ['FIREBASE_DB_URL']

# URL se match ID nikalna (jaise 170103)
match_id_search = re.search(r'/live-cricket-scores/(\d+)/', MATCH_URL)
MATCH_ID = match_id_search.group(1) if match_id_search else "unknown"

def extract_json_block(html, key):
    marker = f'\\"{key}\\":{{'
    idx = html.find(marker)
    if idx == -1:
        return None
    start = idx + len(marker) - 1
    depth = 0
    for i in range(start, len(html)):
        if html[i] == '{':
            depth += 1
        elif html[i] == '}':
            depth -= 1
            if depth == 0:
                raw = html[start:i+1].replace('\\"', '"')
                return json.loads(raw)
    return None

def fetch_and_parse():
    res = requests.get(MATCH_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    html = res.text
    m = extract_json_block(html, "miniscore")
    h = extract_json_block(html, "matchHeader")
    if not m or not h:
        return None, None
    data = {
        "teamA": h["team1"]["shortName"],
        "teamB": h["team2"]["shortName"],
        "score": m["batTeam"]["teamScore"],
        "wickets": m["batTeam"]["teamWkts"],
        "overs": m.get("overs"),
        "status": m.get("status"),
        "strikerName": m["batsmanStriker"]["name"],
        "strikerRuns": m["batsmanStriker"]["runs"],
        "strikerBalls": m["batsmanStriker"]["balls"],
        "nonStrikerName": m["batsmanNonStriker"]["name"],
        "nonStrikerRuns": m["batsmanNonStriker"]["runs"],
        "nonStrikerBalls": m["batsmanNonStriker"]["balls"],
        "bowlerName": m["bowlerStriker"]["name"],
        "bowlerOvers": m["bowlerStriker"]["overs"],
        "bowlerRuns": m["bowlerStriker"]["runs"],
        "bowlerWickets": m["bowlerStriker"]["wickets"],
        "target": m.get("target"),
        "crr": m.get("currentRunRate"),
    }
    is_complete = h.get("complete", False)
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
            print(f"{datetime.now()}: {data['teamA']} {data['score']}/{data['wickets']} — complete={is_complete}")
            if is_complete:
                print("Match khatam ho gaya, script apne aap ruk rahi hai.")
                break
        else:
            print(f"{datetime.now()}: data nahi mila")
    except Exception as e:
        print("Error:", e)
    time.sleep(15)
