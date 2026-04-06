# New York Post article workflow

Scripts to collect article URLs from the NY Post archive, download HTML for each article, and clean the text for analysis. Python 3.9+ recommended.

---

## One-time setup

From this project folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows, activate with `.venv\Scripts\activate` instead of `source .venv/bin/activate`.

---

## Web UI (optional)

You can start a local page with your name, your year range, and the step to run. It uses the same Python scripts as the command line and writes logs under `results/jobs/`. The home page explains the three steps and how to resume after a pause.

```bash
python workflow_app.py
```

Your default browser should open **http://127.0.0.1:5050** automatically after a second; open that URL yourself if it does not. The server listens only on your machine (`127.0.0.1`), not on the network. The job status page shows a **progress bar** (estimated from the log and, for scrape, the targets CSV row count) and streams the log text while the run is active.

Requires `flask` (included in `requirements.txt`).

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

## Team: year ranges (1999 through 2022)

The project target window is **1999 to 2022** (24 years). With **seven** people, it is split into **three four-year blocks** and **four three-year blocks** (every year appears exactly once).

| Person | Years (inclusive) |
|--------|-------------------|
| Salena | 1999, 2000, 2001, 2002 |
| Joice | 2003, 2004, 2005 |
| Bhuwan | 2006, 2007, 2008 |
| Addis | 2009, 2010, 2011 |
| Shiyu | 2012, 2013, 2014, 2015 |
| Farnoosh | 2016, 2017, 2018, 2019 |
| Carey | 2020, 2021, 2022 |

Use the same `--start-year` and `--end-year` in fetch and scrape for your block. Name your files so they do not overwrite someone else's (examples below use first names).

---

## Commands for your year block

Replace `NAME`, `START`, and `END` using the table above. Example for **Salena**: `NAME=salena`, `START=1999`, `END=2002`.

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

Builds targets from the URL file, then downloads pages. Output paths are explicit so runs do not clash.

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

---

## Copy-paste examples (paths use each person's name)

**Salena (1999 to 2002)**

```bash
python fetch_nypost_archive_urls.py --start-year 1999 --end-year 2002 --output results/nypost_urls_salena.csv
python nypost_scrape.py --input-urls results/nypost_urls_salena.csv --start-year 1999 --end-year 2002 --targets-out results/nypost_targets_salena.csv --scrape-out results/nypost_articles_salena.csv --failures-out results/nypost_articles_salena_failures.csv
python clean_articles.py --input results/nypost_articles_salena.csv --output results/nypost_articles_salena_clean.csv --min-words 30
```

**Joice (2003 to 2005)**

```bash
python fetch_nypost_archive_urls.py --start-year 2003 --end-year 2005 --output results/nypost_urls_joice.csv
python nypost_scrape.py --input-urls results/nypost_urls_joice.csv --start-year 2003 --end-year 2005 --targets-out results/nypost_targets_joice.csv --scrape-out results/nypost_articles_joice.csv --failures-out results/nypost_articles_joice_failures.csv
python clean_articles.py --input results/nypost_articles_joice.csv --output results/nypost_articles_joice_clean.csv --min-words 30
```

**Bhuwan (2006 to 2008)**

```bash
python fetch_nypost_archive_urls.py --start-year 2006 --end-year 2008 --output results/nypost_urls_bhuwan.csv
python nypost_scrape.py --input-urls results/nypost_urls_bhuwan.csv --start-year 2006 --end-year 2008 --targets-out results/nypost_targets_bhuwan.csv --scrape-out results/nypost_articles_bhuwan.csv --failures-out results/nypost_articles_bhuwan_failures.csv
python clean_articles.py --input results/nypost_articles_bhuwan.csv --output results/nypost_articles_bhuwan_clean.csv --min-words 30
```

**Addis (2009 to 2011)**

```bash
python fetch_nypost_archive_urls.py --start-year 2009 --end-year 2011 --output results/nypost_urls_addis.csv
python nypost_scrape.py --input-urls results/nypost_urls_addis.csv --start-year 2009 --end-year 2011 --targets-out results/nypost_targets_addis.csv --scrape-out results/nypost_articles_addis.csv --failures-out results/nypost_articles_addis_failures.csv
python clean_articles.py --input results/nypost_articles_addis.csv --output results/nypost_articles_addis_clean.csv --min-words 30
```

**Shiyu (2012 to 2015)**

```bash
python fetch_nypost_archive_urls.py --start-year 2012 --end-year 2015 --output results/nypost_urls_shiyu.csv
python nypost_scrape.py --input-urls results/nypost_urls_shiyu.csv --start-year 2012 --end-year 2015 --targets-out results/nypost_targets_shiyu.csv --scrape-out results/nypost_articles_shiyu.csv --failures-out results/nypost_articles_shiyu_failures.csv
python clean_articles.py --input results/nypost_articles_shiyu.csv --output results/nypost_articles_shiyu_clean.csv --min-words 30
```

**Farnoosh (2016 to 2019)**

```bash
python fetch_nypost_archive_urls.py --start-year 2016 --end-year 2019 --output results/nypost_urls_farnoosh.csv
python nypost_scrape.py --input-urls results/nypost_urls_farnoosh.csv --start-year 2016 --end-year 2019 --targets-out results/nypost_targets_farnoosh.csv --scrape-out results/nypost_articles_farnoosh.csv --failures-out results/nypost_articles_farnoosh_failures.csv
python clean_articles.py --input results/nypost_articles_farnoosh.csv --output results/nypost_articles_farnoosh_clean.csv --min-words 30
```

**Carey (2020 to 2022)**

```bash
python fetch_nypost_archive_urls.py --start-year 2020 --end-year 2022 --output results/nypost_urls_carey.csv
python nypost_scrape.py --input-urls results/nypost_urls_carey.csv --start-year 2020 --end-year 2022 --targets-out results/nypost_targets_carey.csv --scrape-out results/nypost_articles_carey.csv --failures-out results/nypost_articles_carey_failures.csv
python clean_articles.py --input results/nypost_articles_carey.csv --output results/nypost_articles_carey_clean.csv --min-words 30
```

---

## Merging later

After everyone finishes, concatenate the `*_clean.csv` files (same header row) or load them as separate tables in your analysis tool. Coordinate a shared `results/` folder or combine copies with the same column names.

---

## File map

| Script | Role |
|--------|------|
| `fetch_nypost_archive_urls.py` | Build a URL list from the live archive |
| `nypost_scrape.py` | Download HTML and extract article text to CSV |
| `clean_articles.py` | Normalize and filter article bodies |
| `workflow_app.py` | Optional local web form to pick name and step (see Web UI above) |
