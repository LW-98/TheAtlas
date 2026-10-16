"""
Generate src/handlers/sample_puzzle.json - the fallback puzzle the game serves
before the first scheduled build, and the data its offline mock mode uses.

Same idea as the daily builder (src/daily/app.py), but no AWS needed: it links
Commons thumbnails directly instead of copying them to S3, and geocodes cities
on the fly.

Usage:
    py -3.12 scripts/build_sample_puzzle.py [rounds]
"""

import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://commons.wikimedia.org/w/api.php"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "DailyTimeGuess/1.0 (https://github.com/LW-98; sample builder)"
OUTPUT = Path(__file__).parent.parent / "src" / "handlers" / "sample_puzzle.json"

IMG_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff")
ALLOWED_MIME = {"image/jpeg", "image/png", "image/tiff"}
YEAR_MIN = 1890
YEAR_MAX = datetime.now(timezone.utc).year
THUMB_WIDTH = 1280
MIN_WIDTH, MIN_HEIGHT = 1000, 700
MIN_RATIO, MAX_RATIO = 0.5, 2.2
MAX_ATTEMPTS = 400

# Single source of truth for the city pool: the builder Lambda's cities.py.
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "daily"))
from cities import CITIES  # noqa: E402

TITLE_DENYLIST = (
    "map", "plan ", "coat of arms", "logo", "flag", "stamp", "seal", "medal",
    "banknote", "coin", "diagram", "document", "manuscript", "blazon", "emblem",
    "sheet music", "chart", "poster", "title page", "newspaper", "letter from",
    "certificate", "ticket", "award", "cover of", "painting", "drawing",
    "engraving", "lithograph", "etching", "sketch", "watercolour", "watercolor",
    "illustration", "portrait", "caricature", "cartoon", "review", "magazine",
    "journal", "advertisement",
)

CATEGORY_DENYLIST = (
    "painting", "drawing", "engraving", "lithograph", "etching", "sketch",
    "watercolor", "watercolour", "illustration", "portrait", "artwork",
    "caricature", "cartoon", "sculpture", "map", "diagram", "document",
    "manuscript", "poster", "advertis", "coat of arms", "logo", "stamp",
    "banknote", "coin", "medal", "sheet music", "book illustrations",
    "magazine", "newspaper", "postcard",
)


def api(params: dict) -> dict:
    params = {**params, "format": "json"}
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        print(f"  api error: {e}")
        return {}


def category_files(cat: str) -> list:
    data = api({
        "action": "query", "list": "categorymembers",
        "cmtitle": f"Category:{cat}", "cmtype": "file", "cmlimit": 200,
    })
    return (data.get("query") or {}).get("categorymembers") or []


def image_details(titles: list) -> dict:
    data = api({
        "action": "query", "titles": "|".join(titles[:50]),
        "prop": "imageinfo|categories", "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": THUMB_WIDTH, "cllimit": "max",
    })
    pages = (data.get("query") or {}).get("pages") or {}
    out = {}
    for p in pages.values():
        ii = (p.get("imageinfo") or [None])[0]
        if ii:
            cats = [c.get("title", "").replace("Category:", "")
                    for c in (p.get("categories") or [])]
            out[p["title"]] = {"ii": ii, "cats": cats}
    return out


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"&[a-z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def denied(title: str) -> bool:
    low = title.lower()
    return any(bad in low for bad in TITLE_DENYLIST)


def bad_categories(cats: list) -> bool:
    blob = " | ".join(cats).lower()
    return any(bad in blob for bad in CATEGORY_DENYLIST)


def is_quality(title: str, ii: dict, cats: list) -> bool:
    if denied(title) or bad_categories(cats) or ii.get("mime") not in ALLOWED_MIME:
        return False
    w, h = int(ii.get("width", 0)), int(ii.get("height", 0))
    if w < MIN_WIDTH or h < MIN_HEIGHT:
        return False
    ratio = w / h if h else 0
    return MIN_RATIO <= ratio <= MAX_RATIO and bool(ii.get("thumburl"))


def geocode(city: str, cache: dict) -> list | None:
    if city in cache:
        return cache[city]
    params = urllib.parse.urlencode({"q": city, "format": "json", "limit": 1})
    try:
        req = urllib.request.Request(f"{NOMINATIM}?{params}",
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as resp:
            results = json.loads(resp.read().decode())
    except Exception as e:  # noqa: BLE001
        print(f"  geocode error {city}: {e}")
        results = None
    time.sleep(1.2)
    if results:
        cache[city] = [round(float(results[0]["lat"]), 4),
                       round(float(results[0]["lon"]), 4)]
        return cache[city]
    return None


def main():
    rounds_wanted = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    coords: dict = {}
    rounds = []
    used_cities, used_years = set(), set()
    attempts = 0

    print(f"Building a {rounds_wanted}-round sample puzzle...\n")
    while len(rounds) < rounds_wanted and attempts < MAX_ATTEMPTS:
        attempts += 1
        city = random.choice(CITIES)
        if city in used_cities:
            continue
        year = random.randint(YEAR_MIN, YEAR_MAX)
        if year in used_years:
            continue

        members = category_files(f"{year} in {city}")
        titles = [m["title"] for m in members
                  if m["title"].lower().endswith(IMG_EXT) and not denied(m["title"])]
        if not titles:
            continue
        random.shuffle(titles)
        details = image_details(titles)
        for title in titles:
            entry = details.get(title)
            if not entry or not is_quality(title, entry["ii"], entry["cats"]):
                continue
            ii = entry["ii"]
            latlon = geocode(city, coords)
            if not latlon:
                break
            meta = ii.get("extmetadata") or {}
            rounds.append({
                "imageUrl": ii["thumburl"].split("?")[0],
                "year": year,
                "lat": latlon[0],
                "lon": latlon[1],
                "place": city,
                "title": title.replace("File:", "").strip(),
                "description": clean((meta.get("ImageDescription") or {}).get("value", ""))[:280],
                "author": clean((meta.get("Artist") or {}).get("value", "")) or "Unknown",
                "license": clean((meta.get("LicenseShortName") or {}).get("value", "")) or "See source",
                "licenseUrl": (meta.get("LicenseUrl") or {}).get("value", ""),
                "sourceUrl": ii.get("descriptionurl", ""),
            })
            used_cities.add(city)
            used_years.add(year)
            print(f"  round {len(rounds)}: {city} {year} - {title.replace('File:', '')}")
            break

    puzzle = {
        "puzzleId": "sample",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "rounds": rounds,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(puzzle, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {len(rounds)} rounds to {OUTPUT} in {attempts} attempts.")


if __name__ == "__main__":
    main()
