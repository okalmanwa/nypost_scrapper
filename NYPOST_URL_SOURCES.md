# NY Post article URL sources

**Primary:** use `fetch_nypost_archive_urls.py`. It walks month, then day archive pages, and writes `results/nypost_archive_urls_2000_2025.csv` (with `.state.json` for resume). See that script's `--help`.

Any CSV with a `url` column can be used as `--input-urls` for `nypost_scrape.py` (valid nypost.com article URLs, drops sports, entertainment, Page Six, video, and photos paths via `BLOCK_RE`, then lists every remaining URL between `--start-year` and `--end-year`). The scraper sets outlet to New York Post and lean to right.

**After you have a URL CSV:**

```bash
python nypost_scrape.py \
  --input-urls results/nypost_archive_urls_2000_2025.csv
```
