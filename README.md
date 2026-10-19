# The Atlas 🗞️

**A daily "guess when and where this photo was taken" game, dressed as a newspaper.**

**Play at [theatlasnewspaper.co.uk](https://theatlasnewspaper.co.uk)**

Every day The Atlas prints five historical photographs. For each, you place a pin
on a world map for *where* it was taken and slide to a year for *when*. The closer
you are on both, the higher you score - up to 10,000 a round, 50,000 for the day.
The whole thing reads like a single front page that grows as you play.

---

## The game

Each round is an article with its own headline. You study the photograph (pan and
zoom to look for clues), drop your pin, and pick a year. Submit, and the article's
verdict appears in place: your location and year scores, how many kilometres and
how many years off you were, and the photo's real title and credit. Read it, then
move on - the next article stacks beneath it and the page scrolls down, so by the
end you've "read" the full edition top to bottom.

A team enters its name once (remembered on the device from then on). Scores are
saved per team, and each team can look back over its **Past Editions** - its ten
best days, with the edition number and date - plus a chart of how its scores are
spread. There's no global leaderboard by design: it's a shared game for one group,
not a competition against strangers.

---

## Design: it's a newspaper

The entire interface is built around the conceit of a daily broadsheet, which
turned out to be a natural fit for a "date and place" game:

- A **blackletter nameplate** ("The Atlas") over a Scotch rule, with a folio line
  carrying the establishment year, the day's date, and an **edition number** that
  increments by one each day - so "No. 412" quietly tells you the paper is 412 days
  old. The browser tab icon is the nameplate's own "A" on the same paper stock.
- Each round is typeset as an **article** - overline, serif headline, and a byline
  ("From the Picture Desk", "Filed from parts unknown") - and once you've guessed,
  the headline becomes the photo's real title.
- Warm paper, hairline rules, a ruler-style year slider, and a closing **"Final
  Tally"** set like the back-page results. The palette and type (a blackletter
  face, a serif, and a newspaper body serif) do the work; there are no gradients
  or glassy cards.

---

## Architecture

Fully **serverless and scale-to-zero** on AWS, behind a CDN on a custom domain.
The page, the API and the images all sit behind **one CloudFront distribution**, so
they share a single origin - no CORS, and no bucket URLs or account details are
ever exposed to players.

```
                         Browser ──HTTPS──► theatlasnewspaper.co.uk
                                                   │
                                        ┌──────────▼──────────┐
                                        │      CloudFront      │  CDN · TLS (ACM) · one origin
                                        └──────────┬──────────┘   reads S3 privately via OAC
                      /  ┌────────────────┬────────┴───────────┐  /api/*
                         ▼                ▼ /images/*           ▼
                   S3  web/          S3  images/          API Gateway (HTTP API)
                 (frontend)          (the photos)               │
                                                                ▼
                                                   Lambda (game API) ──► DynamoDB (team scores)
                                                          └──────────► S3 puzzle/ (answers, private)

   EventBridge ──00:00 UTC──► Lambda (daily builder)
                                   └─ Wikimedia Commons · OpenStreetMap · Amazon Rekognition
                                          └──────────────────────────► S3 puzzle/ + images/
```

**Stack:** Python on AWS Lambda · API Gateway (HTTP API) · DynamoDB · S3 ·
CloudFront + Route 53 + ACM (custom domain over HTTPS) · Amazon Rekognition ·
EventBridge · AWS SAM for infrastructure-as-code · a single vanilla HTML/CSS/JS
page · Leaflet for the map. Region is `eu-west-2`; the TLS certificate lives in
`us-east-1` because CloudFront requires it there.

The S3 bucket is **fully private** - nothing is public. CloudFront reaches it
through Origin Access Control, and the bucket policy only grants access to the
frontend (`web/`) and the photos (`images/`). The puzzle files - which contain the
answers - are never granted and have no CloudFront route, so they're unreachable
from the web.

---

## Building a fair puzzle (the interesting part)

Anyone can pull images off Wikimedia; the real work was making them *guessable*.

Photos come from Commons' `"<year> in <city>"` category tree:

> `Category:1965 in Paris` → year = **1965**, place = **Paris**

The year comes straight from the category name and the location is the city, so
every photo arrives with both facts attached - no EXIF guesswork. Coordinates are
geocoded once via OpenStreetMap and cached, so widening the pool is just adding a
city name to a list. (Years run from 1900 to the present.)

But a naive scrape of those categories returns maps, logos, documents, paintings,
studio portraits, scanned book plates and backyards - most of which are either not
photographs or impossible to locate. Fixing that took **two stages**:

**1. A cheap metadata screen.** Reject anything that isn't a real landscape-ish
photograph of a decent size, using the file's Commons categories and description.
The reliable trick here: nearly all the non-photos (paintings, engravings, scanned
plates) arrive through a handful of book-digitisation pipelines that leave a
recognisable signature in the file's metadata - so screening on the *source*
catches the whole class at once, rather than trying to name every art form.

**2. A vision gate.** Metadata alone can't tell a photographed street from a
photographed painting, or a recognisable scene from a generic backyard - so each
surviving candidate is passed to **Amazon Rekognition**, which looks at the actual
pixels. A photo is only used if it:

- reads as a **built scene**, not artwork;
- isn't a **captioned postcard** - detected when the printed text restates the
  file's own title (how a postcard gives itself away), while an incidental shop
  sign or a slogan on a shirt won't match the title and is left alone; and
- carries a **guessability cue**: readable signage, a non-Latin writing system
  (Cyrillic, Arabic, CJK...), a flag or billboard, distinctive transport (a tram,
  a double-decker, a tuk-tuk), or a skyline.

That mirrors how a person actually plays - *can I read a sign, spot a flag, place
the architecture?* The number of images sent to Rekognition per build is
**hard-capped**, with a fallback to the metadata screen if the cap is hit, so the
cost is bounded and a puzzle is always produced.

Every chosen image is then **copied into the project's own S3 bucket** rather than
hot-linked, so the game keeps working even if Wikimedia is unreachable.

---

## Decisions I'm happy with

- **Server-authoritative scoring.** Answers never reach the browser until a round
  is guessed - the "today" endpoint returns only image URLs, and the Lambda scores
  each guess. You can't open the network tab and read off the day's answers.

- **One puzzle a day, built ahead of time.** A scheduled Lambda assembles the five
  rounds at midnight UTC and drops them in S3; the game just serves whatever's
  current. That means instant loads, no per-request calls to Wikimedia, and the
  same five photos for everyone so a team's scores are comparable day to day.

- **A vision gate for quality, not just keywords.** Keyword/category filters hit a
  ceiling - they can't see that a photo is a painting or an unguessable backyard.
  Adding an image-analysis pass (Rekognition) that scores for real location cues
  was the step that made the daily puzzle genuinely playable, and capping its usage
  kept it effectively free.

- **One origin behind a CDN, private bucket.** Serving the page, API and images
  through a single CloudFront distribution removed CORS entirely and let me lock
  the S3 bucket down completely - players only ever talk to the CDN.

- **First-write-wins team scores.** A team's score for an edition is locked in the
  moment it's filed - a conditional DynamoDB write rejects any later attempt - so
  you can't replay the day to beat your own number.

- **Pragmatic map tiles.** OpenStreetMap's tiles are detailed but block app use
  from a `file://` origin, so the page serves OSM when deployed and falls back to a
  keyless Esri basemap locally - the map works whether you've deployed or are just
  opening the HTML file.

---

## Cost

Effectively free to run. Compute (Lambda), the API, DynamoDB and S3 all bill per
request and idle at zero; CloudFront and Rekognition sit inside their free tiers at
this volume (and Rekognition is capped regardless). The only standing costs are the
**domain registration** and about **$0.50/month** for the Route 53 hosted zone.

---

Built by [Liam Wilcox](https://github.com/LW-98).
