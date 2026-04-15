#!/usr/bin/env python3
"""
Clean scraped New York Post article CSVs for modeling and analysis.

**Input:** Any CSV with at least ``title``, ``year``, ``outlet``, ``lean``, and ``content``
(plus optional columns such as ``url``). Rows are read from ``--input``.

**Per row,** ``clean_content`` removes common boilerplate (newsletter CTAs, wire-style
leaders, standalone photo credits, repeated Published/Updated lines, trailing outlet
signatures, transcript-style lines when present). It then merges broken lines into
paragraphs and collapses extra blank lines.

**Row filters (always on):** empty body after cleaning; fewer than ``--min-words`` words.

**Optional:** ``--drop-dialogue-rows`` drops entire rows that look like focus-group or
podcast transcripts (speaker labels, moderator/participant patterns, podcast keywords).

**Output:** Core columns ``title``, ``year``, ``outlet``, ``lean``, ``content`` plus any
extra input columns except GDELT-era fields listed in ``DROPPED_COLS``. Duplicate rows
(same title, year, outlet) keep the first only.

**Usage:**

  python clean_articles.py
  python clean_articles.py --input results/nypost_articles_2000_2025.csv
  python clean_articles.py --output results/nypost_articles_clean.csv --min-words 30
"""

import argparse
import csv
import os
import sys

csv.field_size_limit(min(2147483647, sys.maxsize))

import re
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = os.path.join(SCRIPT_DIR, "results", "nypost_articles_2000_2025.csv")
MIN_WORDS_DEFAULT = 20

# Columns we never write (legacy GDELT fields)
DROPPED_COLS = {"event_code", "event_label", "actor1_name", "actor2_name"}
OUTPUT_COLS = ["title", "year", "outlet", "lean", "content"]

# --- Generic / wire-style noise at start of body ---
NEWSLETTER_CTA = re.compile(r"Sign up (?:here )?to get[^\n]*\n?", re.IGNORECASE)
LEADING_TIMESTAMP = re.compile(r"^(\s*(?:\d{1,2}:\d{2}\s*)?(?:am|pm)\s*ET\s*)+", re.IGNORECASE)
LEADING_FILE = re.compile(r"^(?:FILE\s*:\s*UNDATED\s*:\s*|FILE\s*:\s*|FILE\s*-\s*-\s*[,\s]*|FILE\s*-\s+)", re.IGNORECASE)
LEADING_IMAGE_COUNT = re.compile(r"^Image\s+\d+\s+of\s+\d+\s*(?:FILE\s*-\s*)?[,.]?\s*", re.IGNORECASE)
LEADING_PHOTO = re.compile(r"^'?\(?Photo\s+courtesy[^)'\n]*[)']?\)?\s*", re.IGNORECASE)
LEADING_NEW = re.compile(r"^NEW\s*:\s*", re.IGNORECASE)
LEADING_DATELINE = re.compile(r"^[A-Z][A-Z ,\.]{2,40}(?:\([A-Z]+\))?\s*[—–-]\s+")
LEADING_SPECIAL = re.compile(r'^[^\w\'"(]+')

# --- NY Post: byline date lines that duplicate the page header ---
NYPOST_PUBLISHED_LINE = re.compile(
    r"^Published\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},?\s+\d{4}\s*\.?\s*$",
    re.I,
)
NYPOST_UPDATED_LINE = re.compile(
    r"^Updated\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},?\s+\d{4},?\s+.*$",
    re.I,
)

TRAILING_NYPOST = re.compile(
    r"\s*[—–\-]\s*(?:New\s+York\s+Post|NY\s*Post|nypost\.com)[\s.]*$",
    re.I,
)

PHOTO_CREDIT_STANDALONE = re.compile(
    r"^[^.]{0,90}\s*[—/]\s*(Getty Images|AFP|Reuters|Agence France-Presse|AP|UPI)(\s*[—/]\s*Getty Images)?\s*$",
    re.I,
)
LEADING_BYLINE_LINE = re.compile(
    r"^By\s+"
    r"(?:(?:[A-Z][A-Za-z'\.-]+|[A-Z]\.)\s+){1,5}"
    r"(?:[A-Z][A-Za-z'\.-]+|[A-Z]\.)"
    r"(?:\s+(?:in|at)\s+[^\n]{1,80})?"
    r"(?:\s+and\s+"
    r"(?:(?:[A-Z][A-Za-z'\.-]+|[A-Z]\.)\s+){1,5}"
    r"(?:[A-Z][A-Za-z'\.-]+|[A-Z]\.)"
    r"(?:\s+(?:in|at)\s+[^\n]{1,80})?"
    r")?"
    r"\s*$"
)
LEADING_BYLINE_PREFIX = re.compile(
    r"^By\s+"
    r"(?:(?:[A-Z][A-Za-z'\.-]+|[A-Z]\.)\s+){1,4}"
    r"(?:[A-Z][A-Za-z'\.-]+|[A-Z]\.)"
    r"(?:\s+(?:in|at)\s+[A-Z][A-Za-z\-\s\.'’]{1,60})?"
    r"(?:\s+and\s+"
    r"(?:(?:[A-Z][A-Za-z'\.-]+|[A-Z]\.)\s+){1,4}"
    r"(?:[A-Z][A-Za-z'\.-]+|[A-Z]\.)"
    r"(?:\s+(?:in|at)\s+[A-Z][A-Za-z\-\s\.'’]{1,60})?"
    r")?"
    r"\s+"
)
LEADING_PHOTO_CREDIT_LINE = re.compile(
    r"^(?:Photo by|Photo:|Image courtesy(?: of)?|Image credit:|Credit:)\s+"
    r"[^\n]{0,180}\s*"
    r"(?:Getty Images|Reuters|Associated Press|AP|UPI|AFP|Shutterstock)\b.*$",
    re.I,
)

TRAILING_TICKETS = re.compile(r"\n[^\n]*Tickets?\s*:\s*\$\d+\s+to\s+\$\d+[^\n]*\s*$", re.IGNORECASE)

PARENS = re.compile(r"\([^)]{0,200}\)")
MULTI_BLANK = re.compile(r"\n{3,}")

# --- Focus-group / roundtable / podcast transcript patterns ---
SPEAKER_LINE = re.compile(r"^[A-Za-z][A-Za-z\s]{1,35}:\s*", re.MULTILINE)
MODERATOR_LINE = re.compile(r"^Moderator\s*,?\s*[A-Za-z\.\s]+\s*$", re.IGNORECASE)
PARTICIPANT_LABEL = re.compile(
    r"^[A-Za-z][A-Za-z\.\s']*\s*,?\s*\d{1,3}\s*,\s*[A-Za-z\.\s]+\s*,?\s*"
    r"(?:Black|white|Latino|Asian|Asian Pacific Islander)(?:\s*,\s*[A-Za-z\.\s]+)?\s*$",
    re.IGNORECASE,
)
RAISED_HANDS_LINE = re.compile(r"^\d+\s+people\s+raised\s+their\s+hands\.?\s*$", re.IGNORECASE)
PARTICIPANTS_HEADING = re.compile(r"^Participants\s*$", re.IGNORECASE)
STANDALONE_QUOTED_ANSWER = re.compile(r"^[\u201C\"][^\u201C\u201D\"]{0,40}[\u201D\"]\s*$")
PARTICIPANT_LABEL_CONTINUATION = re.compile(
    r"^\d{1,3}\s*,\s*[A-Za-z\.\s]+\s*,?\s*"
    r"(?:Black|white|Latino|Asian|Asian Pacific Islander)(?:\s*,\s*[A-Za-z\.\s]+)?\s*$",
    re.IGNORECASE,
)
PARTICIPANT_LINE_WITH_QUOTE = re.compile(
    r"^[A-Za-z][A-Za-z\.\s']*\s*,?\s*\d{1,3}\s*,\s*[A-Za-z\.\s]+\s*,?\s*"
    r"(?:Black|white|Latino|Asian|Asian Pacific Islander)[^\u201C\u201D\"]*[\u201C\"][^\u201C\u201D\"]*[\u201D\"]\s*$",
    re.IGNORECASE,
)
STANDALONE_REPORTED = re.compile(r"\nreported\s*\n", re.IGNORECASE)


def _is_dialogue_line(line: str, title: Optional[str] = None):
    """True if a single line looks like transcript metadata, not article prose."""
    stripped = line.strip()
    if not stripped:
        return False
    if title and stripped == title.strip():
        return True
    if MODERATOR_LINE.match(stripped):
        return True
    if PARTICIPANT_LABEL.match(stripped) and len(stripped) < 90:
        return True
    if PARTICIPANT_LINE_WITH_QUOTE.match(stripped):
        return True
    if PARTICIPANT_LABEL_CONTINUATION.match(stripped):
        return True
    if RAISED_HANDS_LINE.match(stripped):
        return True
    if PARTICIPANTS_HEADING.match(stripped):
        return True
    if STANDALONE_QUOTED_ANSWER.match(stripped):
        return True
    return False


def is_predominantly_dialogue(text: str, title: Optional[str] = None, url: str = ""):
    """
    Heuristic: True if the body is mostly dialogue / transcript, not a news article.
    Used only when ``--drop-dialogue-rows`` is set (entire row is skipped).
    """
    if url and "focus-group" in url.lower():
        return True
    if not text or not isinstance(text, str):
        return False
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if len(lines) < 8:
        return False
    sample = (text[:4000] if len(text) > 4000 else text).lower()
    if "get your podcasts" in sample or ("spotify" in sample and "podcast" in sample):
        return True
    if "transcript" in sample and ("podcast" in sample or "conversation" in sample or "edited transcript" in sample):
        return True
    speaker_count = sum(1 for ln in lines if SPEAKER_LINE.match(ln))
    if speaker_count >= 8 and len(lines) >= 12:
        return True
    if speaker_count >= 5 and len(lines) >= 20 and speaker_count / len(lines) >= 0.15:
        return True
    dialogue_count = sum(1 for ln in lines if _is_dialogue_line(ln, title=title))
    if dialogue_count >= 8 or (len(lines) >= 15 and dialogue_count / len(lines) >= 0.25):
        return True
    if "focus group" in sample and ("participants" in sample or "we spoke to" in sample or "moderator" in sample):
        return True
    return False


def _strip_dialogue_blocks(text: str, title: Optional[str] = None):
    """
    Remove focus-group style lines; if the piece is very dialogue-heavy, truncate
    before the first ``Participants`` / ``Moderator`` heading when that appears late.
    """
    lines = [ln.rstrip() for ln in text.split("\n")]
    dialogue_count = sum(1 for ln in lines if _is_dialogue_line(ln, title=title))
    if dialogue_count >= 8:
        cut = None
        for i, ln in enumerate(lines):
            st = ln.strip()
            if PARTICIPANTS_HEADING.match(st) or MODERATOR_LINE.match(st):
                cut = i
                break
        if cut is not None and cut > 3:
            lines = lines[:cut]
    kept = [ln for ln in lines if not _is_dialogue_line(ln, title=title)]
    return "\n".join(kept).strip()


def _strip_leading_loop(s: str):
    """Repeatedly strip wire-style prefixes from the start of the text (bounded passes)."""
    patterns = [
        LEADING_TIMESTAMP,
        LEADING_FILE,
        LEADING_IMAGE_COUNT,
        LEADING_PHOTO,
        LEADING_NEW,
        LEADING_DATELINE,
        LEADING_SPECIAL,
    ]
    for _ in range(10):
        prev = s
        for p in patterns:
            s = p.sub("", s).strip()
        if s == prev:
            break
    return s


def _drop_nypost_meta_lines(s: str):
    """Remove whole lines that are only Published … / Updated … (header echo)."""
    out = []
    for line in s.split("\n"):
        ln = line.strip()
        if not ln:
            out.append(line)
            continue
        if NYPOST_PUBLISHED_LINE.match(ln) or NYPOST_UPDATED_LINE.match(ln):
            continue
        out.append(line)
    return "\n".join(out).strip()


def _strip_leading_byline_and_credits(s: str):
    """Drop first-line bylines/credits that leak from the page header area."""
    m = LEADING_BYLINE_PREFIX.match(s)
    if m:
        s = s[m.end() :].lstrip("—–- \t")
    lines = s.split("\n")
    changed = True
    while lines and changed:
        changed = False
        first = lines[0].strip()
        if not first:
            lines.pop(0)
            changed = True
            continue
        if LEADING_BYLINE_LINE.match(first) or LEADING_PHOTO_CREDIT_LINE.match(first):
            lines.pop(0)
            changed = True
    return "\n".join(lines).strip()


def _line_ends_sentence(line: str):
    t = line.rstrip()
    if not t:
        return False
    return bool(re.search(r'[.!?]["\']?\s*$', t))


def _next_starts_continuation(next_line: str):
    t = next_line.strip()
    if not t:
        return False
    if t[0] in ",;:" or t[0] in "\"'":
        return True
    if t[0].islower():
        return True
    return False


def _normalize_paragraphs(text: str):
    """Join soft line breaks inside a paragraph; keep blank lines as paragraph gaps."""
    lines = text.split("\n")
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        while i + 1 < len(lines):
            next_line = lines[i + 1]
            if not next_line.strip():
                break
            if not _line_ends_sentence(line) or _next_starts_continuation(next_line):
                line = line.rstrip() + " " + next_line.lstrip()
                i += 1
                continue
            line = line.rstrip() + " " + next_line.lstrip()
            i += 1
        out.append(line.strip())
        i += 1
    return "\n\n".join(p for p in out if p)


def _strip_trailing_tags(s: str):
    """Drop short trailing lines that look like UI crumbs (no sentence end)."""
    for _ in range(20):
        prev = s
        lines = s.split("\n")
        while lines:
            last = lines[-1].strip()
            if not last:
                lines.pop()
                continue
            if len(last.split()) <= 5 and not last.endswith((".", "?", "!", '"', "'")):
                lines.pop()
            else:
                break
        s = "\n".join(lines).strip()
        if s == prev:
            break
    return s


def clean_content(text: str, title: Optional[str] = None):
    """
    Return cleaned article body text. Does not enforce minimum length; callers filter.
    Order matters: dialogue strip before parenthesis removal, NY Post meta lines after
    leading strip, normalization last.
    """
    if not text or not isinstance(text, str):
        return ""
    s = text.strip()
    if title and title.strip():
        title_esc = re.escape(title.strip())
        pattern = r"^\s*" + re.sub(r"\\ ", r"\\s+", title_esc) + r"\s+"
        s = re.sub(pattern, "", s, count=1, flags=re.I).strip()

    s = NEWSLETTER_CTA.sub("", s).strip()
    s = TRAILING_TICKETS.sub("", s).strip()
    s = STANDALONE_REPORTED.sub("\n", s).strip()
    s = "\n".join(
        line for line in s.split("\n") if not PHOTO_CREDIT_STANDALONE.match(line.strip())
    ).strip()

    s = _strip_dialogue_blocks(s, title=title)
    s = PARENS.sub("", s)
    s = TRAILING_NYPOST.sub("", s).strip()
    s = _strip_leading_loop(s)
    s = _drop_nypost_meta_lines(s)
    s = _strip_leading_byline_and_credits(s)

    s = _strip_trailing_tags(s)
    s = _normalize_paragraphs(s)
    return MULTI_BLANK.sub("\n\n", s).strip()


def main():
    parser = argparse.ArgumentParser(
        description="Clean NY Post article CSV: strip boilerplate, drop short/empty/duplicate rows.",
    )
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT,
        help=f"Input CSV path (default: {DEFAULT_INPUT}).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV path. If omitted, the input file is overwritten in place.",
    )
    parser.add_argument(
        "--min-words",
        type=int,
        default=MIN_WORDS_DEFAULT,
        help=f"Drop rows whose cleaned content has fewer than this many words (default: {MIN_WORDS_DEFAULT}).",
    )
    parser.add_argument(
        "--drop-dialogue-rows",
        action="store_true",
        help="Drop rows that look like focus-group or podcast transcripts (see is_predominantly_dialogue).",
    )
    args = parser.parse_args()

    input_path = args.input
    output_path = args.output or input_path

    if not os.path.isfile(input_path):
        print(f"[ERROR] File not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    seen = set()
    total = dropped_empty = dropped_short = dropped_dup = dropped_dialogue = cleaned = 0
    rows_out = []

    with open(input_path, "r", encoding="utf-8", newline="", errors="replace") as f:
        reader = csv.DictReader(f)
        input_cols = reader.fieldnames or []
        for row in reader:
            total += 1
            out = {c: (row.get(c) or "").strip() for c in input_cols}
            for c in OUTPUT_COLS:
                out.setdefault(c, "")
            raw = out["content"]
            if getattr(args, "drop_dialogue_rows", False) and is_predominantly_dialogue(
                raw, title=out.get("title"), url=out.get("url") or ""
            ):
                dropped_dialogue += 1
                continue
            cleaned_content = clean_content(raw, title=out.get("title"))
            out["content"] = cleaned_content
            if not cleaned_content:
                dropped_empty += 1
                continue
            if len(cleaned_content.split()) < args.min_words:
                dropped_short += 1
                continue
            if cleaned_content != raw:
                cleaned += 1
            key = (out["title"], out["year"], out["outlet"])
            if key in seen:
                dropped_dup += 1
                continue
            seen.add(key)
            rows_out.append(out)

    extra_cols = [c for c in input_cols if c not in OUTPUT_COLS and c not in DROPPED_COLS]
    final_cols = OUTPUT_COLS + extra_cols
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=final_cols, quoting=csv.QUOTE_NONNUMERIC, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows_out)

    print(
        f"Done.\n"
        f"  Read:              {total}\n"
        f"  Content cleaned:   {cleaned}\n"
        f"  Dropped (empty):   {dropped_empty}\n"
        f"  Dropped (<{args.min_words} words): {dropped_short}\n"
        f"  Dropped (dup):     {dropped_dup}\n"
        f"  Dropped (dialogue): {dropped_dialogue}\n"
        f"  Wrote:             {len(rows_out)}  to  {output_path}"
    )


if __name__ == "__main__":
    main()
