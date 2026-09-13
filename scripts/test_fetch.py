import requests, json, os

MATCH_URL = "https://www.cricbuzz.com/live-cricket-scores/170103/afg-vs-ind-1st-t20i-afghanistan-vs-india-in-india-2026"


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
    if not m:
        return None
    return {
        "teamA": h["team1"]["shortName"] if h else "",
        "teamB": h["team2"]["shortName"] if h else "",
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

data = fetch_and_parse()
print(json.dumps(data, indent=2, ensure_ascii=False))
