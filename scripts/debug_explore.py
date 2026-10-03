import requests, json

# Same match, three different tabs
URLS = {
    "SCORECARD": "https://www.cricbuzz.com/live-cricket-scorecard/151554/ind-vs-wi-3rd-odi-west-indies-tour-of-india-2026",
    "SQUADS":    "https://www.cricbuzz.com/cricket-match-squads/151554/ind-vs-wi-3rd-odi-west-indies-tour-of-india-2026",
    "OVERS":     "https://www.cricbuzz.com/live-cricket-over-by-over/151554/ind-vs-wi-3rd-odi-west-indies-tour-of-india-2026",
    "GRAPH_WORM":     "https://www.cricbuzz.com/live-cricket-graphs/151554/ind-vs-wi-3rd-odi-west-indies-tour-of-india-2026?graph=worm",
    "GRAPH_OVERS":    "https://www.cricbuzz.com/live-cricket-graphs/151554/ind-vs-wi-3rd-odi-west-indies-tour-of-india-2026?graph=overs",
    "GRAPH_RUNRATE":  "https://www.cricbuzz.com/live-cricket-graphs/151554/ind-vs-wi-3rd-odi-west-indies-tour-of-india-2026?graph=run_rate",
    "GRAPH_PARTNERSHIPS": "https://www.cricbuzz.com/live-cricket-graphs/151554/ind-vs-wi-3rd-odi-west-indies-tour-of-india-2026?graph=partnerships",
    "GRAPH_BALLMAP":  "https://www.cricbuzz.com/live-cricket-graphs/151554/ind-vs-wi-3rd-odi-west-indies-tour-of-india-2026?graph=ball_map",
    "GRAPH_WINPROB":  "https://www.cricbuzz.com/live-cricket-graphs/151554/ind-vs-wi-3rd-odi-west-indies-tour-of-india-2026?graph=win_probability",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.cricbuzz.com/",
}

def extract_json_at(html, key):
    for pattern in (f'"{key}":', f'\\"{key}\\":'):
        idx = html.find(pattern)
        if idx == -1:
            continue
        start = idx + len(pattern)
        while start < len(html) and html[start] in ' \t\r\n':
            start += 1
        if start >= len(html) or html[start] not in '{[':
            continue
        snippet = html[start:start + 500000]
        if pattern.startswith('\\"'):
            snippet = snippet.replace('\\"', '"').replace('\\\\', '\\').replace('\\/', '/')
        try:
            obj, _ = json.JSONDecoder().raw_decode(snippet)
            return obj
        except Exception:
            continue
    return None

def summarize(obj, prefix="  ", depth=0, max_depth=2):
    if depth > max_depth or obj is None:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict):
                print(f"{prefix}{k}: dict -> keys={list(v.keys())[:10]}")
                summarize(v, prefix + "    ", depth + 1, max_depth)
            elif isinstance(v, list):
                first_keys = list(v[0].keys())[:10] if v and isinstance(v[0], dict) else (v[0] if v else None)
                print(f"{prefix}{k}: list (len={len(v)}) -> first item: {first_keys}")
            else:
                print(f"{prefix}{k}: {type(v).__name__} = {str(v)[:60]}")
    elif isinstance(obj, list):
        print(f"{prefix}(list, len={len(obj)})")
        if obj and isinstance(obj[0], dict):
            summarize(obj[0], prefix + "    ", depth + 1, max_depth)

KEYWORDS = [
    "fowData", "fallOfWickets", "wicketsData", "wicketFall",
    "playingXI", "playing11", "squadDetails", "squadData", "playersList",
    "overSeparator", "overSummary", "ballByBall", "overNbr", "overNum",
    "commentaryList", "recentBalls", "thisOver",
]

for label, url in URLS.items():
    print(f"\n\n========================= {label} =========================")
    print(url)
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        html = res.text
        print(f"HTTP {res.status_code} | length {len(html)}")

        h = extract_json_at(html, "matchHeader")
        sc = extract_json_at(html, "scoreCard")
        m = extract_json_at(html, "miniscore")

        print("\n-- matchHeader --")
        if h:
            print("keys:", list(h.keys()))
            if h.get("matchTeamInfo") is not None:
                print("\n-- matchHeader.matchTeamInfo (likely holds Playing XI) --")
                summarize({"matchTeamInfo": h["matchTeamInfo"]}, max_depth=3)
        else:
            print("NOT FOUND on this page")

        # Previous attempt searched for `"captain":` but the real text has escaped
        # quotes (\"captain\":), so it never matched. Use a bare word instead —
        # that matches regardless of escaping — anchored on a name we know is in
        # this page's squad list.
        for anchor in ["Rohit Sharma", "captain"]:
            apos = html.find(anchor)
            if apos != -1:
                print(f"\n-- context BEFORE first '{anchor}' (to find the enclosing key/array name) --")
                print(html[max(0, apos - 700):apos + 150])
                break
        else:
            print("\n-- could not find 'Rohit Sharma' or 'captain' anywhere on this page --")

        print("\n-- broader keyword scan for squad / overs / ball-by-ball candidates --")
        for kw in ["squadDetails", "squadPlayers", "playing11", "squads", "team1Players",
                   "matchSquad", "commentaryList", "commentaryAllList", "ballByBall",
                   "overSep", "oversList", "timeline", "miniBall", "commEvents",
                   "commText", "shortText", "inningsId"]:
            cnt = html.count(kw)
            if cnt > 0:
                print(f"  '{kw}': found {cnt} times  <-- LOOK AT THIS ONE")
                p = html.find(kw)
                print(f"     context: ...{html[max(0,p-120):p+250]}...")

        # Last resort for pages where nothing above matched: dump a couple of raw
        # chunks so we can eyeball the structure directly.
        if label == "OVERS":
            print("\n-- raw chunk @ 30000-31500 (manual inspection) --")
            print(html[30000:31500])
            print("\n-- raw chunk @ 150000-151500 (manual inspection) --")
            print(html[150000:151500])

        print("\n-- scoreCard --")
        if sc:
            print(f"type={type(sc).__name__}")
            if isinstance(sc, list) and sc:
                print("innings[0] keys:", list(sc[0].keys()) if isinstance(sc[0], dict) else sc[0])
                summarize(sc[0], max_depth=1)
        else:
            print("NOT FOUND on this page")

        print("\n-- miniscore --")
        print("keys:", list(m.keys())) if m else print("NOT FOUND on this page")

        print("\n-- keyword scan (how many times each name appears in raw HTML) --")
        for kw in KEYWORDS:
            cnt = html.count(kw)
            if cnt > 0:
                print(f"  '{kw}': found {cnt} times  <-- LOOK AT THIS ONE")
    except Exception as e:
        print("ERROR fetching this page:", e)

print("\n\nDONE. Copy this ENTIRE output and send it back.")
