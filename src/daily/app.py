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
photo has a known year and city without relying on sparse EXIF data. Selection
is two-stage:

  1. A cheap metadata screen keeps only real photos that sit in a scene category
     (Streets, Buildings, Views, ...) - fast, free, and it drops paintings,
     scanned documents and obvious non-scenes.
  2. A vision gate (Amazon Rekognition) then looks at the actual pixels and
     keeps a photo only if it reads as a built scene AND carries a guessability
     cue - readable signage/text, a flag/billboard, or a distinctive skyline.
     That's the "can I tell where this is" test a person applies. The number of
     images sent to Rekognition per run is hard-capped (VisionBudgetPerBuild) so
     the cost is bounded; if the cap is hit, the remaining rounds fall back to
     the stage-1 screen so a puzzle is always produced.

City coordinates are looked up once via OpenStreetMap and cached, so the answer
location is the city centre; adding a city is just a name in cities.py. Each
photo keeps its attribution, shown on the reveal, per its licence.
"""

import json
import os
import random
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import boto3

from cities import CITIES

PUZZLE_BUCKET = os.environ["PUZZLE_BUCKET"]
REGION = os.environ.get("AWS_REGION", "eu-west-2")
ROUNDS_PER_DAY = int(os.environ.get("ROUNDS_PER_DAY", "5"))
# Hard cap on images sent to Rekognition per build (cost ceiling). 0 disables
# the vision gate entirely and falls back to the metadata screen only.
VISION_BUDGET = int(os.environ.get("MAX_VISION_IMAGES", "30"))

API = "https://commons.wikimedia.org/w/api.php"
UA = "DailyTimeGuess/1.0 (https://github.com/LW-98; daily puzzle builder)"
NOMINATIM = "https://nominatim.openstreetmap.org/search"

IMG_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff")
ALLOWED_MIME = {"image/jpeg", "image/png", "image/tiff"}
# Rekognition accepts JPEG/PNG bytes only, up to 5 MB; our 1280px thumbs fit.
REKOGNITION_MIME = {"image/jpeg", "image/png"}

YEAR_MIN = 1900
YEAR_MAX = datetime.now(timezone.utc).year  # through the current year
THUMB_WIDTH = 1280
MIN_WIDTH, MIN_HEIGHT = 1000, 700
MIN_RATIO, MAX_RATIO = 0.5, 2.2          # landscape-ish; filters tall documents
MAX_ATTEMPTS = 400
CANDIDATES_PER_CITY = 5                   # scene photos to vision-test per city

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
# its title or categories. Kept deliberately specific so real photo captions
# ("portrait orientation", "a road map on the wall") don't trip it.
DESCRIPTION_DENY_TERMS = (
    "painting", "watercolour", "watercolor", "aquarelle", "gouache",
    "oil on canvas", "tempera", "pastel drawing", "lithograph",
    "chromolithograph", "oleograph", "engraving", "etching", "aquatint",
    "mezzotint", "woodcut", "wood engraving", "steel engraving",
    "illustrated by", "illus. in colour", "illus. in color", "colour plate",
    "color plate", "frontispiece", "fresco", "mural painting", "drawing of",
    "print of", "sketch of",
    "travelers in the middle east",
    "bylaws", "by-laws", "title page", "book cover", "pamphlet", "leaflet",
)

# Scanned-book / archive bulk-upload signatures - the source almost all
# non-photos come through (see README). Catching the source catches the class.
SCAN_PROVENANCE_MARKERS = (
    "find matches", "identifier :", "identifier:",
    "book contributor", "digitizing sponsor", "digitized by", "digitised by",
    "archive.org/details", "archive.org/stream", "bub_gb_", "books.google",
    "travelers in the middle east", "from the book", "plate from", "scanned from",
)

# Stage-1 (cheap) guessability hint: a file that also sits in one of these scene
# categories shows an outdoor, locatable place. This narrows candidates before
# the (capped) vision gate - it is not the final word on guessability.
SCENE_CATEGORY_HINTS = (
    "street", "avenue", "boulevard", "road", "square", "plaza", "piazza",
    "building", "architecture", "skyline", "cityscape", "panorama", "aerial",
    "view of", "views of", "harbour", "harbor", "port", "waterfront", "quay",
    "bridge", "castle", "palace", "church", "cathedral", "basilica", "chapel",
    "mosque", "temple", "synagogue", "tower", "monument", "memorial", "gate",
    "market", "fountain", "station", "railway", "stadium", "park", "garden",
    "town hall", "city hall", "landmark", "promenade", "waterfall",
)

# Performer-as-subject categories veto a candidate even with a scene category:
# a concert-hall photo is really a portrait of the band, not a place. Sport is
# deliberately NOT here - a player's kit is a useful location clue.
PEOPLE_SUBJECT_HINTS = (
    "musicians", "singers", "guitarists", "bands", "orchestras", "choirs",
    "actors", "actresses", "dancers", "performers",
)

# --- Vision gate (Rekognition) label sets -------------------------------------
# These decide the final, pixel-level verdict. Confidence thresholds below.
ART_LABELS = {"art", "painting", "drawing", "sketch", "illustration",
              "collage", "mural", "modern art", "rock art", "postcard"}
URBAN_LABELS = {"city", "urban", "street", "road", "building", "architecture",
                "downtown", "town", "neighborhood", "neighbourhood", "metropolis",
                "cityscape", "alley", "intersection", "bus stop", "sidewalk",
                "pavement", "plaza", "town square", "waterfront", "harbor",
                "harbour", "port", "pier", "marina", "train station", "railway"}
# Distinctive skyline markers - low false-positive. Deliberately NOT generic
# building-type labels ("office building", "parliament", "monastery", ...):
# Rekognition misfires on those (it called a Breslau postcard "Parliament" and a
# Turin backyard "Monastery"), which would re-admit the generic scenes we cut.
SKYLINE_LABELS = {"skyscraper", "high rise", "metropolis", "skyline", "tower block"}
# A distant, overhead or scenic view. The skyline labels above also fire on a
# hazy aerial sprawl with no eye-level landmark (a far hilltop panorama), which
# isn't guessable - these labels are the tell, so they veto the skyline cue. A
# genuine eye-level skyline (e.g. Dubai) carries none of them.
AERIAL_LABELS = {"aerial view", "landscape", "scenery", "satellite imagery",
                 "bird's-eye view", "panoramic"}
# Visible man-made cues that locate a photo in space/time.
CUE_LABELS = {"flag", "billboard", "poster", "advertisement", "banner",
              "sign", "signage", "neon", "license plate", "number plate"}
# Regionally distinctive transport - a strong "where" cue (a red double-decker,
# a tram, a tuk-tuk, a gondola pins a place fast).
TRANSPORT_LABELS = {"tram", "streetcar", "trolley", "double decker bus",
                    "rickshaw", "tuk tuk", "gondola", "cable car"}

# Generic words stripped from a title before the captioned-postcard check, so an
# incidental city-name sign (fine - part of the game) doesn't trip it; only a
# caption restating the title's distinctive words (a postcard) does.
TITLE_STOPWORDS = {
    "street", "avenue", "boulevard", "road", "lane", "drive", "place", "square",
    "city", "town", "view", "views", "panorama", "general", "old", "new",
    "building", "buildings", "house", "hotel", "cafe", "restaurant", "church",
    "cathedral", "basilica", "chapel", "mosque", "temple", "station", "bridge",
    "park", "garden", "palace", "castle", "museum", "tower", "hall", "market",
    "centre", "center", "district", "quarter", "from", "with", "near", "the",
    "and", "north", "south", "east", "west", "rue", "strasse", "gasse",
    "platz", "plaza", "piazza", "calle",
}

LABEL_CONF = 80.0      # a label counts as present at or above this confidence
ART_CONF = 90.0        # an art verdict needs high confidence to veto
TEXT_CONF = 80.0       # a detected text line counts at or above this
TEXT_MIN_LINES = 2     # this many readable lines = meaningful signage
TITLE_OVERLAP_MIN = 2  # this many distinctive title words read off the image = a caption

s3 = boto3.client("s3")
rekognition = boto3.client("rekognition")


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


# --- Stage 1: cheap metadata screen -------------------------------------------

def _bad_categories(cats: list) -> bool:
    blob = " | ".join(cats).lower()
    return any(bad in blob for bad in CATEGORY_DENYLIST)


def _nonphoto_metadata(ii: dict) -> bool:
    """Scanned-book provenance or explicit art/document words in the file's
    description/credit - the most reliable non-photo signal on Commons."""
    meta = ii.get("extmetadata") or {}
    blob = " ".join(
        _clean((meta.get(field) or {}).get("value", ""))
        for field in ("ImageDescription", "Credit", "ObjectName", "Artist")
    ).lower()
    return (any(marker in blob for marker in SCAN_PROVENANCE_MARKERS)
            or any(term in blob for term in DESCRIPTION_DENY_TERMS))


def is_quality(title: str, ii: dict, cats: list) -> bool:
    """A real raster photo: decent resolution, roughly landscape, not flagged as
    artwork/document by title, category or description."""
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
    blob = " | ".join(cats).lower()
    if any(hint in blob for hint in PEOPLE_SUBJECT_HINTS):
        return False
    return any(hint in blob for hint in SCENE_CATEGORY_HINTS)


def scene_candidates(city: str):
    """Stage 1. Return (year, [candidate, ...]) - quality scene photos from a
    '<year> in <city>' category for a random year, ready for the vision gate."""
    year = random.randint(YEAR_MIN, YEAR_MAX)
    members = category_files(f"{year} in {city}")
    titles = [m["title"] for m in members
              if m["title"].lower().endswith(IMG_EXT) and not denied(m["title"])]
    if not titles:
        return year, []

    random.shuffle(titles)
    details = image_details(titles[:50])
    out = []
    for title in titles:
        entry = details.get(title)
        if not entry or not is_quality(title, entry["ii"], entry["cats"]):
            continue
        if not _is_scene(entry["cats"]):
            continue
        ii = entry["ii"]
        meta = ii.get("extmetadata") or {}
        out.append({
            "thumb_url": ii["thumburl"],
            "title": title.replace("File:", "").strip(),
            "description": _clean((meta.get("ImageDescription") or {}).get("value", ""))[:280],
            "author": _clean((meta.get("Artist") or {}).get("value", "")) or "Unknown",
            "license": _clean((meta.get("LicenseShortName") or {}).get("value", "")) or "See source",
            "licenseUrl": (meta.get("LicenseUrl") or {}).get("value", ""),
            "sourceUrl": ii.get("descriptionurl", ""),
        })
        if len(out) >= CANDIDATES_PER_CITY:
            break
    return year, out


# --- Stage 2: vision gate -----------------------------------------------------

def _ascii_tokens(text: str) -> set:
    """Lowercase alphabetic words (>=4 letters), accents folded, for comparing a
    file's title against the text printed in the image."""
    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return set(re.findall(r"[a-z]{4,}", folded.lower()))


def _has_non_latin_script(text: str) -> bool:
    """True if the text contains a non-Latin writing system - a strong location
    cue (Cyrillic, Arabic, Greek, Hebrew, Thai, Devanagari, kana/CJK, Hangul)."""
    for ch in text or "":
        o = ord(ch)
        if (0x0400 <= o <= 0x04FF or 0x0600 <= o <= 0x06FF or 0x0370 <= o <= 0x03FF
                or 0x0590 <= o <= 0x05FF or 0x0E00 <= o <= 0x0E7F
                or 0x0900 <= o <= 0x097F or 0x3040 <= o <= 0x30FF
                or 0x4E00 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7AF):
            return True
    return False


def vision_guessable(image_bytes: bytes, title: str, place: str) -> tuple[bool, str]:
    """Look at the pixels and decide if this is a guessable, genuine photograph
    of a place. It must be a built scene, not artwork, and not a captioned
    postcard (whose printed caption restates the file's own title - the tell
    that it's a postcard, whereas an incidental sign or shirt won't match the
    title). It then needs a locating cue: readable signage, a non-Latin writing
    system, a flag/billboard, distinctive transport, or a skyline. Returns
    (ok, reason). Fails open on a Rekognition error so a transient fault can't
    stall the whole build."""
    try:
        labels_resp = rekognition.detect_labels(
            Image={"Bytes": image_bytes}, MaxLabels=20, MinConfidence=70)
        text_resp = rekognition.detect_text(Image={"Bytes": image_bytes})
    except Exception as e:  # noqa: BLE001
        print(f"  rekognition error (accepting): {e}")
        return True, "vision-error"

    labels = {l["Name"].lower(): l["Confidence"] for l in labels_resp.get("Labels", [])}

    def has(names, conf=LABEL_CONF):
        return any(labels.get(n, 0) >= conf for n in names)

    if has(ART_LABELS, ART_CONF):
        return False, "reads as artwork"
    if not (has(URBAN_LABELS) or has(SKYLINE_LABELS)):
        return False, "not a built scene"

    text_lines = [d["DetectedText"] for d in text_resp.get("TextDetections", [])
                  if d.get("Type") == "LINE" and d.get("Confidence", 0) >= 70]
    ocr_text = " ".join(text_lines)

    # Captioned postcard/plate: the printed text restates the file's own title.
    # Incidental text (a shop sign, a shirt, an ad) won't match the title, so one
    # shared word is allowed; two distinctive ones means it's a caption, not a
    # candid photograph.
    title_words = (_ascii_tokens(title) | _ascii_tokens(place)) - TITLE_STOPWORDS
    if len(title_words & _ascii_tokens(ocr_text)) >= TITLE_OVERLAP_MIN:
        return False, "captioned postcard (text restates title)"

    strong_lines = sum(1 for d in text_resp.get("TextDetections", [])
                       if d.get("Type") == "LINE"
                       and d.get("Confidence", 0) >= TEXT_CONF
                       and len(d.get("DetectedText", "").strip()) >= 2)
    if strong_lines >= TEXT_MIN_LINES:
        return True, f"signage ({strong_lines} lines)"
    if _has_non_latin_script(ocr_text):
        return True, "non-Latin script"
    if has(CUE_LABELS):
        return True, "flag/billboard/sign"
    if has(TRANSPORT_LABELS):
        return True, "distinctive transport"
    if has(SKYLINE_LABELS) and not has(AERIAL_LABELS):
        return True, "skyline"
    return False, "no guessability cue"


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
    """City-centre coordinates via OpenStreetMap, cached."""
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


# --- Image fetch / store ------------------------------------------------------

def fetch_image_bytes(src_url: str) -> tuple[bytes, str]:
    """Download an image, returning (bytes, content_type). Raises if it isn't an
    image (some sources hand back landing pages)."""
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
    return data, content_type


def store_image(data: bytes, content_type: str, key: str) -> str:
    """Save image bytes to S3 and return the origin-relative path the game loads
    (served via CloudFront, so no bucket URL is exposed to players)."""
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
    vision_used = 0  # images sent to Rekognition this build (hard-capped)

    while len(rounds) < ROUNDS_PER_DAY and attempts < MAX_ATTEMPTS:
        attempts += 1
        city = random.choice(CITIES)
        if city in used_cities:
            continue
        coords = geocode_city(city, coords_cache)
        if not coords:
            continue

        try:
            year, candidates = scene_candidates(city)
        except Exception as e:  # noqa: BLE001
            print(f"  scene_candidates error for {city!r}: {e}")
            continue
        if not candidates or year in used_years:
            continue

        chosen = None
        for cand in candidates:
            try:
                data, content_type = fetch_image_bytes(cand["thumb_url"])
            except Exception as e:  # noqa: BLE001
                print(f"  fetch failed for {cand.get('sourceUrl')!r}: {e}")
                continue

            # Vision gate, within the per-build budget. Once the cap is hit we
            # fall back to the stage-1 screen so the puzzle still completes.
            if vision_used < VISION_BUDGET and content_type in REKOGNITION_MIME:
                vision_used += 1
                ok, reason = vision_guessable(data, cand["title"], city)
                if not ok:
                    print(f"  skip {city} {year}: {reason}")
                    continue
            chosen = (cand, data, content_type)
            break

        if not chosen:
            continue
        cand, data, content_type = chosen

        image_key = f"images/{date_str}/{len(rounds)}.jpg"
        try:
            image_url = store_image(data, content_type, image_key)
        except Exception as e:  # noqa: BLE001
            print(f"  store failed for {cand.get('sourceUrl')!r}: {e}")
            continue

        rounds.append({
            "imageUrl": image_url,
            "year": year,
            "lat": coords[0],
            "lon": coords[1],
            "place": city,
            "title": cand["title"],
            "description": cand["description"],
            "author": cand["author"],
            "license": cand["license"],
            "licenseUrl": cand["licenseUrl"],
            "sourceUrl": cand["sourceUrl"],
        })
        used_cities.add(city)
        used_years.add(year)
        print(f"  round {len(rounds)}: {city} {year}")

    save_coords_cache(coords_cache)
    print(f"  vision images used: {vision_used}/{VISION_BUDGET}")

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
