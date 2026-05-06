# Job Watch Bot

Automated job search assistant. Every 3 days, it scrapes the career pages of a curated list of companies, filters relevant openings against a keyword shortlist, asks Claude to score and verdict each one against a personal brief, and posts the result as a GitHub Issue.

The whole thing runs for free on GitHub Actions and costs around 5 to 10 cents per run via the Anthropic API.

---

## What it does, step by step

1. **Trigger.** GitHub Actions runs the workflow every 3 days at 08:00 UTC, or on manual demand from the Actions tab.
2. **Scrape.** For each entry in `config/sources.csv`, the script fetches the career page and extracts job titles + URLs. Two fetcher types are supported: generic HTML scraping (most career pages) and the Workable public API (used by Hugging Face and similar). 
3. **Pre-filter.** Each job title is checked against the include and exclude keyword lists in `config/keywords.json`. Only jobs that match an include keyword and no exclude keyword move forward. This keeps the API bill small by sending only realistic candidates to Claude.
4. **Score.** The remaining jobs are sent in a single batch to Claude Opus 4.7 along with the brief in `config/brief.md`. Claude returns, for each job, a fit score out of 100, a verdict (GO / CREUSE / PASS), and a one-line red flag. Jobs scored below 40 are dropped.
5. **Notify.** A GitHub Issue is opened in the repo with a markdown summary, sorted by descending score. GitHub emails the issue to anyone watching the repo, so the notification arrives in the inbox automatically.
6. **Cache.** URLs of all notified jobs are saved to `seen_jobs.json`, which the workflow commits back to the repo. The next run skips anything already in there, so the same job is never reported twice.

---

## Repository structure

```
job-watch-bot/
├── README.md
├── search_jobs.py              # Main script
├── seen_jobs.json              # Cache of already-notified job URLs
├── config/
│   ├── brief.md                # Candidate brief, sent to Claude as context
│   ├── sources.csv             # List of career pages to scrape
│   └── keywords.json           # Include / exclude keywords for pre-filter
└── .github/
    └── workflows/
        └── job-search.yml      # GitHub Actions workflow definition
```

---

## Setup

### 1. Create the repository and fill in your config

Fork or clone this repo, then create your private config files from the provided examples:

```bash
cp config/brief.example.md     config/brief.md
cp config/keywords.example.json config/keywords.json
cp config/sources.example.csv  config/sources.csv
```

Edit each file with your own data. The real config files are listed in `.gitignore` and will never be committed.

### 2. Get an Anthropic API key

1. Go to https://console.anthropic.com
2. Settings → API Keys → Create Key
3. Settings → Billing → add a small balance (5 to 10 € is enough for several months of runs)
4. Copy the key, it starts with `sk-ant-...`

### 3. Add the API key as a GitHub secret

In the repository, go to Settings → Secrets and variables → Actions → New repository secret. Add:

| Name                | Value                       |
|---------------------|-----------------------------|
| `ANTHROPIC_API_KEY` | the key from step 2          |

The `GITHUB_TOKEN` secret is provided automatically by GitHub Actions, no need to add it.

### 4. Test it manually before waiting for the cron

Repository → Actions tab → Job Search Bot → Run workflow → Run. The run takes about 90 seconds. If everything is wired correctly, a new issue appears in the Issues tab with the day's results, and you receive a GitHub notification email.

---

## Configuration

All tunable parts live in the `config/` directory. No need to touch the Python code for normal use.

### `config/brief.md`

The candidate brief sent verbatim to Claude as the context for scoring. The more precise and honest the brief, the better the scoring. Sections currently covered:

- Profile, target roles, target sectors
- Geography and remote constraints
- Hard constraints (auto-pass criteria)
- Compensation target
- Differentiators
- Scoring guidance for Claude (be demanding, penalize vague match, etc.)

Update this file whenever priorities shift, e.g. new sector preference, change of remote tolerance, or compensation expectations.

### `config/sources.csv`

The list of career pages to scrape. Each row is one company. Columns:

| Column       | Required | Description |
|--------------|----------|-------------|
| `Company`    | yes      | Human-readable name shown in logs and the issue |
| `Location`   | no       | Office locations (for reference only, not used by the scraper) |
| `URL`        | yes      | Full URL of the career page or API endpoint |
| `Fields`     | no       | Domains the company hires in (for reference only) |
| `Preference` | no       | Personal interest rating (⭐ to ⭐⭐⭐) |
| `Selector`   | for html | Link to job postings page |
| `Type`       | yes      | `html`, `workable_api`, or `ashby_api` |



For Workable-hosted boards, the API endpoint pattern is `https://apply.workable.com/api/v3/accounts/<slug>/jobs`. Leave `Selector` empty for these rows.

For Ashby-hosted boards (`jobs.ashbyhq.com/<slug>`), set `URL` to the public careers page (e.g. `https://jobs.ashbyhq.com/linear`) and `Type` to `ashby_api`. The scraper derives the slug automatically and hits the public JSON API. Leave `Selector` empty.

### `config/keywords.json`

Two arrays, `include` and `exclude`, used as a fast pre-filter on job titles before sending anything to Claude. Match is case-insensitive, substring-based.

A job is sent to Claude only if its title contains at least one `include` keyword and zero `exclude` keywords. This typically reduces 200 raw scraped titles down to 10 to 30 candidates, which is what gets billed against the API.

Tune these lists to balance recall (don't miss good jobs) against cost (don't spam Claude with junk).

### `seen_jobs.json`

Plain JSON array of URLs already notified. Created automatically on the first run and committed back to the repo by the workflow. To force a re-notification of all current openings, delete this file and commit the change.

---

## Costs

A typical run sends 10 to 30 jobs to Claude with roughly 3000 input tokens (mostly the brief) and produces 500 to 2000 output tokens. With Claude Opus 4.7 pricing, this is about 5 to 10 cents per run. Running every 3 days, that is roughly 50 cents to 1 € per month. GitHub Actions on a public or private free tier easily covers the compute side.

To reduce cost further, switch the model in `search_jobs.py` from `claude-opus-4-7` to `claude-sonnet-4-6`, which is roughly 5x cheaper for similar quality on this kind of structured task.

---

## Known limitations

- **JS-rendered career pages.** Sites that load their job board via client-side JavaScript (Notion, Replit, custom-built boards) are not fully scraped by the basic HTML fetcher: it only sees the initial HTML, not the hydrated content. To handle these, add Playwright as a dependency and write a headless-browser fetcher. Workable and Ashby boards are exempt from this limitation as they are fetched via their public JSON APIs.
- **Anti-bot protections.** LinkedIn Jobs and Welcome to the Jungle block anonymous scraping. The official APIs require accounts and authentication. Not included by default.
- **Title-based filtering only.** The pre-filter looks at job titles, not descriptions. A misleading title (e.g. "Software Engineer" that turns out to be a frontend role) may slip through or be missed. The Claude scoring step partially compensates by reading the title in context.
- **No de-duplication across sources.** If the same job is listed on the company site and on Welcome to the Jungle, it is scored twice. In practice rare since only one source per company is configured.

---

## Modifying the schedule

The cron expression lives in `.github/workflows/job-search.yml` under the `schedule` key. Examples:

| Frequency       | Cron expression       |
|-----------------|----------------------|
| Every 3 days    | `0 8 */3 * *`        |
| Every Monday    | `0 8 * * 1`          |
| Twice a week    | `0 8 * * 1,4`        |
| Daily           | `0 8 * * *`          |

Times are in UTC. Note that GitHub Actions schedules can be delayed by up to 15 minutes during high-load windows.

---

## Manual trigger

The workflow can be run on demand from the Actions tab → Job Search Bot → Run workflow. Useful when adding a new source to confirm the selector works, or when expecting a new round of postings outside the regular cadence.

---

## Troubleshooting

**The workflow runs but no issue is created.** Either no jobs matched the keyword filter, or all matches were already in `seen_jobs.json`, or all scored below 40. Check the Actions log to see which case applies.

**Issue is created but a known-good job is missing.** Likely the source uses JS rendering. Verify by curling the URL and inspecting the raw HTML for the job title. If absent, the page is JS-rendered and needs Playwright.

**Claude returns invalid JSON.** Rare but happens. The error is logged but the cache is still saved, so the next run won't try to rescore everything. If it persists, lower the batch size by reducing the number of sources, or switch model.

**Bill higher than expected.** Check the Anthropic console usage tab. The pre-filter should keep batches small; if many jobs are slipping through, tighten the exclude keywords.