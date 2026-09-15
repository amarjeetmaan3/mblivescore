```python
import os
import re
import json
import time
import logging
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# CONFIG
# ============================================================

FIREBASE_DB_URL = os.environ.get("FIREBASE_DB_URL", "").rstrip("/")

FETCH_INTERVAL = int(os.environ.get("FETCH_INTERVAL", "4"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "15"))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

log = logging.getLogger("MB-Cricbuzz")


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

retry = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
)

adapter = HTTPAdapter(max_retries=retry)

session.mount("https://", adapter)
session.mount("http://", adapter)


# ============================================================
# BASIC HELPERS
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()

    return str(value).strip()


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default

        if isinstance(value, bool):
            return int(value)

        return int(float(str(value).replace(",", "").strip()))
    except Exception:
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def first_non_empty(*values):
    for value in values:
        if value is None:
            continue

        if isinstance(value, str) and not value.strip():
            continue

        if isinstance(value, (list, dict)) and not value:
            continue

        return value

    return None


def normalize_name(name: Any) -> str:
    name = clean_text(name)

    name = re.sub(r"\s*\((?:c|wk|c/wk|vc)\)\s*$", "", name, flags=re.I)

    return re.sub(r"[^a-z0-9]", "", name.lower())


# ============================================================
# CRICKET OVERS
# IMPORTANT:
# 4.3 means 4 overs + 3 legal balls.
# It does NOT mean 4.3 decimal overs.
# ============================================================

def overs_to_balls(value: Any) -> int:
    text = clean_text(value)

    if not text:
        return 0

    try:
        if "." in text:
            over_part, ball_part = text.split(".", 1)

            overs = int(over_part)
            balls = int(ball_part)

            if balls > 5:
                balls = 0

            return overs * 6 + balls

        return int(text) * 6

    except Exception:
        return 0


def balls_to_overs(balls: int) -> str:
    balls = max(0, int(balls))

    return f"{balls // 6}.{balls % 6}"


def normalize_overs(value: Any) -> str:
    text = clean_text(value)

    if not text:
        return "0.0"

    if re.match(r"^\d+\.\d+$", text):
        return text

    try:
        return balls_to_overs(int(float(text) * 6))
    except Exception:
        return "0.0"


# ============================================================
# MATCH ID
# ============================================================

def extract_match_id(url: str) -> str:
    """
    Supports:
      /live-cricket-scores/152009/...
      /live-cricket-scorecard/152009/...
      /cricket-scores/152009/...
      /cricket-scorecard/152009/...
    """

    url = clean_text(url)

    patterns = [
        r"/live-cricket-scores/(\d+)",
        r"/live-cricket-scorecard/(\d+)",
        r"/cricket-scores/(\d+)",
        r"/cricket-scorecard/(\d+)",
        r"/cricket-match-squads/(\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, url, re.I)

        if match:
            return match.group(1)

    # Generic fallback: first long numeric path component
    parsed = urlparse(url)

    for part in parsed.path.split("/"):
        if part.isdigit() and len(part) >= 4:
            return part

    return ""


# ============================================================
# URL NORMALIZATION
# ============================================================

def make_live_url(url: str) -> str:
    url = url.strip()

    replacements = [
        ("/live-cricket-scorecard/", "/live-cricket-scores/"),
        ("/cricket-scorecard/", "/cricket-scores/"),
    ]

    for old, new in replacements:
        url = url.replace(old, new)

    return url


def make_scorecard_url(url: str) -> str:
    url = url.strip()

    replacements = [
        ("/live-cricket-scores/", "/live-cricket-scorecard/"),
        ("/cricket-scores/", "/cricket-scorecard/"),
    ]

    for old, new in replacements:
        url = url.replace(old, new)

    return url


# ============================================================
# SAFE NEXT DATA EXTRACTION
# ============================================================

def extract_next_data(html: str) -> Optional[Dict[str, Any]]:
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    )

    if not match:
        return None

    raw = match.group(1).strip()

    try:
        return json.loads(raw)
    except Exception as exc:
        log.warning("NEXT_DATA JSON error: %s", exc)
        return None


def extract_balanced_json_after_key(
    html: str,
    key: str,
) -> Optional[Any]:

    patterns = [
        f'"{key}"',
        f'\\"{key}\\"',
    ]

    start_index = -1

    for pattern in patterns:
        idx = html.find(pattern)

        if idx != -1:
            start_index = idx
            break

    if start_index == -1:
        return None

    colon = html.find(":", start_index)

    if colon == -1:
        return None

    pos = colon + 1

    while pos < len(html) and html[pos].isspace():
        pos += 1

    if pos >= len(html):
        return None

    opening = html[pos]

    if opening not in "{[":
        return None

    closing = "}" if opening == "{" else "]"

    depth = 0
    in_string = False
    escaped = False

    for i in range(pos, len(html)):

        char = html[i]

        if in_string:

            if escaped:
                escaped = False
                continue

            if char == "\\":
                escaped = True
                continue

            if char == '"':
                in_string = False

            continue

        if char == '"':
            in_string = True
            continue

        if char == opening:
            depth += 1

        elif char == closing:
            depth -= 1

            if depth == 0:

                raw = html[pos:i + 1]

                try:
                    return json.loads(raw)
                except Exception:
                    try:
                        raw = raw.replace('\\"', '"')
                        raw = raw.replace("\\\\", "\\")
                        return json.loads(raw)
                    except Exception:
                        return None

    return None


# ============================================================
# CRICBUZZ PAGE FETCH
# ============================================================

def get_cricbuzz_data(url: str) -> Tuple[Dict[str, Any], str]:

    try:

        response = session.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        html = response.text

        next_data = extract_next_data(html)

        if next_data:

            page = (
                next_data
                .get("props", {})
                .get("pageProps", {})
                .get("page", {})
            )

            if isinstance(page, dict):

                if (
                    page.get("matchHeader")
                    or page.get("miniscore")
                    or page.get("scoreCard")
                ):
                    return page, html

        miniscore = extract_balanced_json_after_key(
            html,
            "miniscore",
        )

        match_header = extract_balanced_json_after_key(
            html,
            "matchHeader",
        )

        score_card = extract_balanced_json_after_key(
            html,
            "scoreCard",
        )

        data = {
            "miniscore": miniscore or {},
            "matchHeader": match_header or {},
            "scoreCard": score_card or [],
        }

        return data, html

    except Exception as exc:

        log.error(
            "Cricbuzz fetch failed: %s | %s",
            url,
            exc,
        )

        return {}, ""


# ============================================================
# PLAYER RESOLUTION
# ============================================================

def build_player_lookup(match_header: Dict[str, Any]) -> Dict[str, str]:

    lookup = {}

    players = match_header.get("players", [])

    if isinstance(players, dict):
        players = list(players.values())

    if not isinstance(players, list):
        players = []

    for player in players:

        if not isinstance(player, dict):
            continue

        pid = first_non_empty(
            player.get("id"),
            player.get("playerId"),
        )

        name = first_non_empty(
            player.get("name"),
            player.get("fullName"),
            player.get("shortName"),
        )

        if pid is not None and name:
            lookup[str(pid)] = clean_text(name)

    return lookup


def resolve_player(value: Any, lookup: Dict[str, str]) -> str:

    if isinstance(value, dict):

        pid = first_non_empty(
            value.get("id"),
            value.get("playerId"),
        )

        if pid is not None and str(pid) in lookup:
            return lookup[str(pid)]

        return clean_text(
            first_non_empty(
                value.get("name"),
                value.get("fullName"),
                value.get("shortName"),
            )
        )

    if value is None:
        return ""

    value_string = str(value).strip()

    if value_string in lookup:
        return lookup[value_string]

    return value_string


# ============================================================
# TEAM DATA
# ============================================================

def get_team_object(
    match_header: Dict[str, Any],
    team_key: str,
) -> Dict[str, Any]:

    team = match_header.get(team_key, {})

    return team if isinstance(team, dict) else {}


def team_id(team: Dict[str, Any]) -> str:

    return str(
        first_non_empty(
            team.get("id"),
            team.get("teamId"),
        ) or ""
    )


def team_short_name(team: Dict[str, Any], fallback: str) -> str:

    return clean_text(
        first_non_empty(
            team.get("shortName"),
            team.get("abbr"),
            team.get("shortname"),
            fallback,
        )
    )


def team_full_name(team: Dict[str, Any], fallback: str) -> str:

    return clean_text(
        first_non_empty(
            team.get("name"),
            team.get("teamName"),
            fallback,
        )
    )


# ============================================================
# PLAYER LIST NORMALIZATION
# ============================================================

def extract_player_names(
    value: Any,
    player_lookup: Dict[str, str],
) -> List[str]:

    result = []

    def add(value_item):

        name = resolve_player(value_item, player_lookup)

        if not name:
            return

        if name not in result:
            result.append(name)

    if isinstance(value, str):

        for part in re.split(r",|\|", value):
            add(part)

    elif isinstance(value, list):

        for item in value:
            add(item)

    elif isinstance(value, dict):

        # Possible map:
        # {"123": {...}, "456": {...}}
        for key, item in value.items():

            if isinstance(item, dict):
                add(item)

            else:
                add(key)

    return result


def find_team_player_field(
    team: Dict[str, Any],
    names: Tuple[str, ...],
):

    for name in names:

        if name in team:
            value = team.get(name)

            if value:
                return value

    return None


# ============================================================
# PLAYING XI / SQUAD
# ============================================================

def get_playing_xi(
    team: Dict[str, Any],
    player_lookup: Dict[str, str],
) -> List[str]:

    value = find_team_player_field(
        team,
        (
            "playingXI",
            "playingXi",
            "playingXIList",
            "playingXiList",
        ),
    )

    return extract_player_names(
        value,
        player_lookup,
    )


def get_squad(
    team: Dict[str, Any],
    player_lookup: Dict[str, str],
) -> List[str]:

    value = find_team_player_field(
        team,
        (
            "squad",
            "squadPlayers",
            "squadList",
            "players",
        ),
    )

    return extract_player_names(
        value,
        player_lookup,
    )


def get_team_players(
    team: Dict[str, Any],
    player_lookup: Dict[str, str],
) -> Dict[str, List[str]]:

    playing_xi = get_playing_xi(
        team,
        player_lookup,
    )

    squad = get_squad(
        team,
        player_lookup,
    )

    # Playing XI should always be part of squad
    merged_squad = list(squad)

    for name in playing_xi:

        if name not in merged_squad:
            merged_squad.append(name)

    return {
        "playingXI": playing_xi,
        "squad": merged_squad,
    }


# ============================================================
# MATCH FORMAT
# ============================================================

def detect_format(
    match_header: Dict[str, Any],
) -> str:

    candidates = [
        match_header.get("matchFormat"),
        match_header.get("format"),
        match_header.get("matchType"),
        match_header.get("type"),
    ]

    text = " ".join(
        clean_text(x)
        for x in candidates
        if x is not None
    ).lower()

    if not text:

        info = match_header.get("matchInfo", {})

        if isinstance(info, dict):

            text = " ".join(
                clean_text(
                    info.get(k, "")
                )
                for k in (
                    "matchFormat",
                    "format",
                    "matchType",
                    "type",
                    "description",
                )
            ).lower()

    if "test" in text:
        return "TEST"

    if "odi" in text:
        return "ODI"

    if "t20" in text:
        return "T20"

    if "t10" in text:
        return "T10"

    return clean_text(
        first_non_empty(
            *candidates,
            "UNKNOWN",
        )
    ).upper()


def detect_max_overs(
    match_header: Dict[str, Any],
    miniscore: Dict[str, Any],
    match_format: str,
) -> int:

    candidates = [
        match_header.get("maxOvers"),
        match_header.get("maxovers"),
        match_header.get("overs"),
        match_header.get("totalOvers"),
        match_header.get("inningsOvers"),
    ]

    for value in candidates:

        if isinstance(value, dict):

            value = first_non_empty(
                value.get("max"),
                value.get("total"),
                value.get("overs"),
            )

        try:

            number = int(float(str(value)))

            if 1 <= number <= 100:
                return number

        except Exception:
            pass

    format_upper = match_format.upper()

    if format_upper == "T20":
        return 20

    if format_upper == "T10":
        return 10

    if format_upper == "ODI":
        return 50

    # Some Cricbuzz pages expose series/match description
    # instead of a clean maxOvers field.
    text = json.dumps(
        match_header,
        ensure_ascii=False,
    ).lower()

    if "100 overs" in text:
        return 100

    if "50 overs" in text:
        return 50

    if "20 overs" in text:
        return 20

    if "10 overs" in text:
        return 10

    # Unknown format: don't invent 20.
    return 0


# ============================================================
# SCORECARD PARSING
# ============================================================

def get_scorecard_list(score_card: Any) -> List[Dict[str, Any]]:

    if isinstance(score_card, list):
        return [
            x for x in score_card
            if isinstance(x, dict)
        ]

    if isinstance(score_card, dict):

        for key in (
            "scoreCard",
            "scorecard",
            "scoreCardData",
            "innings",
        ):

            value = score_card.get(key)

            if isinstance(value, list):

                return [
                    x for x in value
                    if isinstance(x, dict)
                ]

    return []


# ============================================================
# BATTING CARD
# ============================================================

def parse_batting_card(
    innings: Dict[str, Any],
) -> List[Dict[str, Any]]:

    batting_details = innings.get(
        "batTeamDetails",
        {},
    )

    if not isinstance(batting_details, dict):
        return []

    batsmen = batting_details.get(
        "batsmenData",
        {},
    )

    result = []

    if isinstance(batsmen, dict):

        items = batsmen.items()

    elif isinstance(batsmen, list):

        items = enumerate(batsmen)

    else:

        items = []

    for key, player in items:

        if not isinstance(player, dict):
            continue

        name = clean_text(
            first_non_empty(
                player.get("batName"),
                player.get("name"),
                player.get("fullName"),
            )
        )

        if not name:
            continue

        out_desc = clean_text(
            player.get("outDesc", "")
        )

        result.append(
            {
                "id": str(
                    first_non_empty(
                        player.get("batId"),
                        player.get("id"),
                        key,
                    )
                ),
                "name": name,
                "runs": as_int(player.get("runs")),
                "balls": as_int(player.get("balls")),
                "fours": as_int(player.get("fours")),
                "sixes": as_int(player.get("sixes")),
                "strikeRate": clean_text(
                    first_non_empty(
                        player.get("strikerate"),
                        player.get("strikeRate"),
                        "0",
                    )
                ),
                "outDesc": out_desc,
                "isOut": bool(
                    out_desc
                    and out_desc.lower()
                    not in (
                        "not out",
                        "batting",
                        "retired hurt",
                    )
                ),
            }
        )

    return result


# ============================================================
# BOWLING CARD
# ============================================================

def parse_bowling_card(
    innings: Dict[str, Any],
) -> List[Dict[str, Any]]:

    bowling_details = innings.get(
        "bowlTeamDetails",
        {},
    )

    if not isinstance(bowling_details, dict):
        return []

    bowlers = bowling_details.get(
        "bowlersData",
        {},
    )

    result = []

    if isinstance(bowlers, dict):

        items = bowlers.items()

    elif isinstance(bowlers, list):

        items = enumerate(bowlers)

    else:

        items = []

    for key, bowler in items:

        if not isinstance(bowler, dict):
            continue

        name = clean_text(
            first_non_empty(
                bowler.get("bowlName"),
                bowler.get("name"),
                bowler.get("fullName"),
            )
        )

        if not name:
            continue

        overs = normalize_overs(
            first_non_empty(
                bowler.get("overs"),
                "0.0",
            )
        )

        result.append(
            {
                "id": str(
                    first_non_empty(
                        bowler.get("bowlId"),
                        bowler.get("id"),
                        key,
                    )
                ),
                "name": name,
                "overs": overs,
                "maidens": as_int(
                    bowler.get("maidens")
                ),
                "runs": as_int(
                    bowler.get("runs")
                ),
                "wickets": as_int(
                    bowler.get("wickets")
                ),
                "economy": clean_text(
                    first_non_empty(
                        bowler.get("economy"),
                        bowler.get("econ"),
                        "0.00",
                    )
                ),
            }
        )

    return result


# ============================================================
# FOW
# ============================================================

def parse_fow(
    innings: Dict[str, Any],
) -> List[Dict[str, Any]]:

    fow_data = innings.get(
        "fowData",
        {},
    )

    result = []

    if isinstance(fow_data, dict):
        items = fow_data.items()

    elif isinstance(fow_data, list):
        items = enumerate(fow_data)

    else:
        items = []

    for index, item in items:

        if not isinstance(item, dict):
            continue

        wicket_no = as_int(
            first_non_empty(
                item.get("wicketNum"),
                item.get("wktNo"),
                index + 1,
            ),
            index + 1,
        )

        result.append(
            {
                "wktNo": wicket_no,
                "score": as_int(
                    item.get("score")
                ),
                "overs": normalize_overs(
                    item.get("overs")
                ),
                "batterName": clean_text(
                    first_non_empty(
                        item.get("batName"),
                        item.get("batterName"),
                        item.get("name"),
                        "Unknown",
                    )
                ),
            }
        )

    return result


# ============================================================
# EXTRAS
# ============================================================

def parse_extras(
    innings: Dict[str, Any],
) -> Dict[str, int]:

    extras = innings.get(
        "extrasData",
        {},
    )

    if not isinstance(extras, dict):
        extras = {}

    return {
        "total": as_int(extras.get("total")),
        "byes": as_int(
            first_non_empty(
                extras.get("byes"),
                extras.get("b"),
            )
        ),
        "legByes": as_int(
            first_non_empty(
                extras.get("legByes"),
                extras.get("lb"),
            )
        ),
        "wides": as_int(
            first_non_empty(
                extras.get("wides"),
                extras.get("w"),
            )
        ),
        "noBalls": as_int(
            first_non_empty(
                extras.get("noBalls"),
                extras.get("nb"),
            )
        ),
        "penalty": as_int(
            first_non_empty(
                extras.get("penalty"),
                extras.get("p"),
            )
        ),
    }


# ============================================================
# INNINGS TEAM ID
# ============================================================

def get_innings_batting_team_id(
    innings: Dict[str, Any],
) -> str:

    details = innings.get(
        "batTeamDetails",
        {},
    )

    if not isinstance(details, dict):
        return ""

    return str(
        first_non_empty(
            details.get("batTeamId"),
            details.get("teamId"),
            "",
        )
    )


# ============================================================
# MAIN MATCH FETCH
# ============================================================

def fetch_match_smart(
    match_url: str,
) -> Optional[Dict[str, Any]]:

    match_id = extract_match_id(match_url)

    if not match_id:
        log.error(
            "Could not extract match ID from URL: %s",
            match_url,
        )
        return None

    live_url = make_live_url(match_url)
    scorecard_url = make_scorecard_url(match_url)

    live_data, _ = get_cricbuzz_data(
        live_url
    )

    scorecard_data, _ = get_cricbuzz_data(
        scorecard_url
    )

    live_header = (
        live_data.get("matchHeader", {})
        if isinstance(live_data, dict)
        else {}
    )

    score_header = (
        scorecard_data.get("matchHeader", {})
        if isinstance(scorecard_data, dict)
        else {}
    )

    # Prefer scorecard header when available,
    # otherwise live header.
    match_header = (
        score_header
        if isinstance(score_header, dict)
        and score_header
        else live_header
    )

    if not match_header:
        log.warning(
            "No matchHeader found for match %s",
            match_id,
        )
        return None

    miniscore = (
        live_data.get("miniscore", {})
        if isinstance(live_data, dict)
        else {}
    )

    if not isinstance(miniscore, dict):
        miniscore = {}

    score_card = (
        scorecard_data.get("scoreCard", [])
        if isinstance(scorecard_data, dict)
        else []
    )

    innings_list = get_scorecard_list(
        score_card
    )

    # --------------------------------------------------------
    # TEAM A / TEAM B
    # --------------------------------------------------------

    team_a_obj = get_team_object(
        match_header,
        "team1",
    )

    team_b_obj = get_team_object(
        match_header,
        "team2",
    )

    team_a_id = team_id(team_a_obj)
    team_b_id = team_id(team_b_obj)

    team_a_short = team_short_name(
        team_a_obj,
        "TBA",
    )

    team_b_short = team_short_name(
        team_b_obj,
        "TBB",
    )

    team_a_name = team_full_name(
        team_a_obj,
        "Team A",
    )

    team_b_name = team_full_name(
        team_b_obj,
        "Team B",
    )

    # --------------------------------------------------------
    # PLAYER LOOKUP
    # --------------------------------------------------------

    player_lookup = build_player_lookup(
        match_header
    )

    team_a_players = get_team_players(
        team_a_obj,
        player_lookup,
    )

    team_b_players = get_team_players(
        team_b_obj,
        player_lookup,
    )

    # --------------------------------------------------------
    # FORMAT / OVERS
    # --------------------------------------------------------

    match_format = detect_format(
        match_header
    )

    max_overs = detect_max_overs(
        match_header,
        miniscore,
        match_format,
    )

    # --------------------------------------------------------
    # INNINGS DATA
    # --------------------------------------------------------

    innings_data = []

    batting_card_inn1 = []
    batting_card_inn2 = []

    bowling_card_inn1 = []
    bowling_card_inn2 = []

    fow_inn1 = []
    fow_inn2 = []

    extras_inn1 = {}
    extras_inn2 = {}

    batting_team_inn1 = ""
    batting_team_inn2 = ""

    for index, innings in enumerate(innings_list):

        bat_team_id = get_innings_batting_team_id(
            innings
        )

        if bat_team_id == team_a_id:
            batting_team = "A"

        elif bat_team_id == team_b_id:
            batting_team = "B"

        else:
            batting_team = ""

        batting_card = parse_batting_card(
            innings
        )

        bowling_card = parse_bowling_card(
            innings
        )

        fow = parse_fow(
            innings
        )

        extras = parse_extras(
            innings
        )

        innings_data.append(
            {
                "inningsNo": index + 1,
                "battingTeam": batting_team,
                "battingTeamId": bat_team_id,
                "battingCard": batting_card,
                "bowlingCard": bowling_card,
                "fow": fow,
                "extras": extras,
            }
        )

        if index == 0:

            batting_team_inn1 = batting_team
            batting_card_inn1 = batting_card
            bowling_card_inn1 = bowling_card
            fow_inn1 = fow
            extras_inn1 = extras

        elif index == 1:

            batting_team_inn2 = batting_team
            batting_card_inn2 = batting_card
            bowling_card_inn2 = bowling_card
            fow_inn2 = fow
            extras_inn2 = extras

    # --------------------------------------------------------
    # CURRENT BATTING TEAM
    # --------------------------------------------------------

    current_bat = miniscore.get(
        "batTeam",
        {},
    )

    if not isinstance(current_bat, dict):
        current_bat = {}

    current_bat_id = str(
        first_non_empty(
            current_bat.get("teamId"),
            "",
        )
    )

    if current_bat_id == team_a_id:
        current_batting_team = "A"

    elif current_bat_id == team_b_id:
        current_batting_team = "B"

    else:

        current_batting_team = (
            batting_team_inn2
            or batting_team_inn1
        )

    # --------------------------------------------------------
    # CURRENT INNINGS
    # --------------------------------------------------------

    current_innings = 1

    if innings_data:

        if current_batting_team == batting_team_inn2:
            current_innings = 2

        if len(innings_data) >= 2:
            current_innings = 2

    # --------------------------------------------------------
    # CURRENT SCORE
    # --------------------------------------------------------

    score = as_int(
        first_non_empty(
            current_bat.get("teamScore"),
            current_bat.get("score"),
            0,
        )
    )

    wickets = as_int(
        first_non_empty(
            current_bat.get("teamWkts"),
            current_bat.get("wickets"),
            0,
        )
    )

    current_overs = normalize_overs(
        first_non_empty(
            miniscore.get("overs"),
            current_bat.get("overs"),
            "0.0",
        )
    )

    target = as_int(
        miniscore.get("target")
    )

    current_rr = clean_text(
        first_non_empty(
            miniscore.get("currentRunRate"),
            miniscore.get("crr"),
            "0.00",
        )
    )

    # --------------------------------------------------------
    # CURRENT BATTERS
    # --------------------------------------------------------

    striker = miniscore.get(
        "batsmanStriker",
        {},
    )

    non_striker = miniscore.get(
        "batsmanNonStriker",
        {},
    )

    if not isinstance(striker, dict):
        striker = {}

    if not isinstance(non_striker, dict):
        non_striker = {}

    # --------------------------------------------------------
    # CURRENT BOWLER
    # --------------------------------------------------------

    current_bowler = first_non_empty(
        miniscore.get("bowlerStriker"),
        miniscore.get("currentBowler"),
        miniscore.get("bowler"),
        {},
    )

    if not isinstance(current_bowler, dict):
        current_bowler = {}

    # --------------------------------------------------------
    # CURRENT PARTNERSHIP
    # --------------------------------------------------------

    partnership = miniscore.get(
        "partnerShip",
        {},
    )

    if not isinstance(partnership, dict):
        partnership = {}

    # --------------------------------------------------------
    # MATCH STATUS
    # --------------------------------------------------------

    state = clean_text(
        first_non_empty(
            match_header.get("state"),
            match_header.get("status"),
            "",
        )
    )

    complete_value = match_header.get(
        "complete",
        False,
    )

    is_complete = (
        str(state).lower() == "complete"
        or bool(complete_value)
    )

    # --------------------------------------------------------
    # FINAL DATA
    # --------------------------------------------------------

    data = {

        # CENTRAL ID
        "matchId": match_id,
        "source": "cricbuzz",

        # TEAM IDENTITY
        # IMPORTANT: these never swap.
        "teamA": team_a_short,
        "teamA_name": team_a_name,
        "teamA_id": team_a_id,

        "teamB": team_b_short,
        "teamB_name": team_b_name,
        "teamB_id": team_b_id,

        # FORMAT
        "matchFormat": match_format,
        "format": match_format,
        "maxOvers": max_overs,

        # INNINGS IDENTITY
        "currentInnings": current_innings,
        "battingTeam": current_batting_team,

        "bat_team_inn1": batting_team_inn1,
        "bat_team_inn2": batting_team_inn2,

        # CURRENT SCORE
        "score": score,
        "wickets": wickets,
        "overs": current_overs,
        "target": target,
        "crr": current_rr,

        # STATUS
        "matchStatus": state,
        "isComplete": is_complete,

        # CURRENT BATTERS
        "strikerName": clean_text(
            first_non_empty(
                striker.get("name"),
                striker.get("batName"),
                "—",
            )
        ),

        "strikerRuns": as_int(
            striker.get("runs")
        ),

        "strikerBalls": as_int(
            striker.get("balls")
        ),

        "nonStrikerName": clean_text(
            first_non_empty(
                non_striker.get("name"),
                non_striker.get("batName"),
                "—",
            )
        ),

        "nonStrikerRuns": as_int(
            non_striker.get("runs")
        ),

        "nonStrikerBalls": as_int(
            non_striker.get("balls")
        ),

        # CURRENT BOWLER
        "bowlerName": clean_text(
            first_non_empty(
                current_bowler.get("name"),
                current_bowler.get("bowlName"),
                "—",
            )
        ),

        "bowlerOvers": normalize_overs(
            first_non_empty(
                current_bowler.get("overs"),
                "0.0",
            )
        ),

        "bowlerRuns": as_int(
            current_bowler.get("runs")
        ),

        "bowlerWickets": as_int(
            current_bowler.get("wickets")
        ),

        # CURRENT PARTNERSHIP
        "currPartnershipRuns": as_int(
            first_non_empty(
                partnership.get("runs"),
                partnership.get("totalRuns"),
                0,
            )
        ),

        "currPartnershipBalls": as_int(
            first_non_empty(
                partnership.get("balls"),
                partnership.get("totalBalls"),
                0,
            )
        ),

        # RECENT OVERS
        "recentOvs": clean_text(
            miniscore.get(
                "recentOvsStats",
                ""
            )
        ),

        # PLAYERS
        "playing11_A": team_a_players["playingXI"],
        "playing11_B": team_b_players["playingXI"],

        "squad_A": team_a_players["squad"],
        "squad_B": team_b_players["squad"],

        "squadSize_A": len(
            team_a_players["squad"]
        ),

        "squadSize_B": len(
            team_b_players["squad"]
        ),

        "playing11Size_A": len(
            team_a_players["playingXI"]
        ),

        "playing11Size_B": len(
            team_b_players["playingXI"]
        ),

        # INNINGS 1
        "battingCard_inn1": batting_card_inn1,
        "bowlingCard_inn1": bowling_card_inn1,
        "fow_inn1": fow_inn1,
        "extras_inn1": extras_inn1,

        # INNINGS 2
        "battingCard_inn2": batting_card_inn2,
        "bowlingCard_inn2": bowling_card_inn2,
        "fow_inn2": fow_inn2,
        "extras_inn2": extras_inn2,

        # RAW NORMALIZED INNINGS
        "innings": innings_data,

        # DEBUG / TRACE
        "sourceUrl": match_url,
        "liveUrl": live_url,
        "scorecardUrl": scorecard_url,

        "updatedAt": int(
            time.time() * 1000
        ),
    }

    return data


# ============================================================
# FIREBASE
# ============================================================

def firebase_get(path: str):

    if not FIREBASE_DB_URL:
        raise RuntimeError(
            "FIREBASE_DB_URL environment variable is missing."
        )

    response = session.get(
        f"{FIREBASE_DB_URL}/{path}.json",
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


def firebase_put(
    path: str,
    data: Any,
):

    if not FIREBASE_DB_URL:
        raise RuntimeError(
            "FIREBASE_DB_URL environment variable is missing."
        )

    response = session.put(
        f"{FIREBASE_DB_URL}/{path}.json",
        json=data,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    return response


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    if not FIREBASE_DB_URL:

        log.error(
            "FIREBASE_DB_URL is not configured."
        )

        return

    log.info(
        "MB Live Studio Cricbuzz Fetcher started."
    )

    log.info(
        "Firebase: %s",
        FIREBASE_DB_URL,
    )

    last_url = ""

    while True:

        try:

            config = firebase_get(
                "auto_fetch_config"
            )

            if not isinstance(config, dict):
                time.sleep(FETCH_INTERVAL)
                continue

            current_url = clean_text(
                config.get("url")
            )

            if not current_url:

                time.sleep(FETCH_INTERVAL)
                continue

            # ------------------------------------------------
            # NEW MATCH
            # ------------------------------------------------

            if current_url != last_url:

                match_id = extract_match_id(
                    current_url
                )

                log.info(
                    "New Cricbuzz match: %s | ID=%s",
                    current_url,
                    match_id or "UNKNOWN",
                )

                # IMPORTANT:
                # Do NOT delete current_match_auto here.
                #
                # Old data stays available until the new match
                # is successfully parsed.
                #
                # This prevents a temporary Firebase blank state.

                last_url = current_url

            # ------------------------------------------------
            # FETCH
            # ------------------------------------------------

            data = fetch_match_smart(
                current_url
            )

            if data:

                firebase_put(
                    "current_match_auto",
                    data,
                )

                log.info(
                    "PUSHED | %s vs %s | %s %s/%s (%s/%s) | ID=%s",
                    data["teamA"],
                    data["teamB"],
                    data["matchFormat"],
                    data["score"],
                    data["wickets"],
                    data["overs"],
                    data["maxOvers"],
                    data["matchId"],
                )

            else:

                log.warning(
                    "No valid data returned. "
                    "Keeping previous Firebase data."
                )

        except KeyboardInterrupt:

            log.info(
                "Fetcher stopped."
            )

            break

        except Exception as exc:

            log.exception(
                "Fetcher loop error: %s",
                exc,
            )

        time.sleep(
            FETCH_INTERVAL
        )


if __name__ == "__main__":
    main()
```
