"""
Builds one day's puzzle; runs daily at midnight UTC.

Each run picks five quality, guessable photos across different cities and eras,
copies their images into our own S3 bucket (so the game still works if the
source is blocked on a corporate network), and writes the puzzle - answers
included - to S3:

    images/<date>/<n>.jpg   the five photos
    puzzle/<date>.json      that day's puzzle, archived
    puzzle/current.json     what the game serves right now

Photos come from Wikimedia Commons' "<year> in <city>" categories, so every
photo has a known year and city without relying on sparse EXIF data. Candidates
go through a photo screen (so we don't serve paintings, prints or scanned
documents) and must then be guessable: a photo is used only if it also sits in a
scene category - Streets, Buildings, Views, Squares, Churches, Stations - which
means an outdoor, locatable place rather than an interior, portrait or close-up.

City coordinates are looked up once via OpenStreetMap and cached, so the answer
location is the city centre; adding a city is just a name in cities.py. Each
photo keeps its attribution, shown on the reveal, per its licence.
"""

import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import boto3

from cities import CITIES

PUZZLE_BUCKET = os.environ["PUZZLE_BUCKET"]
REGION = os.environ.get("AWS_REGION", "eu-west-2")
ROUNDS_PER_DAY = int(os.environ.get("ROUNDS_PER_DAY", "5"))

API = "https://commons.wikimedia.org/w/api.php"
UA = "DailyTimeGuess/1.0 (https://github.com/LW-98; daily puzzle builder)"
NOMINATIM = "https://nominatim.openstreetmap.org/search"

IMG_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff")
ALLOWED_MIME = {"image/jpeg", "image/png", "image/tiff"}

YEAR_MIN = 1890
YEAR_MAX = datetime.now(timezone.utc).year  # through the current year
THUMB_WIDTH = 1280
MIN_WIDTH, MIN_HEIGHT = 1000, 700
MIN_RATIO, MAX_RATIO = 0.5, 2.2          # landscape-ish; filters tall documents
MAX_ATTEMPTS = 400

COORDS_KEY = "city_coords.json"
CURRENT_KEY = "puzzle/current.json"

TITLE_DENYLIST = (
    "map", "plan ", "coat of arms", "logo", "flag", "stamp", "seal",
    "medal", "banknote", "coin", "diagram", "document", "manuscript",
    "blazon", "emblem", "sheet music", "chart", "poster", "title page",
    "newspaper", "letter from", "certificate", "ticket", "award", "cover of",
    "painting", "drawing", "engraving", "lithograph", "etching", "sketch",
    "watercolour", "watercolor", "illustration", "portrait", "caricature",
    "cartoon", "review", "magazine", "journal", "advertisement",
)

# Categories that mean a file isn't a locatable scene photo. Checked as
# substrings against a file's categories - more reliable than the title alone,
# which misses plenty of artwork and scans (e.g. "Thomas Moran - Venice, 1896").
CATEGORY_DENYLIST = (
    "painting", "drawing", "engraving", "lithograph", "chromolithograph",
    "photochrom", "etching", "aquatint", "mezzotint", "sketch", "woodcut",
    "watercolor", "watercolour", "aquarelle", "gouache", "illustration",
    "portrait", "artwork", "caricature", "cartoon", "sculpture", "frontispiece",
    "map", "diagram", "document", "manuscript", "poster", "advertis",
    "coat of arms", "logo", "stamp", "banknote", "coin", "medal", "sheet music",
    "book illustrations", "magazine", "newspaper", "postcard",
    "travelers in the middle east",  # TIMEA: scanned travel-book plates, mostly art
    # Scanned paper documents - text, not scenes.
    "by-law", "bylaw", "pamphlet", "leaflet", "brochure", "title page",
    "book cover", "certificate", "letterhead", "catalogue",
)

# Words that reliably mark a non-photo (a painting, print, book plate or scanned
# document) even when they surface only in a file's description or credit, not
# its title or categories - which is how these slip through (e.g. a watercolour
# whose only tell is the description "Painting." and a credit citing the source
# book, or a bylaws booklet). Kept deliberately specific so real photo captions
# ("portrait orientation", "a road map on the wall") don't trip it.
DESCRIPTION_DENY_TERMS = (
    # Artwork / prints
    "painting", "watercolour", "watercolor", "aquarelle", "gouache",
    "oil on canvas", "tempera", "pastel drawing", "lithograph",
    "chromolithograph", "oleograph", "engraving", "etching", "aquatint",
    "mezzotint", "woodcut", "wood engraving", "steel engraving",
    "illustrated by", "illus. in colour", "illus. in color", "colour plate",
    "color plate", "frontispiece", "fresco", "mural painting", "drawing of",
    "print of", "sketch of",
    "travelers in the middle east",
    # Scanned documents
    "bylaws", "by-laws", "title page", "book cover", "pamphlet", "leaflet",
)

# The real root cause of non-photos: almost all of them (paintings, engravings,
# drawings, book plates, documents) reach Commons through a handful of
# scanned-book and archive bulk-upload pipelines, which stamp a recognisable
# signature into the file's description or credit. Screening on that source -
# rather than trying to name every art form - catches the whole class in one
# rule and keeps working as new material arrives from the same pipelines.
SCAN_PROVENANCE_MARKERS = (
    "find matches",                              # Internet Archive book-image bot
    "identifier :", "identifier:",               # IA / book-scan metadata dumps
    "book contributor", "digitizing sponsor",
    "digitized by", "digitised by",
    "archive.org/details", "archive.org/stream",
    "bub_gb_", "books.google",                   # Google Books scans
    "travelers in the middle east",              # TIMEA travel-book plates
    "from the book", "plate from", "scanned from",
)

# Positive guessability signal: a file that also sits in one of these scene
# categories ("Streets in Paris", "Churches in Vienna", "Views of Lisbon", ...)
# shows an outdoor, locatable place - exactly what the game needs - so we prefer
# those over equally-valid-but-dull candidates like interiors or close-ups.
SCENE_CATEGORY_HINTS = (
    "street", "avenue", "boulevard", "road", "square", "plaza", "piazza",
    "building", "architecture", "skyline", "cityscape", "panorama", "aerial",
    "view of", "views of", "harbour", "harbor", "port", "waterfront", "quay",
    "bridge", "castle", "palace", "church", "cathedral", "basilica", "chapel",
    "mosque", "temple", "synagogue", "tower", "monument", "memorial", "gate",
    "market", "fountain", "station", "railway", "stadium", "park", "garden",
    "town hall", "city hall", "landmark", "promenade", "waterfall",
)

# People-as-subject categories. A scene category can match on the venue (a
# concert hall, a theatre) while the photo is really a portrait of performers -
# not a locatable place - so these veto a candidate even when a scene category
# is present. Sport is deliberately NOT here: a player in national or club kit
# is a useful location clue, so sports photos tagged with a venue are allowed.
PEOPLE_SUBJECT_HINTS = (
    "musicians", "singers", "guitarists", "bands", "orchestras", "choirs",
    "actors", "actresses", "dancers", "performers",
)

s3 = boto3.client("s3")


# --- Shared helpers -----------------------------------------------------------

def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"&[a-z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _http_json(url: str) -> dict:
    """GET a JSON API with a UA, light retry on throttling."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            print(f"  HTTP {e.code} for {url}")
            return {}
        except Exception as e:  # noqa: BLE001
            print(f"  error: {e}")
            time.sleep(2)
    return {}


def denied(title: str) -> bool:
    low = title.lower()
    return any(bad in low for bad in TITLE_DENYLIST)


# --- Wikimedia queries --------------------------------------------------------

def _api(params: dict) -> dict:
    params = {**params, "format": "json"}
    return _http_json(API + "?" + urllib.parse.urlencode(params))


def category_files(cat: str) -> list:
    data = _api({
        "action": "query", "list": "categorymembers",
        "cmtitle": f"Category:{cat}", "cmtype": "file", "cmlimit": 200,
    })
    return (data.get("query") or {}).get("categorymembers") or []


def image_details(titles: list) -> dict:
    """Return {title: {"ii": imageinfo, "cats": [category names]}}."""
    result = {}
    for i in range(0, len(titles), 50):
        chunk = titles[i:i + 50]
        data = _api({
            "action": "query", "titles": "|".join(chunk),
            "prop": "imageinfo|categories",
            "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": THUMB_WIDTH, "cllimit": "max",
        })
        pages = (data.get("query") or {}).get("pages") or {}
        for p in pages.values():
            ii = (p.get("imageinfo") or [None])[0]
            if ii:
                cats = [c.get("title", "").replace("Category:", "")
                        for c in (p.get("categories") or [])]
                result[p["title"]] = {"ii": ii, "cats": cats}
        time.sleep(0.3)
    return result


# --- Photo screening ----------------------------------------------------------

def _bad_categories(cats: list) -> bool:
    """True if the categories mark the file as artwork, a scan, a map, etc.
    rather than a real scene photo."""
    blob = " | ".join(cats).lower()
    return any(bad in blob for bad in CATEGORY_DENYLIST)


def _nonphoto_metadata(ii: dict) -> bool:
    """True if a file's description/credit gives it away as a non-photo:
    scanned-book / archive provenance (the root cause) or explicit art/document
    words. Commons routinely leaves the title and categories clean while the real
    story sits in the description/credit, so that is where the signal lives."""
    meta = ii.get("extmetadata") or {}
    blob = " ".join(
        _clean((meta.get(field) or {}).get("value", ""))
        for field in ("ImageDescription", "Credit", "ObjectName", "Artist")
    ).lower()
    return (any(marker in blob for marker in SCAN_PROVENANCE_MARKERS)
            or any(term in blob for term in DESCRIPTION_DENY_TERMS))


def is_quality(title: str, ii: dict, cats: list) -> bool:
    """Keep only real raster photos: decent resolution, roughly landscape (not a
    tall document scan), and not flagged as artwork or a document by title,
    category or the file's own description/credit."""
    if denied(title) or _bad_categories(cats) or _nonphoto_metadata(ii):
        return False
    if ii.get("mime") not in ALLOWED_MIME:
        return False
    width = int(ii.get("width", 0))
    height = int(ii.get("height", 0))
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        return False
    ratio = width / height if height else 0
    if not (MIN_RATIO <= ratio <= MAX_RATIO):
        return False
    return bool(ii.get("thumburl"))


def _is_scene(cats: list) -> bool:
    """True if the file's categories place it in an outdoor, locatable scene (a
    street, a landmark, a view) and it isn't a people-subject photo (players,
    musicians) that merely happens to be tagged with its venue."""
    blob = " | ".join(cats).lower()
    if any(hint in blob for hint in PEOPLE_SUBJECT_HINTS):
        return False
    return any(hint in blob for hint in SCENE_CATEGORY_HINTS)


def find_photo(city: str) -> dict | None:
    """A quality, guessable scene photo from a Wikimedia '<year> in <city>'
    category for a random year. The location must be guessable, so a scene
    category (street / landmark / view) is required - a quality photo that isn't
    a locatable scene (an interior, an object close-up, a portrait) is skipped,
    even if that means this city+year yields nothing and we try another.
    Returns a find dict with the image URL + year + attribution, or None."""
    year = random.randint(YEAR_MIN, YEAR_MAX)
    members = category_files(f"{year} in {city}")
    titles = [m["title"] for m in members
              if m["title"].lower().endswith(IMG_EXT) and not denied(m["title"])]
    if not titles:
        return None

    random.shuffle(titles)
    details = image_details(titles[:50])

    scene = [(t, details[t]) for t in titles
             if t in details
             and is_quality(t, details[t]["ii"], details[t]["cats"])
             and _is_scene(details[t]["cats"])]
    if not scene:
        return None
    title, entry = scene[0]

    ii = entry["ii"]
    meta = ii.get("extmetadata") or {}
    return {
        "thumb_url": ii["thumburl"],
        "year": year,
        "title": title.replace("File:", "").strip(),
        "description": _clean((meta.get("ImageDescription") or {}).get("value", ""))[:280],
        "author": _clean((meta.get("Artist") or {}).get("value", "")) or "Unknown",
        "license": _clean((meta.get("LicenseShortName") or {}).get("value", "")) or "See source",
        "licenseUrl": (meta.get("LicenseUrl") or {}).get("value", ""),
        "sourceUrl": ii.get("descriptionurl", ""),
    }


# --- Geocoding (cached) -------------------------------------------------------

def load_coords_cache() -> dict:
    try:
        obj = s3.get_object(Bucket=PUZZLE_BUCKET, Key=COORDS_KEY)
        return json.loads(obj["Body"].read().decode())
    except Exception:
        return {}


def save_coords_cache(cache: dict) -> None:
    s3.put_object(
        Bucket=PUZZLE_BUCKET, Key=COORDS_KEY,
        Body=json.dumps(cache, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )


def geocode_city(city: str, cache: dict) -> list | None:
    """City-centre coordinates via OpenStreetMap, cached. City-level accuracy
    is all the game needs."""
    if city in cache:
        return cache[city]
    params = urllib.parse.urlencode({"q": city, "format": "json", "limit": 1})
    try:
        req = urllib.request.Request(f"{NOMINATIM}?{params}",
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as resp:
            results = json.loads(resp.read().decode())
    except Exception as e:  # noqa: BLE001
        print(f"  geocode error for {city!r}: {e}")
        results = None
    time.sleep(1.2)  # respect Nominatim's <=1 req/sec policy
    if results:
        try:
            coords = [round(float(results[0]["lat"]), 4),
                      round(float(results[0]["lon"]), 4)]
            cache[city] = coords
            return coords
        except (KeyError, IndexError, ValueError, TypeError):
            pass
    return None


# --- Image re-hosting ---------------------------------------------------------

def rehost_image(src_url: str, key: str) -> str:
    """Copy a source image into our S3 bucket and return the origin-relative path
    the game loads. CloudFront serves the page, API and images under one domain,
    so "/images/..." resolves to the same origin as the page - no source URL (or
    account ID) is ever exposed to players. Raises if the URL isn't an image so
    the caller can move on."""
    req = urllib.request.Request(src_url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as resp:
        content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        data = resp.read()
    if content_type not in ALLOWED_MIME:
        low = src_url.lower()
        if low.endswith(".png"):
            content_type = "image/png"
        elif low.endswith((".jpg", ".jpeg")):
            content_type = "image/jpeg"
        else:
            raise ValueError(f"not an image ({content_type or 'unknown type'})")
    s3.put_object(
        Bucket=PUZZLE_BUCKET, Key=key, Body=data,
        ContentType=content_type, CacheControl="public, max-age=31536000",
    )
    return f"/{key}"


# --- Build --------------------------------------------------------------------

def handler(event, context):
    date_str = datetime.now(timezone.utc).date().isoformat()
    coords_cache = load_coords_cache()

    rounds = []
    used_cities: set = set()
    used_years: set = set()
    attempts = 0
    while len(rounds) < ROUNDS_PER_DAY and attempts < MAX_ATTEMPTS:
        attempts += 1
        city = random.choice(CITIES)
        if city in used_cities:
            continue

        try:
            find = find_photo(city)
        except Exception as e:  # noqa: BLE001
            print(f"  find_photo error for {city!r}: {e}")
            continue
        if not find or find["year"] in used_years:
            continue

        coords = geocode_city(city, coords_cache)
        if not coords:
            continue

        image_key = f"images/{date_str}/{len(rounds)}.jpg"
        try:
            image_url = rehost_image(find["thumb_url"], image_key)
        except Exception as e:  # noqa: BLE001
            print(f"  rehost failed for {find.get('sourceUrl')!r}: {e}")
            continue

        rounds.append({
            "imageUrl": image_url,
            "year": find["year"],
            "lat": coords[0],
            "lon": coords[1],
            "place": city,
            "title": find["title"],
            "description": find["description"],
            "author": find["author"],
            "license": find["license"],
            "licenseUrl": find["licenseUrl"],
            "sourceUrl": find["sourceUrl"],
        })
        used_cities.add(city)
        used_years.add(find["year"])
        print(f"  round {len(rounds)}: {city} {find['year']}")

    save_coords_cache(coords_cache)

    if len(rounds) < ROUNDS_PER_DAY:
        msg = f"Only found {len(rounds)}/{ROUNDS_PER_DAY} photos in {attempts} attempts"
        print(msg)
        if not rounds:
            return {"statusCode": 500, "body": "No photos found; keeping existing puzzle"}

    puzzle = {
        "puzzleId": date_str,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "rounds": rounds,
    }
    body = json.dumps(puzzle, ensure_ascii=False).encode("utf-8")
    for key in (f"puzzle/{date_str}.json", CURRENT_KEY):
        s3.put_object(Bucket=PUZZLE_BUCKET, Key=key, Body=body,
                      ContentType="application/json")

    result = f"Built puzzle {date_str} with {len(rounds)} rounds in {attempts} attempts."
    print(result)
    return {"statusCode": 200, "body": result}
