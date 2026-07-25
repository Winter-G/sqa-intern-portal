#!/usr/bin/env python3
"""
scraper.py — SQA-Intern-Aggregator ingestion engine

Polls Sri Lankan tech job boards + global remote job APIs, filters for
QA / SQA / Testing intern & trainee roles, de-duplicates against the
existing data store, and writes the merged result to jobs.json.

Run manually:      python scraper.py
Run automatically:  see .github/workflows/scraper.yml (hourly)

Sources
-------
1. ITPro.lk        — RSS feeds (stable, structured, no scraping needed)
2. RemoteOK         — public JSON API (https://remoteok.com/api)
3. Arbeitnow        — public JSON API (https://www.arbeitnow.com/api/job-board-api)
4. DevJobs.lk       — best-effort HTML scrape (site has no RSS/API)
5. TopJobs.lk       — best-effort HTML scrape (site has no RSS/API)

NOTE on sources 4 & 5:
These two sites don't expose RSS or a JSON API, so we scrape their HTML.
Job boards restyle their pages periodically, so if these two sources ever
stop returning results, that's the first place to check — open the site,
view source, and adjust the selectors / regexes in `scrape_devjobs()` /
`scrape_topjobs()` below. Sources 1-3 are far more robust since they're
structured feeds, not scraped HTML, so don't worry about those breaking.
"""

import hashlib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

JOBS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs.json")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SQAInternAggregator/1.0; "
                  "+https://github.com/) Winter's personal job-tracker bot"
}
REQUEST_TIMEOUT = 20

# Role-level keywords: at least one must appear in the title (or description
# for sources with no clean title) for a posting to count as intern-level.
ROLE_LEVEL_PATTERN = re.compile(
    r"\b(intern(ship)?|trainee|junior|entry[\s-]?level|graduate)\b", re.I
)

# QA-domain keywords: at least one must appear somewhere in title+description.
QA_DOMAIN_PATTERN = re.compile(
    r"\b(qa|sqa|quality\s*assurance|quality\s*engineer|test(ing|er)?|"
    r"sdet|automation\s*test|manual\s*test|test\s*case|test\s*plan)\b",
    re.I,
)

# Terms that should exclude a posting even if the above match (avoid noise
# like "Senior QA Engineer" full-time roles slipping in as false positives —
# these are handled by ROLE_LEVEL_PATTERN requiring intern/junior wording,
# but this list catches a few more obvious mismatches).
EXCLUDE_PATTERN = re.compile(r"\b(senior|lead|manager|head\s*of)\b", re.I)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def make_id(*parts: str) -> str:
    """Stable SHA-256 id for de-duplication."""
    raw = "|".join(p.strip().lower() for p in parts if p)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def is_qa_intern_role(title: str, description: str = "") -> bool:
    text_title = title or ""
    text_all = f"{title} {description}"
    if EXCLUDE_PATTERN.search(text_title):
        return False
    if not ROLE_LEVEL_PATTERN.search(text_title):
        return False
    if not QA_DOMAIN_PATTERN.search(text_all):
        return False
    return True


def clean_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = BeautifulSoup(raw_html, "html.parser").get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()


def safe_get(url: str, **kwargs):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, **kwargs)
        resp.raise_for_status()
        return resp
    except requests.RequestException as exc:
        print(f"  ! request failed for {url}: {exc}", file=sys.stderr)
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Source 1: ITPro.lk (RSS — structured, reliable)
# --------------------------------------------------------------------------

def scrape_itpro() -> list:
    print("[ITPro.lk] fetching RSS feeds...")
    jobs = []
    feeds = [
        "https://itpro.lk/rss/all/quality-assurance/",
        "https://itpro.lk/rss/all/internship/",
    ]
    for feed_url in feeds:
        resp = safe_get(feed_url)
        if not resp:
            continue
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            print(f"  ! RSS parse error for {feed_url}: {exc}", file=sys.stderr)
            continue

        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = clean_html(item.findtext("description") or "")
            pub_date_raw = (item.findtext("pubDate") or "").strip()

            if not title or not link:
                continue
            if not is_qa_intern_role(title, desc):
                continue

            # Try to extract company from "Title Company Location • ..."
            # ITPro RSS descriptions/titles vary; fall back to "ITPro.lk listing"
            company = "See listing"
            m = re.search(r"at\s+([A-Za-z0-9&.,()/\- ]{2,60})$", title)
            if m:
                company = m.group(1).strip()

            posted_iso = None
            if pub_date_raw:
                try:
                    dt = datetime.strptime(pub_date_raw, "%a, %d %b %Y %H:%M:%S %z")
                    posted_iso = dt.astimezone(timezone.utc).isoformat()
                except ValueError:
                    pass

            jobs.append({
                "id": make_id(link),
                "title": title,
                "company": company,
                "location": "Sri Lanka",
                "source": "ITPro.lk",
                "url": link,
                "description": desc[:1200],
                "posted_date": posted_iso,
            })
    print(f"  -> {len(jobs)} matching jobs")
    return jobs


# --------------------------------------------------------------------------
# Source 2: RemoteOK (public JSON API)
# --------------------------------------------------------------------------

def scrape_remoteok() -> list:
    print("[RemoteOK] fetching API...")
    resp = safe_get("https://remoteok.com/api")
    if not resp:
        return []
    try:
        data = resp.json()
    except ValueError:
        print("  ! could not parse RemoteOK JSON", file=sys.stderr)
        return []

    jobs = []
    for entry in data:
        if not isinstance(entry, dict) or "id" not in entry:
            continue  # first element is a metadata blob, skip it
        title = (entry.get("position") or entry.get("title") or "").strip()
        description = clean_html(entry.get("description", ""))
        tags = " ".join(entry.get("tags", []) or [])
        if not is_qa_intern_role(title, f"{description} {tags}"):
            continue

        url = entry.get("url") or (
            f"https://remoteok.com/remote-jobs/{entry['id']}" if entry.get("id") else ""
        )
        posted_iso = None
        if entry.get("date"):
            try:
                posted_iso = datetime.fromisoformat(
                    entry["date"].replace("Z", "+00:00")
                ).astimezone(timezone.utc).isoformat()
            except ValueError:
                pass

        jobs.append({
            "id": make_id(url or title, entry.get("company", "")),
            "title": title,
            "company": entry.get("company", "Unknown"),
            "location": entry.get("location") or "Remote (Worldwide)",
            "source": "RemoteOK",
            "url": url,
            "description": description[:1200],
            "posted_date": posted_iso,
        })
    print(f"  -> {len(jobs)} matching jobs")
    return jobs


# --------------------------------------------------------------------------
# Source 3: Arbeitnow (public JSON API)
# --------------------------------------------------------------------------

def scrape_arbeitnow() -> list:
    print("[Arbeitnow] fetching API...")
    jobs = []
    url = "https://www.arbeitnow.com/api/job-board-api"
    page_url = url
    pages_fetched = 0
    while page_url and pages_fetched < 3:  # cap pagination, we only need recent pages
        resp = safe_get(page_url)
        pages_fetched += 1
        if not resp:
            break
        try:
            payload = resp.json()
        except ValueError:
            break

        for entry in payload.get("data", []):
            title = (entry.get("title") or "").strip()
            description = clean_html(entry.get("description", ""))
            tags = " ".join(entry.get("tags", []) or [])
            if not entry.get("remote", True):
                continue
            if not is_qa_intern_role(title, f"{description} {tags}"):
                continue

            posted_iso = None
            if entry.get("created_at"):
                try:
                    posted_iso = datetime.fromtimestamp(
                        int(entry["created_at"]), tz=timezone.utc
                    ).isoformat()
                except (ValueError, TypeError):
                    pass

            jobs.append({
                "id": make_id(entry.get("url", ""), entry.get("company_name", "")),
                "title": title,
                "company": entry.get("company_name", "Unknown"),
                "location": "Remote (Worldwide)",
                "source": "Arbeitnow",
                "url": entry.get("url", ""),
                "description": description[:1200],
                "posted_date": posted_iso,
            })

        page_url = payload.get("links", {}).get("next")
    print(f"  -> {len(jobs)} matching jobs")
    return jobs


# --------------------------------------------------------------------------
# Source 4: DevJobs.lk (best-effort HTML scrape — verify selectors if broken)
# --------------------------------------------------------------------------

def scrape_devjobs() -> list:
    print("[DevJobs.lk] fetching listing pages...")
    jobs = []
    listing_pages = [
        "https://devjobs.lk/qa-jobs",
        "https://devjobs.lk/intern-jobs",
    ]
    detail_links = set()
    for page in listing_pages:
        resp = safe_get(page)
        if not resp:
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=re.compile(r"/dev-jobs/client/ads/\d+")):
            detail_links.add(urljoin(page, a["href"]))

    for link in detail_links:
        resp = safe_get(link)
        if not resp:
            continue
        soup = BeautifulSoup(resp.text, "html.parser")

        # Page <title> on DevJobs.lk follows "<Job Title> at <Company> | DevJobs"
        raw_title = (soup.title.string if soup.title else "") or ""
        raw_title = raw_title.replace("DevJobs |", "").strip()
        m = re.match(r"(.+?)\s+at\s+(.+?)(\s*\|.*)?$", raw_title)
        if m:
            title, company = m.group(1).strip(), m.group(2).strip()
        else:
            title, company = raw_title, "See listing"

        meta_desc = soup.find("meta", attrs={"name": "description"})
        description = clean_html(meta_desc["content"]) if meta_desc and meta_desc.get("content") else ""
        # Fall back to main body text if meta description is too thin
        if len(description) < 60:
            body = soup.find("body")
            description = clean_html(str(body))[:1500] if body else description

        if not title or not is_qa_intern_role(title, description):
            continue

        jobs.append({
            "id": make_id(link),
            "title": title,
            "company": company,
            "location": "Sri Lanka / Remote",
            "source": "DevJobs.lk",
            "url": link,
            "description": description[:1200],
            "posted_date": None,  # DevJobs.lk doesn't expose a reliable date; falls back to first_seen
        })
        time.sleep(0.5)  # be polite
    print(f"  -> {len(jobs)} matching jobs")
    return jobs


# --------------------------------------------------------------------------
# Source 5: TopJobs.lk (best-effort HTML scrape — verify selectors if broken)
# --------------------------------------------------------------------------

def scrape_topjobs() -> list:
    print("[TopJobs.lk] fetching search results...")
    jobs = []
    search_url = "https://www.topjobs.lk/employer/JobAdvSearch.jsp?keyword=QA+Intern"
    resp = safe_get(search_url)
    if not resp:
        print("  -> 0 matching jobs")
        return jobs

    soup = BeautifulSoup(resp.text, "html.parser")
    # TopJobs.lk lists vacancies as links containing a job ref like "#1519031"
    for a in soup.find_all("a", href=True):
        text = clean_html(a.get_text())
        if not text or "#" not in a.parent.get_text(" ", strip=True) if a.parent else True:
            pass
        if not text:
            continue
        if not is_qa_intern_role(text):
            continue
        href = urljoin(search_url, a["href"])
        # Nearby text often contains "Company Name #RefNo Location"
        context = clean_html(a.parent.get_text(" ", strip=True)) if a.parent else ""
        company = "See listing"
        cm = re.search(r"·\s*([A-Za-z0-9&.,()/\- ]{2,50})\s*#\d+", context)
        if cm:
            company = cm.group(1).strip()

        jobs.append({
            "id": make_id(href, text),
            "title": text,
            "company": company,
            "location": "Sri Lanka",
            "source": "TopJobs.lk",
            "url": href,
            "description": context[:1200],
            "posted_date": None,
        })
    print(f"  -> {len(jobs)} matching jobs")
    return jobs


# --------------------------------------------------------------------------
# Merge, persist, notify
# --------------------------------------------------------------------------

def load_existing() -> list:
    if os.path.exists(JOBS_FILE):
        with open(JOBS_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []


def notify_discord(new_jobs: list):
    if not DISCORD_WEBHOOK_URL or not new_jobs:
        return
    lines = [f"**{j['title']}** — {j['company']} ({j['source']})\n{j['url']}" for j in new_jobs[:10]]
    content = f"🔔 {len(new_jobs)} new SQA intern posting(s) found:\n\n" + "\n\n".join(lines)
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": content[:2000]}, timeout=10)
    except requests.RequestException as exc:
        print(f"  ! Discord webhook failed: {exc}", file=sys.stderr)


def notify_telegram(new_jobs: list):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID) or not new_jobs:
        return
    lines = [f"{j['title']} — {j['company']} ({j['source']})\n{j['url']}" for j in new_jobs[:10]]
    text = f"🔔 {len(new_jobs)} new SQA intern posting(s) found:\n\n" + "\n\n".join(lines)
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(api, json={"chat_id": TELEGRAM_CHAT_ID, "text": text[:4000]}, timeout=10)
    except requests.RequestException as exc:
        print(f"  ! Telegram notify failed: {exc}", file=sys.stderr)


def main():
    existing = load_existing()
    existing_by_id = {j["id"]: j for j in existing}

    all_scraped = []
    for scraper_fn in (scrape_itpro, scrape_remoteok, scrape_arbeitnow, scrape_devjobs, scrape_topjobs):
        try:
            all_scraped.extend(scraper_fn())
        except Exception as exc:  # keep one source failing from killing the whole run
            print(f"! {scraper_fn.__name__} crashed: {exc}", file=sys.stderr)

    new_jobs = []
    merged = dict(existing_by_id)
    for job in all_scraped:
        if job["id"] in merged:
            # Keep the original first_seen timestamp; refresh other fields
            # in case the posting was edited.
            job["first_seen"] = merged[job["id"]].get("first_seen", now_iso())
        else:
            job["first_seen"] = now_iso()
            new_jobs.append(job)
        merged[job["id"]] = job

    result = sorted(
        merged.values(),
        key=lambda j: j.get("posted_date") or j.get("first_seen") or "",
        reverse=True,
    )

    with open(JOBS_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\nDone. {len(result)} total jobs tracked ({len(new_jobs)} new this run).")

    if new_jobs:
        notify_discord(new_jobs)
        notify_telegram(new_jobs)


if __name__ == "__main__":
    main()
