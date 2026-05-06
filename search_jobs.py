"""
Job search bot.

Scrapes career pages of target companies, filters relevant roles by keyword,
scores each remaining offer via Claude using the candidate brief, and notifies
via a GitHub Issue.

Configuration is externalized in the `config/` directory:
  - config/brief.md      : candidate brief sent to Claude as scoring context
  - config/sources.csv   : list of career pages to scrape
  - config/keywords.json : include/exclude keywords for pre-filtering

The seen_jobs.json file caches URLs of previously notified offers to avoid
duplicate notifications across runs.
"""

import csv
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import requests
from anthropic import Anthropic
from bs4 import BeautifulSoup

# === Paths ===
ROOT = Path(__file__).parent
CONFIG_DIR = ROOT / "config"
BRIEF_PATH = CONFIG_DIR / "brief.md"
SOURCES_PATH = CONFIG_DIR / "sources.csv"
KEYWORDS_PATH = CONFIG_DIR / "keywords.json"
CACHE_PATH = ROOT / "seen_jobs.json"

# === HTTP defaults ===
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; JobWatchBot/1.0)"}
HTTP_TIMEOUT = 30


def load_config():
    """Load all external configuration files."""
    brief = BRIEF_PATH.read_text(encoding="utf-8")
    with SOURCES_PATH.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        sources = [
            {
                "name": row["Company"],
                "url": row["URL"],
                "selector": row.get("Selector") or None,
                "type": row["Type"],
                "location": row.get("Location", ""),
                "fields": row.get("Fields", ""),
                "preference": row.get("Preference", ""),
            }
            for row in reader
        ]
    keywords = json.loads(KEYWORDS_PATH.read_text(encoding="utf-8"))
    return brief, sources, keywords


def load_seen_cache():
    """Load the set of previously notified job URLs."""
    if CACHE_PATH.exists():
        return set(json.loads(CACHE_PATH.read_text(encoding="utf-8")))
    return set()


def save_seen_cache(seen):
    """Persist the cache of notified URLs (committed by the GH Actions workflow)."""
    CACHE_PATH.write_text(json.dumps(sorted(seen), indent=2), encoding="utf-8")


def fetch_workable_api(url):
    """Workable's public API returns structured JSON. Used by Hugging Face etc."""
    resp = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    # The account slug is the segment after /accounts/ in the URL
    account = url.split("/accounts/")[1].split("/")[0]
    return [
        (j["title"], f"https://apply.workable.com/{account}/j/{j['shortcode']}/")
        for j in data.get("results", [])
    ]


def fetch_html(url, selector):
    """Generic HTML scraper. Looks for <a> elements matching the CSS selector
    and extracts (title, absolute_url) tuples.
    """
    resp = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    base = "/".join(url.split("/")[:3])

    jobs = []
    for link in soup.select(selector):
        title = link.get_text(strip=True)
        href = link.get("href", "")
        if not title or len(title) < 5 or not href:
            continue
        # Resolve relative URLs
        if not href.startswith("http"):
            href = base + href if href.startswith("/") else f"{base}/{href}"
        jobs.append((title, href))
    return jobs


def fetch_jobs(source):
    """Dispatch to the right fetcher based on the source type."""
    name = source["name"]
    try:
        if source["type"] == "workable_api":
            return fetch_workable_api(source["url"])
        elif source["type"] == "html":
            return fetch_html(source["url"], source["selector"])
        else:
            print(f"  ⚠️  {name}: unknown source type '{source['type']}'")
            return []
    except Exception as exc:
        print(f"  ⚠️  {name}: {exc}")
        return []


def is_relevant(title, keywords):
    """Pre-filter step: keep the job only if its title matches at least one
    include keyword AND no exclude keyword. Lightweight check before sending
    anything to Claude — keeps API costs low.
    """
    t = title.lower()
    if not any(kw in t for kw in keywords["include"]):
        return False
    if any(kw in t for kw in keywords["exclude"]):
        return False
    return True


def score_with_claude(client, brief, jobs):
    """Send the batch of relevant jobs to Claude for scoring + verdict.
    Returns a list of dicts sorted by descending score, filtered to score >= 40.
    """
    if not jobs:
        return []

    job_list = "\n".join([f"- [{c}] {t} | {u}" for c, t, u in jobs])

    prompt = f"""You are Raphaël's career agent. Here is his brief:

{brief}

Today's job offers found across target companies (company, title, url):

{job_list}

For EACH offer, return:
- Fit score /100 (be demanding, not complacent)
- Verdict: GO / CREUSE / PASS
- Main red flag in one short sentence (in French, "tutoiement", startup tone)

Reply ONLY in valid JSON, format:
[
  {{"company": "X", "title": "Y", "url": "Z", "score": 75, "verdict": "GO", "red_flag": "..."}},
  ...
]
Sort by descending score. Do NOT include offers with score < 40."""

    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )

    text = msg.content[0].text
    # Strip markdown code fences if Claude wrapped the JSON
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]

    return json.loads(text.strip())


def create_github_issue(scored_jobs):
    """Open a GitHub Issue summarizing the run. Uses the gh CLI which is
    pre-installed on the GitHub Actions ubuntu runner.
    """
    if not scored_jobs:
        print("No relevant new offer, skipping issue creation.")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    title = f"📋 Job Watch — {today} — {len(scored_jobs)} offers"

    lines = [f"# Run on {today}\n"]
    for j in scored_jobs:
        emoji = {"GO": "🟢", "CREUSE": "🟡", "PASS": "🔴"}.get(j["verdict"], "⚪")
        lines.append(f"## {emoji} [{j['company']}] {j['title']} — {j['score']}/100")
        lines.append(f"**Verdict** : {j['verdict']}  ")
        lines.append(f"**Red flag** : {j['red_flag']}  ")
        lines.append(f"🔗 {j['url']}\n")

    body = "\n".join(lines)
    repo = os.environ["GH_REPO"]
    subprocess.run(
        ["gh", "issue", "create", "--repo", repo, "--title", title, "--body", body],
        check=True,
    )


def main():
    print(f"=== Job Watch run {datetime.now().isoformat()} ===\n")

    brief, sources, keywords = load_config()
    seen = load_seen_cache()
    new_jobs = []

    for source in sources:
        name = source["name"]
        print(f"Scraping {name}...")
        jobs = fetch_jobs(source)
        relevant = [(name, t, u) for t, u in jobs if is_relevant(t, keywords)]
        truly_new = [(c, t, u) for c, t, u in relevant if u not in seen]
        print(f"  → {len(jobs)} total, {len(relevant)} relevant, {len(truly_new)} new")

        for c, t, u in truly_new:
            new_jobs.append((c, t, u))
            seen.add(u)

    print(f"\nTotal new relevant offers: {len(new_jobs)}")

    if new_jobs:
        client = Anthropic()
        try:
            scored = score_with_claude(client, brief, new_jobs)
            print(f"Score >= 40: {len(scored)} offers retained")
            create_github_issue(scored)
        except Exception as exc:
            print(f"Scoring/notification error: {exc}")

    save_seen_cache(seen)
    print("Done.")


if __name__ == "__main__":
    main()