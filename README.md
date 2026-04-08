# New York Post article workflow

Scripts to collect article URLs from the NY Post archive, download HTML for each article, and clean the text for analysis. Python 3.9 or newer is recommended.

---

## Web UI

Run the pipeline from your browser. Logs go under `results/jobs/`.

```bash
python workflow_app.py
```

Your default browser should open **http://127.0.0.1:5050** automatically after a second; open that URL yourself if it does not. The server listens only on your machine (`127.0.0.1`), not on the network. The job status page shows a **progress bar** (estimated from the log and, for scrape, the targets CSV row count) and streams the log text while the run is active.


### Pausing and resuming

- **Fetch URLs:** Safe to stop (Ctrl+C or closing the terminal). Run again with the same `--output` path. The script keeps a `*.csv.state.json` file next to your CSV and skips archive **day pages** already completed; it also skips article URLs already in the CSV. If the log says **0 pending** for a month but the calendar had day links, every day in that month is already in the state file (fetch is done for that month). To force a month to be crawled again you would need to remove those day entries from the state file or delete the state file (only if you understand that you may re-download overlapping URLs).
- **Scrape articles:** Safe to stop. Run again with the same `--scrape-out` path. Already-downloaded URLs are skipped.
- **Clean CSV:** The script builds the full output in memory before writing. If it is interrupted, run **clean** again with the same `--input` and `--output` when ready.

---

## What each script does

| Step | Script | Purpose |
|------|--------|---------|
| 1 | `fetch_nypost_archive_urls.py` | Walk archive pages and save article URLs to a CSV (resumable). |
| 2 | `nypost_scrape.py` | Request each URL, extract title and body text to a CSV. |
| 3 | `clean_articles.py` | Strip boilerplate, drop short or empty rows, dedupe by title, year, outlet. |

Run the steps in order. Step 2 needs the CSV from step 1. Step 3 needs the CSV from step 2.

---

## Command-line usage

If you prefer the terminal instead of the web UI, run the scripts directly. Use the same `--start-year` and `--end-year` in fetch and scrape for a given run. Pick a **basename** for output files (examples use `NAME`) so parallel runs do not overwrite each other.

### 1. Fetch URLs

Creates `results/nypost_urls_NAME.csv` and a resume state file next to it.

```bash
python fetch_nypost_archive_urls.py \
  --start-year START \
  --end-year END \
  --output results/nypost_urls_NAME.csv
```

Optional: lower `--workers` or add `--delay` if requests fail. See `python fetch_nypost_archive_urls.py --help`.

### 2. Scrape articles

Builds targets from the URL file, then downloads pages.

```bash
python nypost_scrape.py \
  --input-urls results/nypost_urls_NAME.csv \
  --start-year START \
  --end-year END \
  --targets-out results/nypost_targets_NAME.csv \
  --scrape-out results/nypost_articles_NAME.csv \
  --failures-out results/nypost_articles_NAME_failures.csv
```

Scraping appends to `--scrape-out` and skips URLs already stored there, so you can restart safely. See `python nypost_scrape.py --help` for `--workers` and timeouts.

### 3. Clean

```bash
python clean_articles.py \
  --input results/nypost_articles_NAME.csv \
  --output results/nypost_articles_NAME_clean.csv \
  --min-words 30
```

Optional: add `--drop-dialogue-rows` to remove rows that look like transcripts. See `python clean_articles.py --help`.

### Example (concrete paths)

`NAME=myrun`, `START=2010`, `END=2015`:

```bash
python fetch_nypost_archive_urls.py --start-year 2010 --end-year 2015 --output results/nypost_urls_myrun.csv
python nypost_scrape.py --input-urls results/nypost_urls_myrun.csv --start-year 2010 --end-year 2015 --targets-out results/nypost_targets_myrun.csv --scrape-out results/nypost_articles_myrun.csv --failures-out results/nypost_articles_myrun_failures.csv
python clean_articles.py --input results/nypost_articles_myrun.csv --output results/nypost_articles_myrun_clean.csv --min-words 30
```
