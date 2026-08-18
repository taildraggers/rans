"""Scraper for RANS experimental aircraft listings on barnstormers.com.

Barnstormers' single-manufacturer category pages (the same pattern seen in
the companion Aviat, CubCrafters, de Havilland, Maule, and Van's RV repos)
can mix in off-brand or off-topic listings with no distinguishing HTML
markup from the genuine ones. So results are filtered by title against a
small allowlist of RANS product names before being published.

On top of that brand allowlist, only whole-aircraft-for-sale listings are
kept: each ad's title must match a recognized RANS model code, and titles
that look like parts/accessories/services/raffles are dropped. Surviving
titles are rewritten to a canonical "YEAR RANS MODEL" form when the ad
states a model year, or just "RANS MODEL" when it doesn't, so every
listing follows the same format.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from .common import (
    Listing,
    extract_date,
    extract_location,
    extract_price,
    fetch,
    format_aircraft_title,
)

SITE_NAME = "Barnstormers.com"
BASE = "https://www.barnstormers.com"
MAKE = "RANS"

# Category page for RANS experimental listings on Barnstormers.
CATEGORY_URLS = [
    f"{BASE}/category-18956-Experimental--Rans.html",
]

MAX_PAGES = 10
LISTING_LINK_RE = re.compile(r"^/classified-(\d+)-(.+)\.html$")
GENERIC_SITE_TITLE_SNIPPET = "barnstormers.com find aircraft"


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[-_]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _compact(text: str) -> str:
    return re.sub(r"[\s-]", "", text.lower())


# RANS model codes are "S" + a model number, optionally followed by a
# trailing letter/suffix (e.g. "S-6ES", "S-19"). The prefix and number may
# be separated by a space, a hyphen, or nothing, since _title_from_url()
# turns the source URL's hyphens into spaces. Only official S-numbers are
# listed (no generic \d+) so this can't drift onto an unrelated "S-something".
_MODEL_NUMBERS = ["4", "5", "6", "7", "9", "10", "12", "14", "17", "18", "19", "20", "21"]
_MODEL_CODE_RE = re.compile(
    r"\bs[\s-]?(" + "|".join(_MODEL_NUMBERS) + r")\b", re.IGNORECASE
)

# Only ads whose title matches one of these (case/hyphen/space-insensitive,
# compared against a fully compacted - no spaces or hyphens - form of the
# title) are kept, since the category page itself isn't reliably RANS-only.
TARGET_MODEL_PHRASES = ["rans"] + [f"s{n}" for n in _MODEL_NUMBERS]


def _matches_target_models(title: str) -> bool:
    compact = _compact(title)
    return any(phrase in compact for phrase in TARGET_MODEL_PHRASES)


# Common marketing names, used only as a fallback when no explicit S-number
# is present. Unlike the S-number codes, these plain English words aren't
# distinctive enough to trust on their own (a lesson learned the hard way in
# the companion Piper repo, where a bare "Cub" mislabeled non-Piper
# homebuilts) - so each one also requires the title to say "RANS" or
# "Rans" explicitly.
_MARKETING_NAME_RULES = [
    (re.compile(r"\bcoyote\s*ii\b", re.IGNORECASE), "S-6"),
    (re.compile(r"\bcoyote\b", re.IGNORECASE), "S-4"),
    (re.compile(r"\bcourier\b", re.IGNORECASE), "S-7"),
    (re.compile(r"\bchaos\b", re.IGNORECASE), "S-9"),
    (re.compile(r"\bsakota\b", re.IGNORECASE), "S-10"),
    (re.compile(r"\bairaile\b", re.IGNORECASE), "S-12"),
    (re.compile(r"\bstinger\s*ii\b", re.IGNORECASE), "S-18"),
    (re.compile(r"\bstinger\b", re.IGNORECASE), "S-17"),
    (re.compile(r"\bventerra\b", re.IGNORECASE), "S-19"),
    (re.compile(r"\braven\b", re.IGNORECASE), "S-20"),
    (re.compile(r"\boutbound\b", re.IGNORECASE), "S-21"),
]


def _extract_model(title: str) -> tuple[str, str] | None:
    match = _MODEL_CODE_RE.search(title)
    if match:
        return MAKE, f"S-{match.group(1)}"

    if "rans" in _compact(title):
        for pattern, canonical in _MARKETING_NAME_RULES:
            if pattern.search(title):
                return MAKE, canonical
    return None


def _title_from_url(url: str) -> str:
    """Listing pages share a generic <title>/<h1>, but the URL slug is the ad's own title."""
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    match = LISTING_LINK_RE.match("/" + slug)
    if not match:
        return unquote(slug)
    return unquote(match.group(2)).replace("-", " ").strip()


def _find_listing_links(html: str) -> set[str]:
    soup = BeautifulSoup(html, "lxml")
    links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0]
        if LISTING_LINK_RE.match(href):
            links.add(urljoin(BASE, href))
    return links


def _find_next_page_url(html: str, current_url: str) -> str | None:
    """Find a "next page" link on a category listing page, if any."""
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower()
        rel = a.get("rel") or []
        if text in ("next", "next »", "»", "next page", ">") or "next" in rel:
            candidate = urljoin(current_url, a["href"])
            if candidate != current_url:
                return candidate
    return None


def _debug_dump_hrefs(html: str, limit: int = 25) -> None:
    soup = BeautifulSoup(html, "lxml")
    hrefs = [a["href"] for a in soup.find_all("a", href=True)]
    interesting = [h for h in hrefs if "classified" in h.lower() or "rans" in h.lower()]
    sample = interesting[:limit] or hrefs[:limit]
    print(f"  [debug] {len(hrefs)} total <a href> on page; sample: {sample}")


def _parse_detail_page(url: str, html: str) -> Listing | None:
    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None
    if title:
        title = re.sub(r"\s*[\|\-]\s*Barnstormers.*$", "", title, flags=re.IGNORECASE).strip()
    if not title or GENERIC_SITE_TITLE_SNIPPET in title.lower():
        title = _title_from_url(url)
    if not title:
        return None

    if not _matches_target_models(title):
        return None

    text = soup.get_text(" ", strip=True)

    formatted_title = format_aircraft_title(title, text, _extract_model)
    if not formatted_title:
        return None
    title = formatted_title

    price = extract_price(text)
    location = extract_location(text)
    date_posted = extract_date(text)

    return Listing(
        title=title,
        price=price,
        location=location,
        date_posted=date_posted,
        site=SITE_NAME,
        url=url,
    )


def scrape() -> list[Listing]:
    print(f"[{SITE_NAME}] starting scrape")
    all_links: set[str] = set()

    for category_url in CATEGORY_URLS:
        seen_this_category: set[str] = set()
        url = category_url
        for page in range(1, MAX_PAGES + 1):
            html = fetch(url)
            if not html:
                break
            links = _find_listing_links(html)
            new_links = links - seen_this_category
            print(f"  [{category_url}] page {page}: {len(links)} links ({len(new_links)} new)")
            if page == 1 and not links:
                _debug_dump_hrefs(html)
            seen_this_category |= links
            next_url = _find_next_page_url(html, url)
            if not next_url or not new_links:
                break
            url = next_url
        all_links |= seen_this_category

    print(f"[{SITE_NAME}] {len(all_links)} unique listing URLs found")

    candidate_links = {url for url in all_links if _matches_target_models(_title_from_url(url))}
    print(f"[{SITE_NAME}] {len(candidate_links)} match RANS product names")

    listings: list[Listing] = []
    for url in sorted(candidate_links):
        html = fetch(url)
        if not html:
            continue
        listing = _parse_detail_page(url, html)
        if listing:
            listings.append(listing)

    print(f"[{SITE_NAME}] parsed {len(listings)} listings")
    return listings
