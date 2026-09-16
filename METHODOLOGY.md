# Methodology

## Data source

- **Starting list**: an export from the Massachusetts Secretary of the
  Commonwealth's office of nonprofit corporations organized between
  1/1/2021 and 6/30/2026 (~12,979 entities), including each entity's ID
  number, name, active status, and current address/resident agent on
  file.
- **Primary documents**: each entity's Articles of Organization PDF,
  retrieved individually from the Corporations Division's public search
  system at `corp.sec.state.ma.us`.

## Why this exists

The starting list's address fields (`Addr1`, `AgentAddr1`, etc.) reflect
each entity's **current** record, which is overwritten whenever the filer
updates it. This project instead extracts the officer/director residential
addresses **as they were stated on the original Articles of Organization**
- the address at the time of filing, which can differ from what's on file
today.

## Collection method

1. **Scraping** (`scraper/scrape_articles.py`): automated (Playwright)
   lookup of each entity by name on the Corporations Division's public
   search, cross-checked against the expected ID number to guard against
   a reused/duplicate entity name silently attaching the wrong filing, then
   download of the Articles of Organization PDF. Run in supervised
   sessions over several days in September 2026, since the site presents
   a CAPTCHA challenge periodically that requires a human to solve it -
   this was not bypassed or automated.
2. **Parsing** (`scraper/parse_articles.py`): text extraction via
   poppler's `pdftotext -layout`, with officer/director rows parsed
   directly from the layout-preserved text (title keywords as row
   boundaries) rather than relying on any detected table structure, since
   real filings vary in whether Article VII(b) is rendered as a
   ruled table at all. Pages with no usable native text (scanned images)
   fall back to Tesseract OCR.
3. **Consolidation** (`scraper/build_master.py`,
   `build_review_list.py`, `find_repeated_addresses.py`,
   `build_summary_stats.py`): merges the extracted data with the source
   list and produces the analysis-ready outputs described in
   `DATA_DICTIONARY.md`.

## Known limitations

- **Format diversity.** ~15% of downloaded filings did not parse into
  structured rows even after OCR. Manually inspecting several of these
  found they are frequently a different underlying document - an
  attorney-prepared, DocuSign-signed filing with a different table format,
  sometimes deferring officer names/addresses to a separate "Continuation
  Sheet" attachment - rather than the state's own online form template.
  These are flagged in `needs_review.csv` (`stage=not_parsed`), not
  silently dropped.
- **Foreign addresses don't split.** The residential/post-office address
  split (`filed_residential_address` vs. `filed_po_address`) relies on
  detecting a "CITY, ST 12345" line as the boundary. Non-US addresses
  (encountered for real in this dataset) don't split - the full address
  text is preserved but lands entirely in `filed_residential_address`.
- **OCR accuracy.** Rows with `extraction_source=ocr` were read by
  Tesseract OCR rather than extracted from native PDF text. OCR can
  misread characters, particularly digits in street numbers and zip
  codes - these rows are worth spot-checking against the source PDF
  before treating a specific address as authoritative.
- **Not every entity has a PDF.** ~1.8% of entities in the source list
  never yielded a PDF at all - ambiguous name searches, ID mismatches
  caught by the safety check, or no Articles of Organization filing found
  on record. These are in `needs_review.csv` (`stage=not_downloaded`).
- **Site terms of use.** The Corporations Division's robots rules disallow
  automated access to the pages this pipeline depends on. Requests were
  rate-limited and run with a human present to handle CAPTCHA challenges,
  but this is still automated bulk access and should be represented as
  such in any resulting research.

## Provenance of individual claims

Every extracted address traces back to a specific filing: `filing_no` in
`master_addresses.csv` is the Secretary's office filing number, and the
source PDF for any entity is at `data/pdfs/<state_id>.pdf`. When in doubt
about a specific row, the source document is the ground truth.
