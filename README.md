# Flowhunt scraper

Finds Webflow jobs across the web and writes them to `data/jobs.json`. The Flowhunt board picks up that file every few hours, removes duplicates and shows the jobs.

## What it scrapes

| Group | Sources | How |
|---|---|---|
| Big job boards | LinkedIn, Indeed (20 countries), Glassdoor (10), Google Jobs, ZipRecruiter, Naukri, Bayt | [python-jobspy](https://github.com/speedyapply/JobSpy), search term `webflow`, last 72h |
| Company career pages | Every Ashby, Greenhouse, Lever, Workable and Recruitee board Common Crawl has seen (thousands of companies, YC startups included) | Public ATS APIs; a job counts if its title says Webflow, or its description does and it's a web/design/marketing role |
| Remote boards | Himalayas, Jobicy, Remotive, RemoteOK, Arbeitnow, Working Nomads | Public JSON APIs |
| Hacker News | Latest two "Who is hiring?" threads | Algolia API |

## Schedule (GitHub Actions)

- **Every 4 hours:** big boards, remote boards and the company boards that have had Webflow jobs before (`data/hot.json`).
- **Daily at 02:43 UTC:** a full sweep of every company board in `data/companies.json`.
- **Weekly on Sunday:** `discover` refreshes the company list from Common Crawl.

Each run commits `data/jobs.json` and `data/meta.json`.

## Setup

1. Create an **empty public** repo on GitHub called `flowhunt-scraper`. Leave out any README or .gitignore.
2. In this folder, run:
   ```
   git init -b main
   git add .
   git commit -m "Flowhunt scraper"
   git remote add origin https://github.com/<you>/flowhunt-scraper.git
   git push -u origin main
   ```
3. On GitHub, open **Actions**. Enable workflows if asked, then:
   - run **discover** once (Run workflow) to build the company list (takes about 20–60 min);
   - then run **scrape** with mode `full`.

## Run locally

```
pip install -r requirements.txt
python -m scraper.run --mode full
```

## Notes

- LinkedIn, Indeed and Glassdoor rate-limit scrapers. When one market gets blocked, that search logs a warning and the rest carry on.
- `data/jobs.json` keeps jobs seen in the last 30 days and ignores postings older than 60 days.
- The ids match the Flowhunt board's dedupe key, so the same job from LinkedIn and from a Greenhouse page merges into one entry.
