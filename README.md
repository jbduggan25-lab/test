# MA nonprofit Articles of Organization scraper

Pulls the original Articles of Organization filing for each nonprofit in a
Secretary of the Commonwealth export, then extracts each officer/director's
address **as stated on that original filing** — which is not the same as the
`Addr1`/`AgentAddr` columns in the export, since those reflect the entity's
*current* record and get overwritten whenever the filer updates it.

## Why this exists

The Secretary's bulk export gives you the entity's current principal-office
and resident-agent address, but not what was on the original Articles of
Organization. This pipeline downloads that original PDF per entity from
corp.sec.state.ma.us and parses the residential address of each initial
officer/director out of Article VII(b) of the form.

## ⚠️ Read before running at scale: robots.txt

The site's robots rules disallow automated access to the summary and filing-
list pages this script depends on (`CorpSummary.aspx`, `CorpSearchFormList.aspx`).
This script runs one entity at a time with a delay to stay as light as
possible, but that does not make it robots-compliant. Before running this
across your full 2021–2026 list (potentially thousands of entities):

- Read the Corporations Division's actual terms of use yourself.
- Consider asking the Division for a bulk data/records extract instead —
  likely both more compliant and more reliable than scraping one entity at a
  time regardless of ToS, and not fragile to a future site redesign.
- If you do scrape, keep batches small and keep the delay between requests.

## What's confirmed vs. still inferred

`scraper/scrape_articles.py`'s docstring marks this explicitly:
- **Confirmed** from live page source: the entity-summary page
  (`CorpSummary.aspx`) field IDs, the "Articles of Organization" filing code,
  and the filing-list page (`CorpSearchFormList.aspx`) row/link structure.
- **Inferred**: the search-page (`CorpSearch.aspx`) text box and results-grid
  layout, marked `### VERIFY ###` in the code. If it doesn't match, the
  script pauses with the browser open (it always runs non-headless) so you
  can click through manually and then resume — no separate debug step
  needed for that part.

The address-parsing regexes in `scraper/parse_articles.py` (field labels,
the residential/post-office split, the compliance-flag heuristics) are
tuned to the standard MA online-filed Articles of Organization template.
Spot-check `data/output/parsed_officers.csv` against a few source PDFs
before trusting it at scale.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium

# parse_articles.py shells out to poppler's pdftotext (more reliable than any
# Python PDF library we tried against these real filings - see its docstring)
brew install poppler          # macOS
# apt-get install poppler-utils   # Ubuntu/Debian
```

The scraper always opens a visible browser window (`headless=False`) so you
can watch/intervene on ambiguous matches. On a server with no display, run
it under Xvfb: `xvfb-run -a python scraper/scrape_articles.py entities.csv`

## Usage

```bash
# 1. Download PDFs (resumable - re-running skips entities already logged "ok",
#    keyed by ID Number)
python scraper/scrape_articles.py entities.csv

# 2. Extract officer/director addresses from whatever PDFs were downloaded
#    (pass entities.csv too so the review list has entity names, not just IDs)
python scraper/parse_articles.py data/pdfs entities.csv
```

`entities.csv` is the Secretary's office export as-is (needs at least
`ID Number`, `Entityname`, `DateOfOrganization`).

### How matching works, and why there's an ID cross-check

The site's search only takes an entity name, not the ID number you already
have — so step 1 searches by exact name, and falls back to "begins with" if
that returns nothing. Since MA name matches are usually unique this works
most of the time, but a name can theoretically be reused (e.g. after a prior
entity by that name dissolved), which would silently attribute the wrong
filing to your row. To guard against that, after landing on the summary page
the script compares its displayed ID against the `ID Number` your row
expected; a mismatch is logged as `id_mismatch` in `results.csv` and the PDF
is **not** downloaded, so it surfaces for manual review instead of quietly
attaching the wrong entity's data.

### Outputs

`data/output/results.csv` — one row per input entity: `status` is `ok`,
`unresolved` (name search needs a human), `id_mismatch` (see above),
`no_articles`, `no_pdf`, or `error`.

`data/output/entities.csv` / `officers.csv` — fields read straight off the
summary page (principal office, resident agent, the officers/directors grid)
for cross-reference against the PDF parse.

`data/output/parsed_officers.csv` — one row per person listed in Article
VII(b) of the PDF, with `residential_address` and `po_address` split out,
plus review flags: `res_missing`, `res_pobox`, `res_co`, `res_eq_principal`,
`res_eq_agent`, `res_eq_po`, `res_unit`. These are heuristics to prioritize
review, not conclusions — e.g. `res_eq_principal` just means the residential
address matches the principal office address, which is expected and fine for
a small org run out of someone's home.

The residential/post-office split only works for US-format addresses
(it looks for a "CITY, ST 12345" line as the boundary between the two).
A foreign address (seen for real on one filing with Hong Kong/China-based
officers) won't split — the full text is preserved, but lands entirely in
`residential_address` with `po_address` left blank.

`data/output/parsed_entities.csv` — one row per filing with roll-up counts
of the above flags.

`data/output/parse_log.csv` — per-PDF status, and your manual-review list:
filter for `status` in (`needs_ocr`, `no_table`) to get every entity that
needs a human to open the PDF and read Article VII directly.
- `needs_ocr`: the filing's actual content (everything past the state's
  generic cover certificate page, which appears as both the first and last
  page of every PDF) has under 200 characters of extracted text — i.e. it's
  a scanned image, not an e-filed form with a text layer. This script does
  not OCR those.
- `no_table`: real text was found, but nothing matched the expected
  Article VII(b) row pattern — could mean the filing genuinely left
  officers/directors blank, or a layout this script doesn't handle yet.

## Privacy note

These addresses become public the moment they're filed, but you're
compiling individuals' home addresses into one dataset. Worth being
deliberate about storage, access, and whether any of this gets published or
shared downstream.
