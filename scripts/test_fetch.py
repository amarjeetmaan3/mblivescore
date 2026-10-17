import requests, json, os, re, time, random, html

FIREBASE_URL = os.environ.get('FIREBASE_DB_URL', '')
session = requests.Session()

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

over_hist_state = {}

def track_over_history(innings_id, overs_val, score):
    """Track runs scored in each completed over, not the cumulative innings total.
    The live score is sampled repeatedly, so score deltas are accumulated into the
    over in progress and committed when the completed-over count advances."""
    st = over_hist_state.setdefault(innings_id, {
        "lastOver": None, "lastScore": 0, "currentOverRuns": 0,
        "history": [], "initialized": False
    })
    try:
        over_int = max(0, int(float(overs_val or 0)))
        score_now = max(0, int(score or 0))
    except (TypeError, ValueError):
        return st["history"]

    if not st["initialized"]:
        # Preserve the correct x-axis position if the workflow starts mid-innings.
        st["history"] = [None] * over_int
        st["lastOver"] = over_int
        st["lastScore"] = score_now
        st["currentOverRuns"] = score_now if over_int == 0 else 0
        st["initialized"] = True
        return st["history"]

    delta = max(0, score_now - int(st.get("lastScore", 0) or 0))
    previous_over = st["lastOver"]
    if over_int == previous_over:
        st["currentOverRuns"] += delta
    elif over_int == previous_over + 1:
        # The score delta since the last poll belongs to the over just completed.
        st["currentOverRuns"] += delta
        st["history"].append(st["currentOverRuns"])
        st["currentOverRuns"] = 0
        st["lastOver"] = over_int
    elif over_int > previous_over + 1:
        # We missed multiple over boundaries, so the score delta cannot safely be
        # assigned to individual overs. Keep those bars unknown instead of inventing
        # per-over totals from the innings score.
        st["history"].extend([None] * (over_int - previous_over))
        st["currentOverRuns"] = 0
        st["lastOver"] = over_int
    st["lastScore"] = score_now
    return st["history"]

def push_status(ok, error=None):
    try:
        session.patch(f"{FIREBASE_URL}/current_match_auto.json", json={"fetchStatus": {"ok": ok, "error": error or "", "ts": int(time.time())}}, timeout=5)
    except Exception: pass

def extract_json_at(html, key):
    for pattern in (f'"{key}":', f'\\"{key}\\":'):
        idx = html.find(pattern)
        if idx == -1: continue
        start = idx + len(pattern)
        while start < len(html) and html[start] in ' \t\r\n': start += 1
        if start >= len(html) or html[start] not in '{[': continue
        snippet = html[start:start + 400000]
        if pattern.startswith('\\"'): snippet = snippet.replace('\\"', '"').replace('\\\\', '\\').replace('\\/', '/')
        try:
            obj, _ = json.JSONDecoder().raw_decode(snippet)
            return obj
        except Exception: continue
    return None

def get_cricbuzz_data(url, attempts=3):
    last_err = None
    for i in range(attempts):
        headers = { "User-Agent": random.choice(USER_AGENTS), "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.cricbuzz.com/", "Cache-Control": "no-cache" }
        try:
            res = session.get(url, headers=headers, timeout=12)
            if res.status_code in (403, 429):
                last_err = f"HTTP {res.status_code} — Rate limiting"
                time.sleep(3 + i * 3); continue
            if res.status_code != 200:
                last_err = f"HTTP {res.status_code}"
                time.sleep(1.5); continue

            html = res.text
            head_sample = html[:6000].lower()
            if "captcha" in head_sample or "access denied" in head_sample or "unusual traffic" in head_sample:
                last_err = "Bot check page"
                time.sleep(3 + i * 3); continue

            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1)).get("props", {}).get("pageProps", {}).get("page", {})
                    if data and "matchHeader" in data:
                        data["pageText"] = html
                        return data, None
                except Exception as e: last_err = f"Parse err: {e}"

            m, h, sc = extract_json_at(html, "miniscore"), extract_json_at(html, "matchHeader"), extract_json_at(html, "scoreCard")
            if h: return {"miniscore": m or {}, "matchHeader": h or {}, "scoreCard": sc or [], "pageText": html}, None
            last_err = "No match data found"
        except requests.exceptions.RequestException as e:
            last_err = f"Network err: {e}"
            time.sleep(1.5)
    return {}, last_err

def get_playing_11(match_header, team_key):
    """Return only a real 11-player Playing XI from matchHeader, never a partial bowling card."""
    team_data = match_header.get(team_key, {}) or {}
    team_id = str(team_data.get("id", team_data.get("teamId", "")))
    team_name = str(team_data.get("name", team_data.get("teamName", ""))).strip().lower()
    team_index = 0 if team_key == "team1" else 1

    def player_name(item):
        if not isinstance(item, dict):
            return None
        for key in ("fullName", "name", "playerName", "shortName", "batName", "bowlName"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        nested = item.get("player") or item.get("playerDetails")
        if isinstance(nested, dict):
            return player_name(nested)
        return None

    def as_list(value):
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            if player_name(value):
                return [value]
            for key in ("players", "playerDetails", "playingXI", "playing11", "list"):
                if isinstance(value.get(key), list):
                    return value[key]
        if isinstance(value, str) and value.strip():
            return [part.strip() for part in value.split(",") if part.strip()]
        return []

    sources = [team_data]
    team_info = match_header.get("matchTeamInfo", [])
    entries = list(team_info.values()) if isinstance(team_info, dict) else (team_info if isinstance(team_info, list) else [])
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("teamId", entry.get("id", entry.get("teamID", ""))))
        entry_name = str(entry.get("teamName", entry.get("name", ""))).strip().lower()
        if ((team_id and entry_id == team_id) or
                (team_name and entry_name == team_name) or
                (not entry_id and not entry_name and index == team_index)):
            sources.append(entry)

    player_lookup = {}
    def collect_players(obj):
        if isinstance(obj, dict):
            name = player_name(obj)
            pid = obj.get("id", obj.get("playerId", obj.get("playerID")))
            if name and pid is not None:
                player_lookup[str(pid)] = name
            for value in obj.values():
                collect_players(value)
        elif isinstance(obj, list):
            for value in obj:
                collect_players(value)
    collect_players(match_header)

    # Only trust fields explicitly named Playing XI/Playing 11. A general squad
    # may contain 15-20 players, and a bowling card only contains used bowlers.
    for source in sources:
        for key in ("playingXI", "playing11", "playingXi", "playing_xi"):
            names = []
            for item in as_list(source.get(key)):
                name = player_name(item)
                if name:
                    names.append(name)
                elif isinstance(item, (str, int, float)):
                    resolved = player_lookup.get(str(item).strip())
                    text_value = str(item).strip()
                    if resolved:
                        names.append(resolved)
                    elif isinstance(source.get(key), str) and text_value:
                        names.append(text_value)
            unique = []
            seen = set()
            for name in names:
                marker = name.casefold()
                if marker not in seen:
                    seen.add(marker)
                    unique.append(name)
            if len(unique) >= 11:
                return unique[:11]
    return []
def get_match_max_overs(match_header, miniscore, scorecard, page_text=""):
    """Resolve scheduled/revised innings overs, preferring explicit rain-reduced data."""
    def as_positive_int(value):
        if isinstance(value, bool) or value is None:
            return None
        try:
            number = int(value) if isinstance(value, (int, float)) else int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return number if 1 <= number <= 100 else None

    revised_priority = ("revisedovers", "revisedmaxovers", "reducedovers")
    regular_priority = (
        "oversperside", "oversperinnings", "scheduledovers", "matchovers",
        "overslimit", "overlimit", "maxoversperinnings", "maxovers"
    )

    def find_explicit(obj, wanted_key):
        if isinstance(obj, dict):
            for key, value in obj.items():
                normalized = re.sub(r"[^a-z]", "", str(key).lower())
                if normalized == wanted_key:
                    result = as_positive_int(value)
                    if result:
                        return result
            for value in obj.values():
                result = find_explicit(value, wanted_key)
                if result:
                    return result
        elif isinstance(obj, list):
            for value in obj:
                result = find_explicit(value, wanted_key)
                if result:
                    return result
        return None

    sources = (match_header, miniscore, scorecard)
    for source in sources:
        for wanted_key in revised_priority:
            explicit = find_explicit(source, wanted_key)
            if explicit:
                return explicit

    text_fields = []
    for source in (match_header, miniscore):
        if isinstance(source, dict):
            for key in ("status", "stateTitle", "matchDesc", "matchDescription",
                        "matchStatus", "description", "commentary", "note"):
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    text_fields.append(value)
    # Prefer explicit rain/reduction wording over ordinary scorecard references
    # to 20-over/50-over formats elsewhere on the page.
    patterns = (
        r"(?:reduced|shortened|cut\s+down|limited)\s+(?:to\s+)?(\d{1,2})\s*[- ]?overs?\s*(?:per\s+side|each|a\s+side)?",
        # Cricbuzz's live page for rain-shortened matches says e.g. "9 Overs game due to rain".
        r"(\d{1,2})\s*[- ]?overs?\s+game\b",
        r"(\d{1,2})\s*[- ]?overs?\s*(?:per\s+side|each|a\s+side)\b",
        r"(?:innings|each\s+side|per\s+side)\s+(?:of\s+)?(\d{1,2})\s*overs?\b"
    )
    for text in text_fields:
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                result = as_positive_int(match.group(1))
                if result:
                    return result
    page_text = str(page_text or "").replace("\\u002d", "-").replace("\\u003c", "<")
    for pattern in patterns:
        match = re.search(pattern, page_text, re.IGNORECASE)
        if match:
            result = as_positive_int(match.group(1))
            if result:
                return result

    # Generic scheduled limits come after explicit reduction text so a default
    # maxOvers=20 cannot override a rain-reduced "9 overs per side" update.
    for source in sources:
        for wanted_key in regular_priority:
            explicit = find_explicit(source, wanted_key)
            if explicit:
                return explicit

    match_format = str((match_header or {}).get("matchFormat", "")).strip().upper()
    desc_text = f"{(match_header or {}).get('matchDesc', '')} {(match_header or {}).get('matchDescription', '')} {(match_header or {}).get('seriesName', '')}"
    custom_overs = re.search(r"(\d+)\s*-?\s*overs?\b", desc_text, re.IGNORECASE)
    if custom_overs and match_format not in ("ODI", "TEST", "T10"):
        result = as_positive_int(custom_overs.group(1))
        if result:
            return result
    if "ODI" in match_format or "ONE DAY" in match_format:
        return 50
    if "TEST" in match_format:
        return 90
    if "T10" in match_format:
        return 10
    return 20


def fetch_raw_page(url, attempts=1):
    """Fetch a Cricbuzz tab even when its page does not expose matchHeader JSON."""
    last_error = None
    for attempt in range(attempts):
        try:
            response = session.get(
                url,
                headers={
                    "User-Agent": random.choice(USER_AGENTS),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://www.cricbuzz.com/",
                    "Cache-Control": "no-cache",
                },
                timeout=8,
            )
            if response.status_code == 200:
                page = response.text
                sample = page[:6000].lower()
                if not any(marker in sample for marker in ("captcha", "access denied", "unusual traffic")):
                    return page
                last_error = "Bot check page"
            else:
                last_error = f"HTTP {response.status_code}"
        except requests.exceptions.RequestException as exc:
            last_error = f"Network err: {exc}"
        if attempt + 1 < attempts:
            time.sleep(1 + attempt)
    if last_error:
        print(f"Cricbuzz tab fetch failed ({url}): {last_error}", flush=True)
    return ""


def extract_playing_xi_from_html(page_text, team_index):
    """Extract a full Playing XI from the squads page, not just used bowlers."""
    if not page_text:
        return []

    # Triple-quoted regex avoids accidental quote escaping in the profile href matcher.
    anchor_re = re.compile(
        r"""<a\b[^>]*href=["'][^"']*/profiles/\d+/[^"']*["'][^>]*>(.*?)</a>""",
        re.IGNORECASE | re.DOTALL,
    )
    def names_in(section):
        names = []
        for anchor in anchor_re.finditer(section):
            raw_name = re.sub(r"<[^>]+>", " ", anchor.group(1))
            name = html.unescape(re.sub(r"\s+", " ", raw_name)).strip()
            if name and name.casefold() not in {existing.casefold() for existing in names}:
                names.append(name)
        return names

    # Some Cricbuzz layouts use one combined Playing XI section; others use one
    # Playing XI/Bench block per team. Support both structures.
    markers = list(re.finditer(r"\bplaying\s+xi\b", page_text, re.IGNORECASE))
    candidate_lists = []
    for marker in markers:
        next_xi = re.search(r"\bplaying\s+xi\b", page_text[marker.end():], re.IGNORECASE)
        next_bench = re.search(r"\bbench\b", page_text[marker.end():], re.IGNORECASE)
        ends = []
        if next_xi:
            ends.append(marker.end() + next_xi.start())
        if next_bench:
            ends.append(marker.end() + next_bench.start())
        end = min(ends) if ends else len(page_text)
        names = names_in(page_text[marker.end():end])
        if len(names) >= 11:
            candidate_lists.append(names)

    # Deduplicate repeated labels that can occur in embedded page data.
    unique_lists = []
    seen_lists = set()
    for names in candidate_lists:
        key = tuple(name.casefold() for name in names)
        if key not in seen_lists:
            seen_lists.add(key)
            unique_lists.append(names)

    for names in unique_lists:
        if len(names) >= 22:
            return names[team_index * 11:team_index * 11 + 11]
    if len(unique_lists) >= 2:
        names = unique_lists[min(team_index, len(unique_lists) - 1)]
        if len(names) >= 11:
            return names[:11]

    # Fallback: the page may expose the two XIs without a separate heading per team.
    all_names = names_in(page_text)
    if len(all_names) >= 22:
        return all_names[team_index * 11:team_index * 11 + 11]
    if len(all_names) >= 11 and team_index == 0:
        return all_names[:11]
    return []


def fetch_squads_xis(match_url, cache):
    """Fetch/cache the dedicated Cricbuzz squads page as the XI source of truth."""
    now = time.time()
    cached = cache.get("playing11")
    if cached and now - cache.get("playing11_ts", 0) < 300:
        return cached.get("A", []), cached.get("B", [])

    squads_url = re.sub(
        r"/(?:live-cricket-scores|live-cricket-scorecard|cricket-scores|cricket-scorecard|live-cricket-over-by-over|live-cricket-full-commentary|live-cricket-graphs|live-cricket-highlights|cricket-match-facts)/",
        "/cricket-match-squads/",
        match_url,
        count=1,
    )
    page = fetch_raw_page(squads_url)
    if page:
        team_a = extract_playing_xi_from_html(page, 0)
        team_b = extract_playing_xi_from_html(page, 1)
        if len(team_a) == 11 and len(team_b) == 11:
            cache["playing11"] = {"A": team_a, "B": team_b}
            cache["playing11_ts"] = now
            print(f"Playing XI loaded from squads page: {len(team_a)} + {len(team_b)} players", flush=True)
            return team_a, team_b
        print(f"Squads page parsed incomplete XI: {len(team_a)} + {len(team_b)}", flush=True)
    return (cached or {}).get("A", []), (cached or {}).get("B", [])
def extract_over_histories(page_text, team_a, team_b, default_innings=1):
    """Parse per-over run totals from Cricbuzz over summaries and preserve unknown gaps."""
    result = {1: [], 2: []}
    if not page_text:
        return result

    plain = html.unescape(re.sub(r"<[^>]+>", " ", str(page_text)))
    plain = re.sub(r"\s+", " ", plain)
    team_names = {
        str(team_a or "").strip().upper(),
        str(team_b or "").strip().upper(),
    }
    team_names.discard("")
    heading_re = re.compile(
        r"\b(" + "|".join(re.escape(name) for name in sorted(team_names, key=len, reverse=True)) +
        r")\s*\((1st|2nd)\s*Inn\)",
        re.IGNORECASE,
    )
    headings = list(heading_re.finditer(plain))

    # Cricbuzz's visible live page uses blocks such as:
    # "Over 4 66-2 ... (10 runs)". These contain the per-over total directly.
    summary_re = re.compile(
        r"\bOver\s*(\d{1,2})\s+(\d{1,3})\s*[-–]\s*(\d{1,2})\b.{0,260}?\((\d{1,2})\s+runs?\)",
        re.IGNORECASE,
    )
    # Also accept cumulative-only summaries seen on some older Cricbuzz layouts.
    cumulative_re = re.compile(
        r"\b(?:Ov|Over)\s*(\d{1,2})\s+(\d{1,3})\s*[-–]\s*(\d{1,2})\b",
        re.IGNORECASE,
    )

    found = {1: {}, 2: {}}
    if headings:
        sections = []
        for index, heading in enumerate(headings):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(plain)
            inn_no = 1 if heading.group(2).lower().startswith("1") else 2
            sections.append((inn_no, plain[heading.end():end]))
        for inn_no, section in sections:
            summaries = list(summary_re.finditer(section))
            if summaries:
                for match in summaries:
                    found[inn_no][int(match.group(1))] = int(match.group(4))
            else:
                cumulative = {}
                for match in cumulative_re.finditer(section):
                    cumulative[int(match.group(1))] = int(match.group(2))
                previous_over, previous_total = 0, 0
                for over_no in sorted(cumulative):
                    total = cumulative[over_no]
                    if over_no == previous_over + 1:
                        found[inn_no][over_no] = max(0, total - previous_total)
                    previous_over, previous_total = over_no, total

    # Commentary/over tabs often omit the "(1st Inn)" heading in the text layer.
    # In that case assign visible summaries to the current innings from miniscore.
    if not any(found.values()):
        summaries = list(summary_re.finditer(plain))
        if summaries:
            inn_no = 2 if str(default_innings) == "2" else 1
            for match in summaries:
                found[inn_no][int(match.group(1))] = int(match.group(4))
        else:
            cumulative = {}
            for match in cumulative_re.finditer(plain):
                cumulative[int(match.group(1))] = int(match.group(2))
            inn_no = 2 if str(default_innings) == "2" else 1
            previous_over, previous_total = 0, 0
            for over_no in sorted(cumulative):
                total = cumulative[over_no]
                if over_no == previous_over + 1:
                    found[inn_no][over_no] = max(0, total - previous_total)
                previous_over, previous_total = over_no, total

    for inn_no, values in found.items():
        if not values:
            continue
        history = [None] * max(values)
        for over_no, runs in values.items():
            if 1 <= over_no <= len(history):
                history[over_no - 1] = runs
        result[inn_no] = history
    return result
def fetch_match_smart(match_url, sc_cache):
    live_url = match_url.replace('/live-cricket-scorecard/', '/live-cricket-scores/').replace('/cricket-scorecard/', '/cricket-scores/')
    sc_url = match_url.replace('/live-cricket-scores/', '/live-cricket-scorecard/').replace('/cricket-scores/', '/cricket-scorecard/')

    live_data, live_err = get_cricbuzz_data(live_url)
    m, h = live_data.get("miniscore", {}), live_data.get("matchHeader", {})

    now = time.time()
    sc_err = None
    if now - sc_cache.get('ts', 0) > 20 or not sc_cache.get('data'):
        sc_data, sc_err = get_cricbuzz_data(sc_url)
        if sc_data.get("matchHeader") or sc_data.get("scoreCard"):
            sc_cache['data'], sc_cache['ts'] = sc_data, now
            
    sc_data = sc_cache.get('data', {}) or {}

    # Fetch Cricbuzz's separate tabs too. Several tabs (especially Overs and
    # Full Commentary) do not expose matchHeader JSON, so fetch their HTML directly.
    if now - sc_cache.get("tabs_ts", 0) > 45 or not sc_cache.get("tabs"):
        def tab_url(slug):
            return re.sub(
                r"/(?:live-cricket-scores|live-cricket-scorecard|cricket-scores|cricket-scorecard|live-cricket-over-by-over|live-cricket-full-commentary|live-cricket-graphs|live-cricket-highlights|cricket-match-facts|cricket-match-squads)/",
                f"/{slug}/",
                match_url,
                count=1,
            )
        tabs = {
            "overs": "live-cricket-over-by-over",
            "squads": "cricket-match-squads",
            "info": "cricket-match-facts",
            "commentary": "live-cricket-full-commentary",
            "graphs": "live-cricket-graphs",
            "highlights": "live-cricket-highlights",
        }
        sc_cache["tabs"] = {
            key: fetch_raw_page(tab_url(slug))
            for key, slug in tabs.items()
        }
        sc_cache["tabs_ts"] = now
    tab_pages = sc_cache.get("tabs", {}) or {}
    full_sc = sc_data.get("scoreCard", [])

    if not h: m, h = sc_data.get("miniscore", {}), sc_data.get("matchHeader", {})
    # Fill gaps field-by-field from the scorecard page's matchHeader (e.g. matchFormat
    # sometimes only appears on one of the two pages) instead of an all-or-nothing swap.
    sc_h = sc_data.get("matchHeader", {}) or {}
    for k, v in sc_h.items():
        if not h.get(k) and v:
            h[k] = v
    combined_err = live_err or sc_err
    if not h: return None, combined_err or "no data"

    t1_id, t2_id = str(h.get("team1", {}).get("id", "")), str(h.get("team2", {}).get("id", ""))

    toss_res = h.get("tossResults", {})
    toss_winner = "A" if str(toss_res.get("tossWinnerId", "")) == t1_id else ("B" if str(toss_res.get("tossWinnerId", "")) == t2_id else "A")
    max_overs_text = " ".join([
        live_data.get("pageText", ""),
        sc_data.get("pageText", ""),
        tab_pages.get("overs", ""),
        tab_pages.get("info", ""),
        tab_pages.get("commentary", ""),
        tab_pages.get("graphs", ""),
        tab_pages.get("highlights", ""),
    ])
    max_overs = get_match_max_overs(h, m, full_sc, max_overs_text)

    bat_team_inn1 = "A"
    if isinstance(full_sc, list) and len(full_sc) > 0:
        if str(full_sc[0].get("batTeamDetails", {}).get("batTeamId", "")) == t2_id: bat_team_inn1 = "B"
    elif m.get("batTeam", {}).get("teamId"):
        bat_team_inn1 = "B" if str(m.get("batTeam", {}).get("teamId", "")) == t2_id else "A"

    playing11_A, playing11_B = get_playing_11(h, "team1"), get_playing_11(h, "team2")
    # Header data can omit the not-yet-used players; fetch the dedicated squads
    # page if either XI is incomplete instead of letting bowling figures fill it.
    if len(playing11_A) < 11 or len(playing11_B) < 11:
        squads_A, squads_B = fetch_squads_xis(match_url, sc_cache)
        if len(squads_A) == 11:
            playing11_A = squads_A
        if len(squads_B) == 11:
            playing11_B = squads_B

    curr_innings_id = m.get("inningsId", 1) or 1
    track_over_history(curr_innings_id, m.get("overs", 0), m.get("batTeam", {}).get("teamScore", 0))
    page_histories = {1: [], 2: []}
    history_pages = [
        live_data.get("pageText", ""),
        sc_data.get("pageText", ""),
        tab_pages.get("overs", ""),
        tab_pages.get("commentary", ""),
    ]
    for history_page in history_pages:
        parsed = extract_over_histories(
            history_page,
            h.get("team1", {}).get("shortName", ""),
            h.get("team2", {}).get("shortName", ""),
            default_innings=curr_innings_id,
        )
        for innings_number in (1, 2):
            existing = page_histories[innings_number]
            incoming = parsed.get(innings_number, [])
            if incoming and any(value is not None for value in incoming):
                merged = []
                for idx in range(max(len(existing), len(incoming))):
                    old_value = existing[idx] if idx < len(existing) else None
                    new_value = incoming[idx] if idx < len(incoming) else None
                    merged.append(new_value if new_value is not None else old_value)
                page_histories[innings_number] = merged
    for innings_number in (1, 2):
        parsed_history = page_histories.get(innings_number, [])
        tracked = over_hist_state.setdefault(innings_number, {"history": [], "currentOverRuns": 0})
        tracked_history = tracked.get("history", [])
        if parsed_history and any(value is not None for value in parsed_history):
            merged_history = []
            for idx in range(max(len(parsed_history), len(tracked_history))):
                parsed_value = parsed_history[idx] if idx < len(parsed_history) else None
                tracked_value = tracked_history[idx] if idx < len(tracked_history) else None
                merged_history.append(parsed_value if parsed_value is not None else tracked_value)
            tracked["history"] = merged_history
            page_histories[innings_number] = merged_history
        elif tracked_history:
            page_histories[innings_number] = tracked_history
    current_innings_history = page_histories.get(int(curr_innings_id) if str(curr_innings_id).isdigit() else 1, [])
    try:
        completed_overs = int(float(m.get("overs", 0) or 0))
        current_score = int(m.get("batTeam", {}).get("teamScore", 0) or 0)
        known_completed = current_innings_history[:completed_overs]
        if len(known_completed) == completed_overs and all(value is not None for value in known_completed):
            over_hist_state.setdefault(curr_innings_id, {})["currentOverRuns"] = max(0, current_score - sum(known_completed))
    except (TypeError, ValueError):
        pass
    
    batting_card_inn1, bowling_card_inn1, fow_inn1, part_inn1 = [], [], [], []
    batting_card_inn2, bowling_card_inn2, fow_inn2, part_inn2 = [], [], [], []
    inn1_details = {"score": 0, "wickets": 0, "overs": "0.0", "extras": {}, "extrasString": ""}
    inn2_details = {"score": 0, "wickets": 0, "overs": "0.0", "extras": {}, "extrasString": ""}

    if isinstance(full_sc, list):
        for idx, inn in enumerate(full_sc):
            bat_card, bowl_card, fow_list, past_parts = [], [], [], []
            
            # PERFECT BATTING CARD & DISMISSALS
            for key, b in inn.get("batTeamDetails", {}).get("batsmenData", {}).items():
                out_desc = str(b.get("outDesc", "")).strip()
                bat_card.append({
                    "name": b.get("batName", "TBA"), "runs": int(b.get("runs", 0)), "balls": int(b.get("balls", 0)), 
                    "fours": int(b.get("fours", 0)), "sixes": int(b.get("sixes", 0)), "outDesc": out_desc, "dismissalInfo": out_desc,
                    "isOut": bool(out_desc and out_desc.lower() not in ['not out', 'batting', 'retired hurt'])
                })
                
            # PERFECT BOWLING CARD
            for key, bw in inn.get("bowlTeamDetails", {}).get("bowlersData", {}).items():
                bowl_card.append({
                    "name": bw.get("bowlName", "TBA"), "overs": float(bw.get("overs", 0)), "maidens": int(bw.get("maidens", 0)), 
                    "runs": int(bw.get("runs", 0)), "wickets": int(bw.get("wickets", 0)), "wides": int(bw.get("wides", 0)),
                    "noBalls": int(bw.get("no_balls", 0)), "economy": str(bw.get("economy", "0.0"))
                })
                
            # FOW — confirmed real structure: inn["wicketsData"] = {"wkt_1": {...}, "wkt_2": {...}, ...}
            # with fields batId, batName, wktOver, wktRuns, ballNbr, wktNbr.
            for key, f in inn.get("wicketsData", {}).items():
                if not isinstance(f, dict): continue
                fow_list.append({"wktNo": f.get("wktNbr", 0), "score": f.get("wktRuns", 0), "overs": str(f.get("wktOver", "0.0")), "batterName": f.get("batName", "Unknown")})
            fow_list.sort(key=lambda x: x["wktNo"])

            # ALL PARTNERSHIPS — confirmed field names: bat1Name/bat1Runs/bat2Name/bat2Runs/
            # totalRuns/totalBalls (no per-batsman ball counts are provided by Cricbuzz here).
            for key, p in inn.get("partnershipsData", {}).items():
                past_parts.append({
                    "wktNo": p.get("wktNbr", 0),
                    "p1Name": p.get("bat1Name", ""), "p1Runs": p.get("bat1Runs", 0), "p1Balls": 0,
                    "p2Name": p.get("bat2Name", ""), "p2Runs": p.get("bat2Runs", 0), "p2Balls": 0,
                    "runs": p.get("totalRuns", 0), "balls": p.get("totalBalls", 0)
                })
            # Cricbuzz includes the still-unbroken partnership as an extra entry, which
            # shifts every wicket number up by one. Number of *completed* partnerships
            # always equals number of fallen wickets — drop the extra one if present.
            if len(past_parts) == len(fow_list) + 1:
                past_parts = past_parts[:-1]

            # TOTAL & EXTRAS FOR BOTTOM STRIP
            score_det = inn.get("scoreDetails", {})
            extras_det = inn.get("extrasData", {})
            ex_total = extras_det.get("total", 0)
            ex_b = extras_det.get("byes", 0)
            ex_lb = extras_det.get("legByes", 0)
            ex_w = extras_det.get("wides", 0)
            ex_nb = extras_det.get("noBalls", 0)
            ex_p = extras_det.get("penalty", 0)
            
            details = {
                "score": score_det.get("runs", 0), "wickets": score_det.get("wickets", 0), "overs": str(score_det.get("overs", "0.0")),
                "extras": {"total": ex_total, "byes": ex_b, "legByes": ex_lb, "wides": ex_w, "noBalls": ex_nb, "penalty": ex_p},
                "extrasString": f"{ex_total} (b {ex_b}, lb {ex_lb}, w {ex_w}, nb {ex_nb}, p {ex_p})"
            }

            if idx == 0: 
                batting_card_inn1, bowling_card_inn1, fow_inn1, part_inn1 = bat_card, bowl_card, fow_list, past_parts
                inn1_details = details
            elif idx == 1: 
                batting_card_inn2, bowling_card_inn2, fow_inn2, part_inn2 = bat_card, bowl_card, fow_list, past_parts
                inn2_details = details

    data = {
        "teamA": h.get("team1", {}).get("shortName", "TBA"), "teamA_name": h.get("team1", {}).get("name", "Team A"),
        "teamB": h.get("team2", {}).get("shortName", "TBB"), "teamB_name": h.get("team2", {}).get("name", "Team B"),
        "bat_team_inn1": bat_team_inn1, "maxOvers": max_overs, "tossWinner": toss_winner, "tossDecision": str(toss_res.get("decision", "BAT")).upper(),
        "score": m.get("batTeam", {}).get("teamScore", 0), "wickets": m.get("batTeam", {}).get("teamWkts", 0),
        "overs": m.get("overs", "0.0"), "target": m.get("target", 0), "crr": m.get("currentRunRate", "0.00"),
        "matchStatus": h.get("status", ""), "isComplete": str(h.get("state", "")) == "Complete" or h.get("complete", False),
        "strikerName": m.get("batsmanStriker", {}).get("name", "—"), "strikerRuns": m.get("batsmanStriker", {}).get("runs", 0), "strikerBalls": m.get("batsmanStriker", {}).get("balls", 0),
        "nonStrikerName": m.get("batsmanNonStriker", {}).get("name", "—"), "nonStrikerRuns": m.get("batsmanNonStriker", {}).get("runs", 0), "nonStrikerBalls": m.get("batsmanNonStriker", {}).get("balls", 0),
        "bowlerName": m.get("bowlerStriker", {}).get("name", "—"), "bowlerOvers": m.get("bowlerStriker", {}).get("overs", "0.0"), "bowlerRuns": m.get("bowlerStriker", {}).get("runs", 0), "bowlerWickets": m.get("bowlerStriker", {}).get("wickets", 0),
        "currPartnershipRuns": m.get("partnerShip", {}).get("runs", 0), "currPartnershipBalls": m.get("partnerShip", {}).get("balls", 0), "recentOvs": m.get("recentOvsStats", ""),
        "playing11_A": playing11_A, "playing11_B": playing11_B, 
        
        "inn1_details": inn1_details, "inn2_details": inn2_details,
        "battingCard_inn1": batting_card_inn1, "bowlingCard_inn1": bowling_card_inn1, "fow_inn1": fow_inn1, "pastParts_inn1": part_inn1,
        "battingCard_inn2": batting_card_inn2, "bowlingCard_inn2": bowling_card_inn2, "fow_inn2": fow_inn2, "pastParts_inn2": part_inn2,
        "overHistory_inn1": list(page_histories.get(1) or over_hist_state.get(1, {}).get("history", [])),
        "overHistory_inn2": list(page_histories.get(2) or over_hist_state.get(2, {}).get("history", [])),
        "currentOverRuns": int(over_hist_state.get(curr_innings_id, {}).get("currentOverRuns", 0) or 0)
    }
    return data, combined_err

start_time = time.time()
last_url = ""
sc_cache = {}
print("Cricbuzz fetcher started (Full 1st Innings Data Extraction)...", flush=True)

while time.time() - start_time < 6 * 60 * 60:
    try:
        config_data = session.get(f"{FIREBASE_URL}/auto_fetch_config.json", timeout=10).json()
        if not config_data or 'url' not in config_data:
            if os.environ.get('CRICBUZZ_ONCE') == '1':
                print('Smoke test stopped: no auto_fetch_config URL is configured.', flush=True)
                break
            time.sleep(4); continue
        current_url = config_data['url']
        if current_url != last_url:
            last_url = current_url; sc_cache = {}; over_hist_state.clear(); session.delete(f"{FIREBASE_URL}/current_match_auto.json")
            print(f"New Link: {current_url}", flush=True)
        data, err = fetch_match_smart(current_url, sc_cache)
        if data:
            data["fetchStatus"] = {"ok": True, "error": err or "", "ts": int(time.time())}
            session.put(f"{FIREBASE_URL}/current_match_auto.json", json=data, timeout=5)
            print(
                f"Pushed: {data['teamA']} vs {data['teamB']} | "
                f"Score: {data['score']}/{data['wickets']} ({data.get('overs', '0.0')} ov) | "
                f"maxOvers={data.get('maxOvers')} | "
                f"PlayingXI={len(data.get('playing11_A', []))}+{len(data.get('playing11_B', []))}"
                + (f" | warn: {err}" if err else ""),
                flush=True
            )
        else:
            push_status(False, err)
    except Exception as e: push_status(False, f"script error: {e}")
    if os.environ.get('CRICBUZZ_ONCE') == '1':
        break
    time.sleep(4)
    
