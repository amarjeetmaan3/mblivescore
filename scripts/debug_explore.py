import requests, json

# Same match, three different tabs
URLS = {
    "SCORECARD": "https://www.cricbuzz.com/live-cricket-scorecard/151554/ind-vs-wi-3rd-odi-west-indies-tour-of-india-2026",
    "SQUADS":    "https://www.cricbuzz.com/cricket-match-squads/151554/ind-vs-wi-3rd-odi-west-indies-tour-of-india-2026",
    "OVERS":     "https://www.cricbuzz.com/live-cricket-over-by-over/151554/ind-vs-wi-3rd-odi-west-indies-tour-of-india-2026",
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

        # Grab raw text around each "playingXI" mention so we can see its exact
        # surrounding key/structure even if it's not nested under matchHeader.
        pxi_positions = [i for i in range(len(html)) if html.startswith("playingXI", i)]
        if pxi_positions:
            print(f"\n-- raw context around 'playingXI' ({len(pxi_positions)} hits, showing first 3) --")
            for p in pxi_positions[:3]:
                snippet = html[max(0, p - 80):p + 400]
                print(f"  ...{snippet}...\n")

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
