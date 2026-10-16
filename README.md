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
spread. There's no global leaderboard by design: it's a shared game for one team,
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

Fully **serverless and scale-to-zero** on AWS - it costs effectively nothing when
no one's playing and sits inside the free tier.

```
  Browser  ──HTTPS──►  API Gateway  ──►  Lambda (Python)  ──►  DynamoDB
 (static HTML)          (HTTP API)        scoring + game         team scores
                                              │
                                              └──►  S3  (today's puzzle + images)
                                                        ▲
  EventBridge (daily, 00:00 UTC)  ──►  Lambda  ──┘  builds the day's 5-photo puzzle
                                               from Wikimedia Commons
```

**Stack:** Python on AWS Lambda · API Gateway (HTTP API) · DynamoDB · S3 ·
EventBridge · AWS SAM for infrastructure-as-code · a single vanilla HTML/CSS/JS
page · Leaflet for the map.

---

## Where do the photos come from?

Images come from Wikimedia Commons' `"<year> in <city>"` category tree:

> `Category:1965 in Paris` → year = **1965**, place = **Paris**

The year comes straight from the category name and the location is the city, so
every photo arrives with both facts attached and no EXIF guesswork. Everything on
Commons is freely licensed, and the city coordinates are geocoded once via
OpenStreetMap and cached, so widening the pool is just adding a city name to a list.

Getting *good* rounds was its own problem. A naive scrape returns maps, logos,
documents, and - the sneaky ones - paintings and studio portraits that sail past a
title check. The builder reads each file's Commons **categories** and rejects
artwork, drawings, portraits, postcards (which often print the place name right on
the image) and other non-scene files, then checks resolution and a landscape-ish
aspect ratio. Only real, locatable photographs survive.

---

## Decisions I'm happy with

- **Server-authoritative scoring.** Answers never reach the browser until a round
  is guessed - the "today" endpoint returns only image URLs, and the Lambda scores
  each guess. You can't open the network tab and read off the day's answers.

- **One puzzle a day, built ahead of time.** A scheduled Lambda assembles the five
  rounds at midnight UTC and drops them in S3; the game just serves whatever's current.
  That means instant loads, no per-request calls to Wikimedia, and the same five
  photos for everyone so a team's scores are comparable day to day.

- **First-write-wins team scores.** A team's score for an edition is locked in the
  moment it's filed - a conditional DynamoDB write rejects any later attempt - so
  you can't replay the day to beat your own number. Each playthrough carries a
  unique id, and an edition is only ever filed once.

- **Pragmatic map tiles.** OpenStreetMap's tiles are detailed but block app use
  from a `file://` origin, so the page serves OSM when deployed and falls back to a
  keyless Esri basemap locally - the map works whether you've deployed or are just
  opening the HTML file.

- **Genuinely free to run.** No always-on anything - no NAT gateway, no provisioned
  capacity. Every component bills per request and idles at $0.

---

## Running it

It's an AWS SAM application - `sam build && sam deploy` stands up the API, the two
Lambdas, the table and the bucket - with a single static HTML page for the
frontend. Open that page straight from disk and it runs offline against a bundled
sample puzzle, which is how I developed it without touching AWS.

---

Built by [Liam Wilcox](https://github.com/LW-98).
