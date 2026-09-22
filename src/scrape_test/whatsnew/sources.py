"""Curated feed sources for the what's-new digest.

Every entry here was verified to return posts through the existing feed-discovery chain
(declared feed, common feed paths, sitemap, then page scrape). Firms whose sites are
rendered entirely in JavaScript are listed in KNOWN_GAPS rather than silently omitted -
they are real coverage holes, not oversights.

Edit this list to match your territory; nothing else needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Source:
    name: str
    domain: str
    kind: str      # "vc" | "press"
    region: str    # "us" | "emea" | "global"


VC_SOURCES: tuple[Source, ...] = (
    # --- United States ---
    Source("Sequoia", "sequoiacap.com", "vc", "us"),
    Source("Accel", "accel.com", "vc", "us"),
    Source("Greylock", "greylock.com", "vc", "us"),
    Source("NEA", "nea.com", "vc", "us"),
    Source("Bain Capital Ventures", "baincapitalventures.com", "vc", "us"),
    Source("Felicis", "felicis.com", "vc", "us"),
    Source("Lightspeed", "lsvp.com", "vc", "us"),
    Source("Kleiner Perkins", "kleinerperkins.com", "vc", "us"),
    Source("Founders Fund", "foundersfund.com", "vc", "us"),
    Source("Menlo Ventures", "menlovc.com", "vc", "us"),
    Source("Madrona", "madrona.com", "vc", "us"),
    Source("Unusual Ventures", "unusual.vc", "vc", "us"),
    Source("Bessemer", "bvp.com", "vc", "us"),
    Source("First Round", "firstround.com", "vc", "us"),
    Source("Battery Ventures", "battery.com", "vc", "us"),
    Source("Uncork Capital", "uncorkcapital.com", "vc", "us"),
    Source("Boldstart", "boldstart.vc", "vc", "us"),
    # --- EMEA ---
    Source("Balderton", "balderton.com", "vc", "emea"),
    Source("Northzone", "northzone.com", "vc", "emea"),
    Source("Creandum", "creandum.com", "vc", "emea"),
    Source("Cherry Ventures", "cherry.vc", "vc", "emea"),
    Source("HV Capital", "hvcapital.com", "vc", "emea"),
    Source("Earlybird", "earlybird.com", "vc", "emea"),
    Source("83North", "83north.com", "vc", "emea"),
    Source("Frontline", "frontline.vc", "vc", "emea"),
    Source("Seedcamp", "seedcamp.com", "vc", "emea"),
)

PRESS_SOURCES: tuple[Source, ...] = (
    Source("TechCrunch", "techcrunch.com", "press", "global"),
    Source("Sifted", "sifted.eu", "press", "emea"),
    Source("Tech.eu", "tech.eu", "press", "emea"),
    Source("Crunchbase News", "news.crunchbase.com", "press", "global"),
    Source("UKTN", "uktech.news", "press", "emea"),
    Source("VentureBeat", "venturebeat.com", "press", "global"),
)

ALL_SOURCES: tuple[Source, ...] = VC_SOURCES + PRESS_SOURCES

# Sites that render their newsroom entirely client-side, so no feed, sitemap or static
# scrape reaches the posts. Listed so the gap is visible rather than looking like an
# editorial choice. Reaching these would need a headless browser.
KNOWN_GAPS: tuple[str, ...] = (
    "a16z.com", "indexventures.com", "atomico.com", "generalcatalyst.com",
    "insightpartners.com", "benchmark.com", "khoslaventures.com", "redpoint.com",
    "pointnine.com", "localglobe.vc", "speedinvest.com", "eu-startups.com",
)


def sources_for(
    kinds: tuple[str, ...] = ("vc", "press"),
    regions: tuple[str, ...] = (),
) -> list[Source]:
    out = [s for s in ALL_SOURCES if s.kind in kinds]
    if regions:
        out = [s for s in out if s.region in regions or s.region == "global"]
    return out
