# MA nonprofit Articles of Organization scraper

Pulls the original Articles of Organization filing for each nonprofit in a
Secretary of the Commonwealth export, then extracts the address(es) as they
were stated **at the time of initial filing** — which is not the same as the
`Addr1`/`AgentAddr` columns in the export, since those reflect the
entity's *current* record and get overwritten whenever the filer updates it.

## Why this exists

The Secretary's bulk export gives you the entity's current principal-office
and resident-agent address, but not what was on the original Articles of
Organization. This pipeline downloads that original PDF per entity from
corp.sec.state.ma.us and parses the incorporator / resident-agent address
block out of it.

## ⚠️ Not yet verified against the live site

`scraper/scrape_articles.py` was written without the ability to load
corp.sec.state.ma.us from the environment it was authored in (network policy
blocked that domain). It's built on the general shape of that site (an
ASP.NET WebForms search form, a per-entity filing-history table with PDF
links) using resilient accessible-role/label selectors rather than hardcoded
element IDs, but the exact label text and table structure need confirming.

**Before running the full list:**

```bash
python scraper/scrape_articles.py --input entities.csv --limit 3 --headed
```

`--headed` opens a real browser window so you can watch it search and click.
If a step can't find what it's looking for, it writes a screenshot + full
page HTML to `data/debug/<id>_<step>.png` / `.html` instead of guessing. If
that happens, send me those files (or just paste the relevant HTML) and I'll
fix the selector — I can't browse the site myself to pre-verify this.

Likely failure points, in order of how early they'd surface:
1. `search_by_id()` — the label text for the ID-number search field
   (currently matches "Data Number" / "Entity ID" / "ID Number").
2. `find_articles_pdf_url()` — assumes filings are in `<tr>` rows and the
   row's text contains "Articles of Organization" plus the filing date.
3. `download_pdf()` — assumes either a direct PDF link or a postback that
   triggers a download / opens a new tab.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium

# OCR fallback deps (only needed if some PDFs turn out to be scanned images
# rather than e-filed text):
#   macOS:   brew install poppler tesseract
#   Ubuntu:  apt-get install poppler-utils tesseract-ocr
```

## Usage

```bash
# 1. Download PDFs (resumable - re-running skips rows already logged "ok")
python scraper/scrape_articles.py \
    --input entities.csv \
    --output-dir data/pdfs \
    --log data/scrape_log.csv \
    --delay 3 --jitter 2

# 2. Extract addresses from whatever PDFs were downloaded
python scraper/extract_addresses.py \
    --pdf-dir data/pdfs \
    --input entities.csv \
    --output data/output/addresses.csv
```

`entities.csv` should have the same columns as the Secretary's export
(`ID Number`, `Entityname`, `DateOfOrganization`, ...) — export your data to
CSV and point `--input` at it.

### Output

`data/output/addresses.csv` has one row per PDF with:
- `incorporator_address` / `resident_agent_address` — parsed address, when a
  labeled block was found and matched an address pattern.
- `confidence` — `labeled_match` or `manual_review`. Rows marked
  `manual_review` need a human to check `data/text/<id>.txt` (the raw
  extracted text is always saved there, whether or not parsing succeeded).
- `text_source` — `text_layer` (e-filed PDF, extracted directly) or `ocr`
  (scanned image, fell back to Tesseract).

The address-parsing regexes in `LABEL_PATTERNS` / `ADDRESS_LINE_RE`
(`scraper/extract_addresses.py`) are a starting point based on the standard
MA Articles of Organization template, not verified against a real filed
form. Run on 3-5 PDFs first, check the `manual_review` rows, and send me a
couple of sample `data/text/*.txt` files if the labels don't match — I'll
tighten the patterns.

## Rate limiting and terms of use

`--delay`/`--jitter` add a randomized pause between entities by default
(~3-5s). Check corp.sec.state.ma.us's terms of use / robots.txt yourself
before running this at full scale (thousands of entities) — I couldn't check
from this session. If the office offers a bulk data request for this kind of
filing detail, that's likely more reliable than scraping one entity at a
time regardless of ToS, since the scraper is also fragile to any future site
redesign.

## Privacy note

These become public the moment they're filed, but you're compiling
individuals' home addresses into one dataset. Worth being deliberate about
storage, access, and whether any of this gets published or shared
downstream.
