# Data dictionary

Column definitions for every output file this pipeline produces. See
`METHODOLOGY.md` for how the data was collected and its known limitations.

## `master_addresses.csv` (the main analysis file)

One row per officer/director listed on an entity's Articles of
Organization. Produced by `scraper/build_master.py`.

| Column | Meaning |
|---|---|
| `state_id` | 9-digit Secretary of the Commonwealth ID number |
| `entity_name` | Entity's legal name |
| `entity_type` | e.g. "Nonprofit Corporation" (from the Secretary's export) |
| `date_of_organization` | Date filed, MM-DD-YYYY (from the Secretary's export) |
| `active_flag` | Y/N - entity's current active status (from the Secretary's export, i.e. *today*, not at filing time) |
| `inactive_type` | Reason for inactivation, if any (e.g. "Involuntary Dissolution by Court Order") |
| `inactive_date` | Date inactivated, if any |
| `current_principal_addr1/2/city/state/zip` | Entity's **current** principal office address, per the Secretary's export - this updates whenever the filer amends it, so it is not necessarily what was on the original Articles of Organization |
| `current_agent_name`, `current_agent_addr1/2/city/state/zip` | Entity's **current** resident agent, same caveat as above |
| `title` | PRESIDENT / TREASURER / CLERK / DIRECTOR |
| `person_name` | Officer/director's name as printed on the filing |
| `filed_residential_address` | Address **as stated on the original Articles of Organization** - the actual research-relevant field, since it reflects the address at time of filing rather than whatever is on file today |
| `filed_po_address` | Post-office address as stated on the same filing, when the form and the address format allowed it to be split out (see `METHODOLOGY.md` re: the foreign-address limitation) |
| `filed_extra_address_lines` | Any address-shaped text beyond the two expected blocks (rare; worth a manual look when non-empty) |
| `term_expires` | As printed on the filing, when present |
| `res_missing` | 1 if no residential address block was found for this person |
| `res_pobox` | 1 if the residential address contains a PO box / PMB |
| `res_co` | 1 if the residential address contains "c/o" / "care of" |
| `res_eq_principal` | 1 if the residential address matches the entity's *current* principal office address |
| `res_eq_agent` | 1 if the residential address matches the entity's *current* resident agent's address |
| `res_eq_po` | 1 if the residential address matches this same person's post-office address |
| `res_unit` | 1 if the residential address mentions a suite/floor/unit/apartment number - weak signal, common for both legitimate apartments and business offices |
| `extraction_source` | `text` (native PDF text) or `ocr` (Tesseract OCR - worth spot-checking, OCR can misread digits) |
| `filing_no` | Secretary's office filing number for this specific document |

All `res_*` flags are heuristics meant to prioritize manual review, not
legal conclusions - e.g. `res_eq_principal` = 1 is expected and normal for
a small org run out of someone's home.

## `needs_review.csv`

Every entity still needing a human, combined into one file. Produced by
`scraper/build_review_list.py`.

| Column | Meaning |
|---|---|
| `state_id` | ID number, when known (blank for some `not_downloaded` rows where the search never resolved to a page displaying one) |
| `entity_name` | Best available name (search term used, or from the source file) |
| `date_of_organization` | From the source file, when the ID was known |
| `stage` | `not_downloaded` (never got a PDF) or `not_parsed` (got a PDF, couldn't extract structured rows) |
| `reason` | The specific status - see status vocabularies below |
| `note` | Free-text detail, when the scraper logged one |
| `pdf_path` | Path to the downloaded PDF, for `not_parsed` rows - open this directly to read Article VII yourself |
| `reviewed` | Blank - fill in as you work through the list |
| `reviewer_notes` | Blank - your notes |

## `repeated_addresses.csv`

One row per residential address used by 2+ distinct entities (threshold
configurable via `--min-entities`). Produced by
`scraper/find_repeated_addresses.py`. Sorted by `distinct_entities`
descending - the addresses most worth looking at are at the top.

| Column | Meaning |
|---|---|
| `normalized_address` | Address with punctuation/abbreviations normalized, used as the grouping key |
| `example_raw_address` | One real example of how the address appears on a filing |
| `distinct_entities` | Number of different entities (by `state_id`) using this address as an officer's residential address |
| `total_officer_rows` | Total officer/director rows at this address (can exceed `distinct_entities` if the same address covers multiple officers at one entity) |
| `state_ids` | Semicolon-separated list of every entity ID at this address |
| `people_and_entities` | Up to 25 "Name (Entity)" pairs at this address, for a quick read of who/what's here |
| `any_unit_suffix` | 1 if any row at this address had a suite/floor/unit marker |
| `any_pobox` | 1 if any row was flagged as a PO box |
| `any_care_of` | 1 if any row was flagged "c/o" |
| `any_matches_resident_agent` | 1 if any row's residential address also matched that entity's current resident agent address |

## `summary_stats.txt`

Plain-text, human-readable rollup - pipeline funnel counts, entity-type
and organization-year breakdowns, address-flag rates, and the top 10
repeated addresses. Meant to be read, not analyzed further. Produced by
`scraper/build_summary_stats.py`.

## Status vocabularies

**`results.csv` (scraping)**
| Status | Meaning |
|---|---|
| `ok` | PDF downloaded |
| `unresolved` | Name search returned zero or multiple non-exact candidates |
| `id_mismatch` | Landed on a summary page whose displayed ID didn't match the expected ID - likely a reused/duplicate entity name; PDF was **not** downloaded |
| `no_articles` | Entity found, but no Articles of Organization filing listed |
| `no_pdf` | Filing row found, but the PDF request didn't return a PDF |
| `error` | Unexpected exception during scraping |

**`parse_log.csv` (parsing)**
| Status | Meaning |
|---|---|
| `ok` | Parsed from native PDF text |
| `ok_ocr` | Parsed after OCR (some pages were scanned images) |
| `no_table` | Real text found (no OCR needed), but no matching officer table found |
| `no_table_ocr` | Same, but only after OCR - usually a different filing template (attorney-drafted, DocuSign-signed, etc.) that defers officer info elsewhere |
| `needs_ocr` | OCR was attempted and still found nothing usable - genuinely blank/corrupt/unreadable |
| `error` | Unexpected exception during parsing |
