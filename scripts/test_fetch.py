import requests, json, os, re, time
from urllib.parse import urlparse

FIREBASE_URL = os.environ.get('FIREBASE_DB_URL', '').rstrip('/')
session = requests.Session()

HEADERS = {
"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
"(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
"image/avif,image/webp,*/*;q=0.8",
}

# ---------------------------------------------------------

# BASIC HELPERS

# ---------------------------------------------------------

def safe_int(value, default=0):
try:
if value is None or value == "":
return default
return int(float(str(value).strip()))
except (ValueError, TypeError):
return default

def safe_str(value, default=""):
if value is None:
return default
return str(value).strip()

def cricket_overs(value):
"""
Cricket overs must stay as cricket notation.
Example:
4.3 = 4 overs + 3 balls
It must NOT be converted to normal decimal math.
"""
if value is None or value == "":
return "0.0"

```
text = str(value).strip()

try:
    if "." in text:
        overs, balls = text.split(".", 1)
        balls = re.sub(r"\D", "", balls) or "0"
        balls = min(safe_int(balls), 5)
        return f"{safe_int(overs)}.{balls}"

    return f"{safe_int(text)}.0"
except Exception:
    return text
```

def legal_balls_from_overs(value):
text = cricket_overs(value)

```
try:
    overs, balls = text.split(".")
    return safe_int(overs) * 6 + safe_int(balls)
except Exception:
    return 0
```

def extract_match_id(url):
"""
Cricbuzz score URLs normally contain the match ID in the URL path.

```
Example:
/live-cricket-scorecard/152009/...
-> 152009
"""
if not url:
    return ""

match = re.search(
    r"/(?:live-cricket-scorecard|live-cricket-scores|"
    r"cricket-scorecard|cricket-scores)/(\d+)(?:/|$)",
    url,
    re.IGNORECASE
)

if match:
    return match.group(1)

# Generic numeric fallback from path.
try:
    path = urlparse(url).path
    numbers = re.findall(r"/(\d+)(?:/|$)", path)
    if numbers:
        return numbers[0]
except Exception:
    pass

return ""
```

def normalise_cricbuzz_url(url):
if not url:
return ""

```
url = url.strip()

if not url.startswith(("http://", "https://")):
    url = "https://" + url

return url
```

def make_live_url(match_url):
match_url = normalise_cricbuzz_url(match_url)

```
return (
    match_url
    .replace("/live-cricket-scorecard/", "/live-cricket-scores/")
    .replace("/cricket-scorecard/", "/cricket-scores/")
)
```

def make_scorecard_url(match_url):
match_url = normalise_cricbuzz_url(match_url)

```
return (
    match_url
    .replace("/live-cricket-scores/", "/live-cricket-scorecard/")
    .replace("/cricket-scores/", "/cricket-scorecard/")
)
```

# ---------------------------------------------------------

# JSON EXTRACTION

# ---------------------------------------------------------

def extract_raw_json(html, key):
"""
Safer extraction of an object/array following:
"key":
Handles nested objects/arrays and quoted strings without
incorrectly stopping on braces inside strings.
"""

```
if not html:
    return None

patterns = [
    f'"{key}":',
    f'\\"{key}\\":'
]

idx = -1

for pattern in patterns:
    idx = html.find(pattern)
    if idx != -1:
        break

if idx == -1:
    return None

start = html.find(":", idx)

if start == -1:
    return None

start += 1

while start < len(html) and html[start].isspace():
    start += 1

# Sometimes escaped JSON has whitespace/characters before
# the actual object.
while start < len(html) and html[start] not in ['{', '[']:
    start += 1

if start >= len(html):
    return None

open_char = html[start]

if open_char == "{":
    close_char = "}"
elif open_char == "[":
    close_char = "]"
else:
    return None

depth = 0
in_string = False
escaped = False

for i in range(start, len(html)):
    char = html[i]

    if in_string:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            in_string = False

        continue

    if char == '"':
        in_string = True
        continue

    if char == open_char:
        depth += 1

    elif char == close_char:
        depth -= 1

        if depth == 0:
            raw = html[start:i + 1]

            # Handle escaped JSON when necessary.
            candidates = [
                raw,
                raw.replace('\\"', '"').replace("\\\\", "\\")
            ]

            for candidate in candidates:
                try:
                    return json.loads(candidate)
                except (ValueError, TypeError):
                    continue

            return None

return None
```

# ---------------------------------------------------------

# CRICBUZZ FETCH

# ---------------------------------------------------------

def get_cricbuzz_data(url):
url = normalise_cricbuzz_url(url)

```
if not url:
    return {}, ""

try:
    response = session.get(
        url,
        headers=HEADERS,
        timeout=15
    )

    response.raise_for_status()

    html = response.text

    if not html:
        print(f"[FETCH] Empty response: {url}", flush=True)
        return {}, ""

    # First try the Next.js page data.
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>'
        r'(.*?)'
        r'</script>',
        html,
        re.DOTALL | re.IGNORECASE
    )

    if match:
        try:
            next_data = json.loads(match.group(1))

            page = (
                next_data
                .get("props", {})
                .get("pageProps", {})
                .get("page", {})
            )

            if isinstance(page, dict) and "matchHeader" in page:
                return page, html

        except (ValueError, TypeError) as e:
            print(
                f"[FETCH] __NEXT_DATA__ parse failed: {e}",
                flush=True
            )

    # Fallback extraction.
    miniscore = extract_raw_json(html, "miniscore")
    match_header = extract_raw_json(html, "matchHeader")
    score_card = extract_raw_json(html, "scoreCard")

    data = {
        "miniscore": miniscore if isinstance(miniscore, dict) else {},
        "matchHeader": (
            match_header if isinstance(match_header, dict) else {}
        ),
        "scoreCard": score_card if isinstance(score_card, list) else []
    }

    return data, html

except requests.RequestException as e:
    print(f"[FETCH] HTTP error: {e}", flush=True)
    return {}, ""

except Exception as e:
    print(f"[FETCH] Unexpected error: {type(e).__name__}: {e}", flush=True)
    return {}, ""
```

# ---------------------------------------------------------

# PLAYER HELPERS

# ---------------------------------------------------------

def get_players_meta(match_header):
players = match_header.get("players", [])

```
if isinstance(players, dict):
    players = list(players.values())

if not isinstance(players, list):
    return []

return [
    p for p in players
    if isinstance(p, dict)
]
```

def player_name_from_meta(players_meta, player_id):
for player in players_meta:
if str(player.get("id", "")) == str(player_id):
return (
safe_str(player.get("name"))
or safe_str(player.get("fullName"))
or safe_str(player.get("shortName"))
)

```
return ""
```

def normalise_player_names(value, players_meta):
"""
Converts Cricbuzz player representations into names.

```
Supports:
- comma-separated names
- player ID lists
- player dictionaries
- strings
"""

names = []

if isinstance(value, str):
    for name in value.split(","):
        name = name.strip()
        if name:
            names.append(name)

elif isinstance(value, list):
    for item in value:
        if isinstance(item, dict):
            name = (
                safe_str(item.get("name"))
                or safe_str(item.get("fullName"))
                or safe_str(item.get("shortName"))
            )

            if not name:
                name = player_name_from_meta(
                    players_meta,
                    item.get("id")
                )

            if name:
                names.append(name)

        else:
            name = player_name_from_meta(
                players_meta,
                item
            )

            if name:
                names.append(name)

elif isinstance(value, dict):
    for item in value.values():
        if isinstance(item, dict):
            name = (
                safe_str(item.get("name"))
                or safe_str(item.get("fullName"))
                or safe_str(item.get("shortName"))
            )

            if name:
                names.append(name)

# Preserve Cricbuzz order, remove duplicates.
result = []
seen = set()

for name in names:
    key = name.casefold()

    if key not in seen:
        seen.add(key)
        result.append(name)

return result
```

# ---------------------------------------------------------

# SQUAD / PLAYING XI

# ---------------------------------------------------------

def get_playing_11(match_header, team_key):
"""
ONLY returns Playing XI.

```
IMPORTANT:
We deliberately do NOT fall back to Squad.
If Cricbuzz does not expose XI data, return [].
"""

team_data = match_header.get(team_key, {})

if not isinstance(team_data, dict):
    return []

players_meta = get_players_meta(match_header)

playing_xi = team_data.get("playingXI")

names = normalise_player_names(
    playing_xi,
    players_meta
)

return names
```

def get_squad(match_header, team_key):
"""
Returns Squad separately from Playing XI.
"""

```
team_data = match_header.get(team_key, {})

if not isinstance(team_data, dict):
    return []

players_meta = get_players_meta(match_header)

squad = team_data.get("squad")

return normalise_player_names(
    squad,
    players_meta
)
```

# ---------------------------------------------------------

# FORMAT / OVERS

# ---------------------------------------------------------

def get_max_overs(match_header, miniscore, scorecard):
"""
Read actual match format/max overs from Cricbuzz data.

```
Priority:
1. Explicit max overs fields
2. Format string
3. Match description/title
4. Scorecard innings declared max overs
"""

possible_values = []

for source in [match_header, miniscore]:
    if isinstance(source, dict):
        for key in [
            "maxOvers",
            "maxOver",
            "maxOversPerInnings",
            "oversPerInnings",
            "totalOvers"
        ]:
            if source.get(key) not in (None, ""):
                possible_values.append(source.get(key))

for value in possible_values:
    number = safe_int(value, 0)
    if number > 0:
        return number

format_values = []

for source in [match_header, miniscore]:
    if not isinstance(source, dict):
        continue

    for key in [
        "matchFormat",
        "format",
        "matchType",
        "type",
        "description",
        "title"
    ]:
        value = source.get(key)

        if value not in (None, ""):
            format_values.append(str(value))

# Search all visible format/title strings.
combined = " ".join(format_values).upper()

if re.search(r"\bT20\b|\bT-20\b|\b20\s*OVERS?\b", combined):
    return 20

if re.search(r"\bT10\b|\bT-10\b|\b10\s*OVERS?\b", combined):
    return 10

if re.search(r"\bT5\b|\bT-5\b|\b5\s*OVERS?\b", combined):
    return 5

if re.search(r"\bODI\b|\b50\s*OVERS?\b", combined):
    return 50

if re.search(r"\bTHE\s*HUNDRED\b|\b100\s*BALLS?\b", combined):
    # The Hundred is ball based, not an overs competition.
    return 100

# Look at scorecard innings max overs if present.
if isinstance(scorecard, list):
    for innings in scorecard:
        if not isinstance(innings, dict):
            continue

        for container in [
            innings,
            innings.get("batTeamDetails", {}),
            innings.get("scoreDetails", {})
        ]:
            if not isinstance(container, dict):
                continue

            for key in [
                "maxOvers",
                "maxOver",
                "oversLimit",
                "totalOvers"
            ]:
                value = container.get(key)

                if value not in (None, ""):
                    number = safe_int(value, 0)

                    if number > 0:
                        return number

# Unknown format: do not invent T20.
return 0
```

def get_format_name(match_header, miniscore):
values = []

```
for source in [match_header, miniscore]:
    if not isinstance(source, dict):
        continue

    for key in [
        "matchFormat",
        "format",
        "matchType",
        "type"
    ]:
        value = source.get(key)

        if value not in (None, ""):
            values.append(str(value).strip())

if values:
    return values[0]

combined = " ".join(values).upper()

if "T20" in combined:
    return "T20"

if "ODI" in combined:
    return "ODI"

return ""
```

# ---------------------------------------------------------

# INNINGS PARSING

# ---------------------------------------------------------

def parse_batting_card(innings):
result = []

```
details = innings.get("batTeamDetails", {})

if not isinstance(details, dict):
    return result

batsmen = details.get("batsmenData", {})

if isinstance(batsmen, list):
    items = enumerate(batsmen)
elif isinstance(batsmen, dict):
    items = batsmen.items()
else:
    return result

# Dict insertion order is retained, matching Cricbuzz order.
for _, batter in items:
    if not isinstance(batter, dict):
        continue

    name = (
        safe_str(batter.get("batName"))
        or safe_str(batter.get("name"))
    )

    if not name:
        continue

    out_desc = safe_str(batter.get("outDesc"))

    lower_out = out_desc.casefold()

    is_out = bool(
        out_desc
        and lower_out not in {
            "not out",
            "batting",
            "retired not out",
            "retired hurt"
        }
    )

    result.append({
        "name": name,
        "runs": safe_int(batter.get("runs")),
        "balls": safe_int(batter.get("balls")),
        "fours": safe_int(batter.get("fours")),
        "sixes": safe_int(batter.get("sixes")),
        "outDesc": out_desc,
        "isOut": is_out
    })

return result
```

def parse_bowling_card(innings):
result = []

```
details = innings.get("bowlTeamDetails", {})

if not isinstance(details, dict):
    return result

bowlers = details.get("bowlersData", {})

if isinstance(bowlers, list):
    items = enumerate(bowlers)
elif isinstance(bowlers, dict):
    items = bowlers.items()
else:
    return result

for _, bowler in items:
    if not isinstance(bowler, dict):
        continue

    name = (
        safe_str(bowler.get("bowlName"))
        or safe_str(bowler.get("name"))
    )

    if not name:
        continue

    overs_value = (
        bowler.get("overs")
        if bowler.get("overs") is not None
        else bowler.get("over")
    )

    result.append({
        "name": name,

        # Keep cricket notation, e.g. 4.3.
        "overs": cricket_overs(overs_value),

        # Extra normalized field for calculations.
        "legalBalls": legal_balls_from_overs(overs_value),

        "maidens": safe_int(bowler.get("maidens")),
        "runs": safe_int(bowler.get("runs")),
        "wickets": safe_int(bowler.get("wickets"))
    })

return result
```

def parse_fow(innings):
result = []

```
fow_data = innings.get("fowData", {})

if isinstance(fow_data, list):
    items = enumerate(fow_data)
elif isinstance(fow_data, dict):
    items = fow_data.items()
else:
    return result

counter = 1

for _, fow in items:
    if not isinstance(fow, dict):
        continue

    wicket_no = safe_int(
        fow.get("wicketNum"),
        counter
    )

    result.append({
        "wktNo": wicket_no,
        "score": safe_int(fow.get("score")),
        "overs": cricket_overs(fow.get("overs")),
        "batterName": (
            safe_str(fow.get("batName"))
            or safe_str(fow.get("batterName"))
            or "Unknown"
        )
    })

    counter += 1

return result
```

def parse_partnerships(innings):
result = []

```
partnerships = innings.get("partnershipsData", {})

if isinstance(partnerships, list):
    items = enumerate(partnerships)
elif isinstance(partnerships, dict):
    items = partnerships.items()
else:
    return result

for _, partnership in items:
    if not isinstance(partnership, dict):
        continue

    result.append({
        "wktNo": safe_int(partnership.get("wicketNum")),
        "bat1Name": safe_str(partnership.get("bat1Name")),
        "bat1Runs": safe_int(partnership.get("bat1Runs")),
        "bat2Name": safe_str(partnership.get("bat2Name")),
        "bat2Runs": safe_int(partnership.get("bat2Runs")),
        "totalRuns": safe_int(partnership.get("totalRuns")),
        "totalBalls": safe_int(partnership.get("totalBalls"))
    })

return result
```

def parse_extras(innings):
extras = innings.get("extrasData", {})

```
if not isinstance(extras, dict):
    return 0

return safe_int(
    extras.get("total")
)
```

# ---------------------------------------------------------

# INNINGS TEAM IDENTIFICATION

# ---------------------------------------------------------

def get_innings_team(innings, team_a_id, team_b_id):
details = innings.get("batTeamDetails", {})

```
if not isinstance(details, dict):
    return ""

bat_team_id = str(
    details.get("batTeamId", "")
).strip()

if bat_team_id and bat_team_id == team_a_id:
    return "A"

if bat_team_id and bat_team_id == team_b_id:
    return "B"

return ""
```

# ---------------------------------------------------------

# CURRENT LIVE DATA

# ---------------------------------------------------------

def get_current_live_values(miniscore, team_a_id, team_b_id):
bat_team = miniscore.get("batTeam", {})

```
if not isinstance(bat_team, dict):
    bat_team = {}

batting_team_id = str(
    bat_team.get("teamId", "")
).strip()

if batting_team_id == team_a_id:
    batting_team = "A"
elif batting_team_id == team_b_id:
    batting_team = "B"
else:
    batting_team = ""

striker = miniscore.get("batsmanStriker", {})
non_striker = miniscore.get("batsmanNonStriker", {})
bowler = miniscore.get("bowlerStriker", {})
partnership = miniscore.get("partnerShip", {})

if not isinstance(striker, dict):
    striker = {}

if not isinstance(non_striker, dict):
    non_striker = {}

if not isinstance(bowler, dict):
    bowler = {}

if not isinstance(partnership, dict):
    partnership = {}

return {
    "battingTeam": batting_team,

    "score": safe_int(bat_team.get("teamScore")),
    "wickets": safe_int(bat_team.get("teamWkts")),

    "overs": cricket_overs(
        miniscore.get("overs")
    ),

    "target": safe_int(
        miniscore.get("target")
    ),

    "crr": safe_str(
        miniscore.get("currentRunRate"),
        "0.00"
    ),

    "strikerName": (
        safe_str(striker.get("name"))
        or "—"
    ),
    "strikerRuns": safe_int(
        striker.get("runs")
    ),
    "strikerBalls": safe_int(
        striker.get("balls")
    ),

    "nonStrikerName": (
        safe_str(non_striker.get("name"))
        or "—"
    ),
    "nonStrikerRuns": safe_int(
        non_striker.get("runs")
    ),
    "nonStrikerBalls": safe_int(
        non_striker.get("balls")
    ),

    "bowlerName": (
        safe_str(bowler.get("name"))
        or "—"
    ),
    "bowlerOvers": cricket_overs(
        bowler.get("overs")
    ),
    "bowlerRuns": safe_int(
        bowler.get("runs")
    ),
    "bowlerWickets": safe_int(
        bowler.get("wickets")
    ),

    "currPartnershipRuns": safe_int(
        partnership.get("runs")
    ),
    "currPartnershipBalls": safe_int(
        partnership.get("balls")
    ),

    "recentOvs": miniscore.get(
        "recentOvsStats",
        ""
    )
}
```

# ---------------------------------------------------------

# MAIN MATCH FETCH

# ---------------------------------------------------------

def fetch_match_smart(match_url):
match_url = normalise_cricbuzz_url(match_url)

```
if not match_url:
    print("[MATCH] Empty match URL", flush=True)
    return None

match_id = extract_match_id(match_url)

if not match_id:
    print(
        f"[MATCH] Could not extract match ID: {match_url}",
        flush=True
    )
    return None

live_url = make_live_url(match_url)
scorecard_url = make_scorecard_url(match_url)

print(
    f"[MATCH] Fetching ID {match_id}",
    flush=True
)

# Live page: current score/striker/bowler.
live_data, _ = get_cricbuzz_data(live_url)

# Scorecard: batting/bowling/FOW/partnerships.
scorecard_data, _ = get_cricbuzz_data(scorecard_url)

live_miniscore = (
    live_data.get("miniscore", {})
    if isinstance(live_data, dict)
    else {}
)

live_header = (
    live_data.get("matchHeader", {})
    if isinstance(live_data, dict)
    else {}
)

scorecard_miniscore = (
    scorecard_data.get("miniscore", {})
    if isinstance(scorecard_data, dict)
    else {}
)

scorecard_header = (
    scorecard_data.get("matchHeader", {})
    if isinstance(scorecard_data, dict)
    else {}
)

full_scorecard = (
    scorecard_data.get("scoreCard", [])
    if isinstance(scorecard_data, dict)
    else []
)

if not isinstance(full_scorecard, list):
    full_scorecard = []

# Live page is preferred for current values.
# Scorecard header is fallback only.
h = live_header if live_header else scorecard_header

if not isinstance(h, dict) or not h:
    print(
        f"[MATCH] No matchHeader found for {match_id}",
        flush=True
    )
    return None

m = (
    live_miniscore
    if isinstance(live_miniscore, dict) and live_miniscore
    else scorecard_miniscore
)

if not isinstance(m, dict):
    m = {}

team1 = h.get("team1", {})
team2 = h.get("team2", {})

if not isinstance(team1, dict):
    team1 = {}

if not isinstance(team2, dict):
    team2 = {}

# -----------------------------------------------------
# PERMANENT A/B IDENTITY
# -----------------------------------------------------

team_a_id = safe_str(team1.get("id"))
team_b_id = safe_str(team2.get("id"))

team_a_short = (
    safe_str(team1.get("shortName"))
    or safe_str(team1.get("name"))
    or "TBA"
)

team_b_short = (
    safe_str(team2.get("shortName"))
    or safe_str(team2.get("name"))
    or "TBB"
)

team_a_name = (
    safe_str(team1.get("name"))
    or team_a_short
)

team_b_name = (
    safe_str(team2.get("name"))
    or team_b_short
)

# -----------------------------------------------------
# INNINGS 1 TEAM
# -----------------------------------------------------

bat_team_inn1 = ""

if full_scorecard:
    bat_team_inn1 = get_innings_team(
        full_scorecard[0],
        team_a_id,
        team_b_id
    )

if not bat_team_inn1:
    live_batting_team = get_current_live_values(
        m,
        team_a_id,
        team_b_id
    ).get("battingTeam", "")

    bat_team_inn1 = live_batting_team

# Do NOT guess A if Cricbuzz has not told us.
# Empty is safer than wrong identity.

# -----------------------------------------------------
# PLAYING XI / SQUAD
# -----------------------------------------------------

playing11_A = get_playing_11(
    h,
    "team1"
)

playing11_B = get_playing_11(
    h,
    "team2"
)

squad_A = get_squad(
    h,
    "team1"
)

squad_B = get_squad(
    h,
    "team2"
)

# -----------------------------------------------------
# SCORECARD
# -----------------------------------------------------

batting_card_inn1 = []
bowling_card_inn1 = []
fow_inn1 = []
part_inn1 = []
extras_inn1 = 0

batting_card_inn2 = []
bowling_card_inn2 = []
fow_inn2 = []
part_inn2 = []
extras_inn2 = 0

innings_data = []

for idx, innings in enumerate(full_scorecard):
    if not isinstance(innings, dict):
        continue

    innings_team = get_innings_team(
        innings,
        team_a_id,
        team_b_id
    )

    bat_card = parse_batting_card(
        innings
    )

    bowl_card = parse_bowling_card(
        innings
    )

    fow_list = parse_fow(
        innings
    )

    partnerships = parse_partnerships(
        innings
    )

    extras_val = parse_extras(
        innings
    )

    innings_data.append({
        "inningsNo": idx + 1,
        "battingTeam": innings_team,
        "batTeamId": safe_str(
            innings.get("batTeamDetails", {}).get(
                "batTeamId"
            )
        ),
        "battingCard": bat_card,
        "bowlingCard": bowl_card,
        "fow": fow_list,
        "partnerships": partnerships,
        "extras": extras_val
    })

    if idx == 0:
        batting_card_inn1 = bat_card
        bowling_card_inn1 = bowl_card
        fow_inn1 = fow_list
        part_inn1 = partnerships
        extras_inn1 = extras_val

    elif idx == 1:
        batting_card_inn2 = bat_card
        bowling_card_inn2 = bowl_card
        fow_inn2 = fow_list
        part_inn2 = partnerships
        extras_inn2 = extras_val

# -----------------------------------------------------
# CURRENT LIVE VALUES
# -----------------------------------------------------

current = get_current_live_values(
    m,
    team_a_id,
    team_b_id
)

# -----------------------------------------------------
# FORMAT
# -----------------------------------------------------

format_name = get_format_name(
    h,
    m
)

max_overs = get_max_overs(
    h,
    m,
    full_scorecard
)

# -----------------------------------------------------
# MATCH STATUS
# -----------------------------------------------------

state = safe_str(
    h.get("state")
)

status = (
    safe_str(h.get("status"))
    or safe_str(h.get("statusText"))
)

complete_value = h.get("complete", False)

is_complete = (
    state.casefold() in {
        "complete",
        "completed",
        "finished"
    }
    or complete_value is True
)

# -----------------------------------------------------
# FINAL NORMALIZED DATA
# -----------------------------------------------------

data = {
    # Match identity
    "matchId": match_id,

    # Permanent Cricbuzz team order
    "teamA": team_a_short,
    "teamA_name": team_a_name,
    "teamA_id": team_a_id,

    "teamB": team_b_short,
    "teamB_name": team_b_name,
    "teamB_id": team_b_id,

    # Format
    "format": format_name,
    "maxOvers": max_overs,

    # Innings 1 identity
    "bat_team_inn1": bat_team_inn1,

    # Current live score
    "battingTeam": current["battingTeam"],
    "score": current["score"],
    "wickets": current["wickets"],
    "overs": current["overs"],
    "target": current["target"],
    "crr": current["crr"],

    # Status
    "matchStatus": status,
    "matchState": state,
    "isComplete": is_complete,

    # Current players
    "strikerName": current["strikerName"],
    "strikerRuns": current["strikerRuns"],
    "strikerBalls": current["strikerBalls"],

    "nonStrikerName": current["nonStrikerName"],
    "nonStrikerRuns": current["nonStrikerRuns"],
    "nonStrikerBalls": current["nonStrikerBalls"],

    "bowlerName": current["bowlerName"],
    "bowlerOvers": current["bowlerOvers"],
    "bowlerRuns": current["bowlerRuns"],
    "bowlerWickets": current["bowlerWickets"],

    # Current partnership
    "currPartnershipRuns": current[
        "currPartnershipRuns"
    ],
    "currPartnershipBalls": current[
        "currPartnershipBalls"
    ],

    "recentOvs": current["recentOvs"],

    # Separate Playing XI
    "playing11_A": playing11_A,
    "playing11_B": playing11_B,

    # Separate squads
    "squad_A": squad_A,
    "squad_B": squad_B,

    # Innings 1
    "extras_inn1": extras_inn1,
    "battingCard_inn1": batting_card_inn1,
    "bowlingCard_inn1": bowling_card_inn1,
    "fow_inn1": fow_inn1,
    "pastParts_inn1": part_inn1,

    # Innings 2
    "extras_inn2": extras_inn2,
    "battingCard_inn2": batting_card_inn2,
    "bowlingCard_inn2": bowling_card_inn2,
    "fow_inn2": fow_inn2,
    "pastParts_inn2": part_inn2,

    # Complete normalized innings list
    "innings": innings_data
}

print(
    f"[MATCH] {team_a_name} vs {team_b_name} | "
    f"ID={match_id} | Format={format_name or 'unknown'} | "
    f"MaxOvers={max_overs or 'unknown'} | "
    f"Batting={current['battingTeam'] or 'unknown'} | "
    f"Score={current['score']}/{current['wickets']} "
    f"({current['overs']})",
    flush=True
)

print(
    f"[MATCH] XI: A={len(playing11_A)} "
    f"B={len(playing11_B)} | "
    f"Squad: A={len(squad_A)} "
    f"B={len(squad_B)} | "
    f"Innings={len(innings_data)}",
    flush=True
)

return data
```

# ---------------------------------------------------------

# FIREBASE LOOP

# ---------------------------------------------------------

def firebase_get(path):
response = session.get(
f"{FIREBASE_URL}/{path}.json",
timeout=10
)

```
response.raise_for_status()

return response.json()
```

def firebase_put(path, data):
response = session.put(
f"{FIREBASE_URL}/{path}.json",
json=data,
timeout=10
)

```
response.raise_for_status()
```

if not FIREBASE_URL:
print(
"[START] FIREBASE_DB_URL is not set.",
flush=True
)
else:
start_time = time.time()
last_url = ""

```
print(
    "MB Live Score Fetcher Started...",
    flush=True
)

while time.time() - start_time < 6 * 60 * 60:

    try:
        config_data = firebase_get(
            "auto_fetch_config"
        )

        if not isinstance(config_data, dict):
            time.sleep(4)
            continue

        current_url = normalise_cricbuzz_url(
            config_data.get("url", "")
        )

        if not current_url:
            time.sleep(4)
            continue

        if current_url != last_url:
            last_url = current_url

            print(
                f"[CONFIG] New Match URL: {current_url}",
                flush=True
            )

        data = fetch_match_smart(
            current_url
        )

        if data:
            # IMPORTANT:
            # Do not delete old data before a successful fetch.
            # A temporary Cricbuzz/network failure therefore
            # does not destroy the last good live data.
            firebase_put(
                "current_match_auto",
                data
            )

            print(
                f"[FIREBASE] Updated current_match_auto | "
                f"{data['teamA']} vs {data['teamB']} | "
                f"{data['score']}/{data['wickets']} "
                f"({data['overs']})",
                flush=True
            )

    except requests.RequestException as e:
        print(
            f"[LOOP] Firebase/HTTP error: {e}",
            flush=True
        )

    except Exception as e:
        print(
            f"[LOOP] Unexpected error: "
            f"{type(e).__name__}: {e}",
            flush=True
        )

    time.sleep(4)
```
