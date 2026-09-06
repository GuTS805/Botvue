"""Builds the scan list.

Sites publishing an llms.txt have already decided how they want AI crawlers treated, which
makes them both the highest-yield pool and the only pool where a declared policy can be
compared against what is actually served. The curated seeds cover the segments that pool
misses: publishers monetising AI traffic, shopping surfaces an agent would consult, and the
package-documentation sites that SEO-poisoning campaigns imitate.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

CORPUS_DIR = Path(__file__).resolve().parents[1] / "corpus"
LLMSTXT_INDEX = "https://llmstxt.site/"
_LLMS_DOMAIN = re.compile(r"https?://([a-z0-9\-]+\.[a-z0-9.\-]+)/llms\.txt")

PUBLISHERS = [
    # news and general interest
    "time.com", "fortune.com", "theatlantic.com", "businessinsider.com", "axios.com",
    "vox.com", "theverge.com", "wired.com", "forbes.com", "newsweek.com",
    "usatoday.com", "nypost.com", "people.com", "sciencealert.com", "sfgate.com",
    "thedailybeast.com", "salon.com", "rollingstone.com", "variety.com", "cnet.com",
    "zdnet.com", "techcrunch.com", "engadget.com", "mashable.com", "gizmodo.com",
    "arstechnica.com", "thehill.com", "politico.com", "semafor.com", "theguardian.com",
    "npr.org", "cbsnews.com", "nbcnews.com", "latimes.com", "chicagotribune.com",
    "bostonglobe.com", "seattletimes.com", "denverpost.com", "mercurynews.com",
    "miamiherald.com", "dallasnews.com", "houstonchronicle.com", "azcentral.com",
    "oregonlive.com", "cleveland.com", "nj.com", "al.com", "pennlive.com",
    "reuters.com", "apnews.com", "bbc.com", "aljazeera.com", "dw.com", "france24.com",
    # magazines and long form
    "newyorker.com", "vanityfair.com", "gq.com", "vogue.com", "esquire.com",
    "harpersbazaar.com", "elle.com", "cosmopolitan.com", "menshealth.com",
    "womenshealthmag.com", "runnersworld.com", "bicycling.com", "popularmechanics.com",
    "popsci.com", "sciencenews.org", "smithsonianmag.com", "discovermagazine.com",
    # business and finance
    "cnbc.com", "marketwatch.com", "investopedia.com", "fool.com", "benzinga.com",
    "thestreet.com", "kiplinger.com", "morningstar.com", "inc.com", "entrepreneur.com",
    "fastcompany.com", "hbr.org", "economist.com", "ft.com",
    # sport
    "espn.com", "cbssports.com", "si.com", "bleacherreport.com", "sbnation.com",
    "golfdigest.com", "mlb.com", "nba.com",
    # health
    "healthline.com", "webmd.com", "medicalnewstoday.com", "verywellhealth.com",
    "everydayhealth.com", "mayoclinic.org", "clevelandclinic.org", "drugs.com",
    "medscape.com", "statnews.com",
    # food
    "allrecipes.com", "foodnetwork.com", "epicurious.com", "seriouseats.com",
    "bonappetit.com", "delish.com", "tasteofhome.com", "thekitchn.com",
    "simplyrecipes.com", "food52.com",
    # technology and how-to
    "lifehacker.com", "howtogeek.com", "makeuseof.com", "tomshardware.com",
    "pcworld.com", "pcmag.com", "digitaltrends.com", "androidauthority.com",
    "9to5mac.com", "macrumors.com", "appleinsider.com", "theregister.com",
    "phoronix.com", "slashdot.org", "venturebeat.com", "thenextweb.com",
    "techradar.com", "tomsguide.com", "laptopmag.com", "windowscentral.com",
    "xda-developers.com", "hackernoon.com",
    # travel and home
    "travelandleisure.com", "cntraveler.com", "lonelyplanet.com", "fodors.com",
    "afar.com", "thepointsguy.com", "architecturaldigest.com", "dwell.com",
    "apartmenttherapy.com", "housebeautiful.com", "thespruce.com", "bhg.com",
    "hgtv.com", "realsimple.com", "marthastewart.com",
    # entertainment and games
    "rottentomatoes.com", "ign.com", "polygon.com", "kotaku.com", "pcgamer.com",
    "gamespot.com", "eurogamer.net", "gamesradar.com", "vg247.com",
    "hollywoodreporter.com", "deadline.com", "indiewire.com", "avclub.com",
    "pitchfork.com", "billboard.com", "stereogum.com", "consequence.net", "nme.com",
    # asia pacific and india
    "scmp.com", "japantimes.co.jp", "straitstimes.com", "thehindu.com", "ndtv.com",
    "hindustantimes.com", "indianexpress.com", "livemint.com", "business-standard.com",
    "thequint.com", "scroll.in", "thewire.in", "firstpost.com", "news18.com",
    "moneycontrol.com",
]

# Newspaper chains deploy one CDN configuration across every property they own. If a
# crawler rule is set at the chain level rather than per title, it is visible across the
# whole network at once — which is what separates one publisher's choice from an industry
# default nobody announced.
CHAINS = {
    "hearst": [
        "sfgate.com", "sfchronicle.com", "houstonchronicle.com", "chron.com",
        "timesunion.com", "expressnews.com", "mysanantonio.com", "ctpost.com",
        "newstimes.com", "stamfordadvocate.com", "greenwichtime.com", "nhregister.com",
        "thehour.com", "registercitizen.com", "middletownpress.com", "seattlepi.com",
        "lmtonline.com", "mrt.com", "ourmidland.com", "theintelligencer.com",
        "manisteenews.com", "bigrapidsnews.com",
    ],
    "hearst-magazines": [
        "cosmopolitan.com", "elle.com", "esquire.com", "harpersbazaar.com",
        "menshealth.com", "womenshealthmag.com", "runnersworld.com", "bicycling.com",
        "popularmechanics.com", "housebeautiful.com", "delish.com", "countryliving.com",
        "goodhousekeeping.com", "prevention.com", "roadandtrack.com", "caranddriver.com",
        "townandcountrymag.com", "elledecor.com", "veranda.com",
    ],
    "gannett": [
        "usatoday.com", "azcentral.com", "dispatch.com", "freep.com", "detroitnews.com",
        "tennessean.com", "indystar.com", "jsonline.com", "courier-journal.com",
        "cincinnati.com", "northjersey.com", "democratandchronicle.com",
        "desmoinesregister.com", "statesman.com", "commercialappeal.com", "knoxnews.com",
        "floridatoday.com", "delawareonline.com", "lohud.com", "greenvilleonline.com",
        "clarionledger.com", "argusleader.com", "rgj.com", "coloradoan.com",
        "elpasotimes.com",
    ],
    "advance": [
        "nj.com", "cleveland.com", "al.com", "oregonlive.com", "syracuse.com",
        "pennlive.com", "mlive.com", "masslive.com", "silive.com",
        "lehighvalleylive.com", "nola.com", "gulflive.com",
    ],
    "lee": [
        "stltoday.com", "richmond.com", "buffalonews.com", "tucson.com", "omaha.com",
        "madison.com", "journalstar.com", "billingsgazette.com", "tulsaworld.com",
        "roanoke.com", "thesouthern.com", "herald-review.com", "pantagraph.com",
        "qctimes.com", "wcfcourier.com", "globegazette.com",
    ],
    "mcclatchy": [
        "miamiherald.com", "kansascity.com", "charlotteobserver.com", "sacbee.com",
        "fresnobee.com", "modbee.com", "thestate.com", "newsobserver.com",
        "star-telegram.com", "idahostatesman.com", "tri-cityherald.com",
        "bellinghamherald.com", "sunherald.com", "myrtlebeachonline.com",
        "heraldsun.com",
    ],
}


def build_chains() -> list[dict]:
    entries: list[dict] = []
    seen: set[str] = set()
    for chain, domains in CHAINS.items():
        for d in domains:
            if d in seen:
                continue
            seen.add(d)
            entries.append({"domain": d, "url": f"https://{d}/", "source": chain})
    return entries


ECOMMERCE = [
    "bestbuy.com", "target.com", "etsy.com", "ebay.com", "wayfair.com",
    "homedepot.com", "lowes.com", "ikea.com", "zappos.com", "chewy.com",
    "rei.com", "nike.com", "adidas.com", "sephora.com", "macys.com",
    "nordstrom.com", "overstock.com", "newegg.com", "asos.com", "uniqlo.com",
]

DOCS = [
    "pypi.org", "npmjs.com", "docs.python.org", "developer.mozilla.org",
    "kubernetes.io", "nodejs.org", "react.dev", "vuejs.org", "go.dev",
    "docs.docker.com",
]


def _fetch_llmstxt_domains(limit: int) -> list[str]:
    r = httpx.get(
        LLMSTXT_INDEX,
        timeout=45,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; Botvue corpus builder)"},
    )
    r.raise_for_status()
    seen: list[str] = []
    for domain in _LLMS_DOMAIN.findall(r.text):
        if domain not in seen:
            seen.append(domain)
    return seen[:limit]


def build(llmstxt_limit: int = 400, refresh: bool = False) -> list[dict]:
    out_path = CORPUS_DIR / "urls.json"
    if out_path.exists() and not refresh:
        return json.loads(out_path.read_text(encoding="utf-8"))

    entries: list[dict] = []
    seen: set[str] = set()

    def add(domain: str, source: str) -> None:
        domain = domain.strip().lower().rstrip("/")
        if not domain or domain in seen:
            return
        seen.add(domain)
        entries.append({"domain": domain, "url": f"https://{domain}/", "source": source})

    for d in PUBLISHERS:
        add(d, "publisher")
    for d in ECOMMERCE:
        add(d, "ecommerce")
    for d in DOCS:
        add(d, "docs")
    for d in _fetch_llmstxt_domains(llmstxt_limit):
        add(d, "llmstxt")

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(entries, indent=1), encoding="utf-8")
    return entries


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(prog="scanner.corpus")
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    built = build(args.limit, args.refresh)
    counts: dict[str, int] = {}
    for e in built:
        counts[e["source"]] = counts.get(e["source"], 0) + 1
    print(f"{len(built)} domains: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
