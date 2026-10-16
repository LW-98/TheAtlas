"""Game API for The Atlas - a daily "guess when & where this photo was taken".

One Lambda behind an API Gateway HTTP API. Each day everyone gets the same five
historical photos; for each you guess the year and drop a map pin. Score is
distance plus how far off the year is, up to 10,000 a round (50,000 a day).

The daily puzzle is built at midnight UTC by src/daily/app.py and left in S3 as
puzzle/current.json; this function just serves it and scores guesses. It never
calls Wikimedia itself.

Routes:
    GET  /api/puzzle/today        today's photo URLs (no answers)
    POST /api/guess               score one round, reveal its answer
    POST /api/scores              save a finished game under a team name
    GET  /api/scores/team/{team}  a team's own scores, best first

Answers aren't sent up front - only image URLs are. Each round's answer comes
back when you guess it, so the day can't be scraped in a single request.
"""

import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

# --- Configuration -----------------------------------------------------------

SCORES_TABLE = os.environ.get("SCORES_TABLE", "")
PUZZLE_BUCKET = os.environ.get("PUZZLE_BUCKET", "")

MAX_LOCATION_SCORE = 5000
MAX_YEAR_SCORE = 5000
MAX_ROUND_SCORE = MAX_LOCATION_SCORE + MAX_YEAR_SCORE
# Sanity cap on a submitted total (real max is ~10k per round).
SCORE_CAP = 100000

# Falloff constants for the scoring curves below.
_LOCATION_SCALE_KM = 1000.0
_YEAR_SCALE = 15.0

_ddb = boto3.resource("dynamodb")
_scores_table = _ddb.Table(SCORES_TABLE) if SCORES_TABLE else None
_s3 = boto3.client("s3") if PUZZLE_BUCKET else None

CURRENT_KEY = "puzzle/current.json"


# --- Puzzle loading -----------------------------------------------------------

# Cache the current puzzle briefly so we're not hitting S3 on every request;
# refreshes within a minute of the daily swap.
_CACHE_TTL_SECONDS = 60
_cache: dict = {"loaded_at": 0.0, "puzzle": None}

# ?date=YYYY-MM-DD replays an archived puzzle (for testing). Turn off in
# production with ALLOW_DATE_OVERRIDE=false.
ALLOW_DATE_OVERRIDE = os.environ.get("ALLOW_DATE_OVERRIDE", "true").lower() == "true"


def _bundled_sample() -> dict:
    """The puzzle shipped with the code - used locally and before the first build."""
    path = Path(__file__).with_name("sample_puzzle.json")
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _read_s3_puzzle(key: str) -> dict | None:
    if _s3 is None:
        return None
    try:
        obj = _s3.get_object(Bucket=PUZZLE_BUCKET, Key=key)
        return json.loads(obj["Body"].read().decode())
    except Exception as e:  # noqa: BLE001
        print(f"Puzzle load failed for {key}: {e}")
        return None


def _load_puzzle(event: dict | None = None) -> dict:
    """The full puzzle with answers: the ?date= override if allowed, else the
    cached current one, else the bundled sample."""
    if event and ALLOW_DATE_OVERRIDE:
        params = event.get("queryStringParameters") or {}
        override = str(params.get("date", "")).strip()
        if override:
            archived = _read_s3_puzzle(f"puzzle/{override}.json")
            if archived:
                return archived

    now = time.time()
    if _cache["puzzle"] is not None and now - _cache["loaded_at"] < _CACHE_TTL_SECONDS:
        return _cache["puzzle"]

    puzzle = _read_s3_puzzle(CURRENT_KEY) or _bundled_sample()
    _cache["puzzle"] = puzzle
    _cache["loaded_at"] = now
    return puzzle


def _max_game_score(puzzle: dict) -> int:
    return MAX_ROUND_SCORE * len(puzzle.get("rounds", []))


# --- Scoring ------------------------------------------------------------------

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two (lat, lon) points."""
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    h = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return 2 * radius * math.asin(math.sqrt(h))


def _score_location(distance_km: float) -> int:
    """5000 at the exact spot, decaying smoothly with distance.
    ~4500 within 100 km, ~1800 at 1000 km, near zero across an ocean."""
    return round(MAX_LOCATION_SCORE * math.exp(-distance_km / _LOCATION_SCALE_KM))


def _score_year(years_off: int) -> int:
    """5000 for the exact year, falling off either side. Getting within a few
    years still scores well; decades off scores little."""
    return round(MAX_YEAR_SCORE * math.exp(-abs(years_off) / _YEAR_SCALE))


# --- HTTP helpers -------------------------------------------------------------

def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _parse_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _public_round(photo: dict, index: int) -> dict:
    """Pre-guess view: image only. Year, place, coords and credit stay hidden."""
    return {"round": index, "imageUrl": photo.get("imageUrl", "")}


def _reveal(photo: dict) -> dict:
    """Everything about a photo, sent once its round is guessed - including the
    attribution Wikimedia's licences require."""
    return {
        "year": photo.get("year"),
        "lat": photo.get("lat"),
        "lon": photo.get("lon"),
        "place": photo.get("place", ""),
        "title": photo.get("title", photo.get("id", "")),
        "description": photo.get("description", ""),
        "author": photo.get("author", "Unknown"),
        "license": photo.get("license", ""),
        "licenseUrl": photo.get("licenseUrl", ""),
        "sourceUrl": photo.get("sourceUrl", ""),
        "imageUrl": photo.get("imageUrl", ""),
    }


# --- Game routes --------------------------------------------------------------

def _get_puzzle_today(event: dict) -> dict:
    puzzle = _load_puzzle(event)
    rounds = puzzle.get("rounds", [])
    return _response(200, {
        "puzzleId": puzzle.get("puzzleId", ""),
        "rounds": [_public_round(p, i) for i, p in enumerate(rounds)],
        "roundCount": len(rounds),
        "maxRoundScore": MAX_ROUND_SCORE,
        "maxGameScore": _max_game_score(puzzle),
    })


def _post_guess(event: dict) -> dict:
    body = _parse_body(event)
    puzzle_id = str(body.get("puzzleId", "")).strip()
    round_index = body.get("round")
    year = body.get("year")
    lat = body.get("lat")
    lon = body.get("lng", body.get("lon"))

    if not puzzle_id or round_index is None:
        return _response(400, {"error": "puzzleId and round are required"})

    puzzle = _load_puzzle(event)
    if puzzle_id != puzzle.get("puzzleId"):
        return _response(409, {"error": "This puzzle is no longer active"})

    try:
        round_index = int(round_index)
        year = int(year)
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return _response(400, {"error": "round, year, lat and lng must be numbers"})

    rounds = puzzle.get("rounds", [])
    if not (0 <= round_index < len(rounds)):
        return _response(400, {"error": f"round must be 0..{len(rounds) - 1}"})
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return _response(400, {"error": "lat/lng out of range"})

    photo = rounds[round_index]
    distance_km = _haversine_km(lat, lon, float(photo["lat"]), float(photo["lon"]))
    years_off = year - int(photo["year"])
    location_score = _score_location(distance_km)
    year_score = _score_year(years_off)

    return _response(200, {
        "round": round_index,
        "locationScore": location_score,
        "yearScore": year_score,
        "roundScore": location_score + year_score,
        "distanceKm": round(distance_km, 1),
        "yearsOff": years_off,
        "answer": _reveal(photo),
    })


# --- Scores (per-team history) -----------------------------------------------
# No login - team names are self-declared, which is fine for a trusted group.
# One row per (team, edition), first write wins (see below).

def _post_score(event: dict) -> dict:
    body = _parse_body(event)
    team = str(body.get("team", "")).strip()[:30]
    puzzle_id = str(body.get("puzzleId", "")).strip()
    game_id = str(body.get("gameId", "")).strip()[:64]
    score = body.get("score")

    if not team or not puzzle_id:
        return _response(400, {"error": "team and puzzleId are required"})
    if not isinstance(score, int) or not (0 <= score <= SCORE_CAP):
        return _response(400, {"error": f"score must be an int in 0..{SCORE_CAP}"})
    if _scores_table is None:
        return _response(503, {"error": "Scores not available"})

    # First write wins: once a team files an edition it's locked, so you can't
    # replay to beat your own score. gameId records which playthrough it was.
    item = {
        "team": team,
        "puzzleId": puzzle_id,
        "score": score,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if game_id:
        item["gameId"] = game_id
    try:
        _scores_table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(team) AND attribute_not_exists(puzzleId)",
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return _response(409, {
                "error": "This team has already filed today's edition.",
                "alreadyFiled": True,
            })
        raise
    return _response(200, {"success": True})


def _get_team_scores(event: dict) -> dict:
    """A team's scores, each with the edition it came from, best first. Feeds
    the team's Past Editions list and its chart. No cross-team ranking."""
    params = event.get("pathParameters") or {}
    team = str(params.get("team", "")).strip()
    if not team:
        return _response(400, {"error": "team is required"})
    if _scores_table is None:
        return _response(200, {"team": team, "count": 0, "best": 0, "games": []})

    resp = _scores_table.query(KeyConditionExpression=Key("team").eq(team))
    games = sorted(
        ({"score": int(i.get("score", 0)), "puzzleId": i.get("puzzleId", "")}
         for i in resp.get("Items", [])),
        key=lambda g: g["score"], reverse=True,
    )
    return _response(200, {
        "team": team,
        "count": len(games),
        "best": games[0]["score"] if games else 0,
        "games": games,
    })


# --- Entry point --------------------------------------------------------------

_ROUTES = {
    "GET /api/puzzle/today": _get_puzzle_today,
    "POST /api/guess": _post_guess,
    "POST /api/scores": _post_score,
    "GET /api/scores/team/{team}": _get_team_scores,
}


def handler(event, context):
    route_key = event.get("routeKey", "")
    try:
        route = _ROUTES.get(route_key)
        if route is None:
            return _response(404, {"error": f"No route for {route_key}"})
        return route(event)
    except Exception as exc:  # noqa: BLE001
        print(f"Unhandled error on {route_key}: {exc!r}")
        return _response(500, {"error": "Internal server error"})
