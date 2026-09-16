"""
Build a one-page, human-readable summary of the whole dataset: pipeline
funnel counts, entity breakdowns, and address-flag rates. Meant to be read,
not analyzed further - the actual analysis files are master_addresses.csv,
needs_review.csv, and repeated_addresses.csv.

Usage:
    python scraper/build_summary_stats.py entities.csv

Reads:
    data/output/master_addresses.csv
    data/output/results.csv (if present)
    data/output/parse_log.csv (if present)
    data/output/repeated_addresses.csv (if present - run
        find_repeated_addresses.py first to include this section)
Writes:
    data/output/summary_stats.txt
"""
import csv
import sys
from collections import Counter
from pathlib import Path

OUTPUT_DIR = Path("data/output")


def load_entities_count(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    while rows and not any(cell.strip() for cell in rows[0]):
        rows.pop(0)
    if not rows:
        return 0
    header, data_rows = rows[0], rows[1:]
    idx = header.index("ID Number") if "ID Number" in header else None
    if idx is None:
        return 0
    return sum(1 for r in data_rows if len(r) > idx and r[idx].strip())


def status_counts(path, key):
    if not path.exists():
        return None
    c = Counter()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            c[row[key]] += 1
    return c


def org_year(date_str):
    # DateOfOrganization is MM-DD-YYYY in the Secretary's export.
    parts = (date_str or "").split("-")
    return parts[-1] if len(parts) == 3 and len(parts[-1]) == 4 else None


def main(entities_csv):
    lines = []

    def p(s=""):
        lines.append(s)

    total_entities = load_entities_count(entities_csv)

    p("=" * 60)
    p("DATASET SUMMARY")
    p("=" * 60)
    p(f"Total entities in source file: {total_entities}")
    p()

    results_counts = status_counts(OUTPUT_DIR / "results.csv", "status")
    if results_counts:
        p("-- Scraping (results.csv) --")
        for status, n in results_counts.most_common():
            p(f"  {status:14s} {n}")
        p()

    log_counts = status_counts(OUTPUT_DIR / "parse_log.csv", "status")
    if log_counts:
        p("-- Parsing (parse_log.csv) --")
        for status, n in log_counts.most_common():
            p(f"  {status:14s} {n}")
        ok_total = log_counts.get("ok", 0) + log_counts.get("ok_ocr", 0)
        parsed_total = sum(log_counts.values())
        if parsed_total:
            p(f"  -> {ok_total}/{parsed_total} PDFs parsed successfully "
              f"({100 * ok_total / parsed_total:.1f}%)")
        p()

    master_path = OUTPUT_DIR / "master_addresses.csv"
    if master_path.exists():
        with open(master_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        entities_with_data = {r["state_id"] for r in rows}
        p("-- Extracted data (master_addresses.csv) --")
        p(f"  Distinct entities with at least one officer row: {len(entities_with_data)}")
        p(f"  Total officer/director rows: {len(rows)}")
        p()

        type_counts = Counter(r["entity_type"] for r in rows if r["entity_type"])
        if type_counts:
            p("-- By entity type (row count, not distinct entities) --")
            for t, n in type_counts.most_common():
                p(f"  {t:30s} {n}")
            p()

        year_counts = Counter()
        seen_entities_for_year = set()
        for r in rows:
            if r["state_id"] in seen_entities_for_year:
                continue
            seen_entities_for_year.add(r["state_id"])
            yr = org_year(r["date_of_organization"])
            if yr:
                year_counts[yr] += 1
        if year_counts:
            p("-- By year of organization (distinct entities) --")
            for yr in sorted(year_counts):
                p(f"  {yr}  {year_counts[yr]}")
            p()

        n = len(rows)
        flags = ["res_missing", "res_pobox", "res_co", "res_eq_principal", "res_eq_agent", "res_unit"]
        p("-- Address flag rates (share of officer/director rows) --")
        for flag in flags:
            hits = sum(1 for r in rows if r.get(flag) == "1")
            p(f"  {flag:18s} {hits:6d} / {n}  ({100 * hits / n:.1f}%)" if n else f"  {flag:18s} 0")
        p()

    repeated_path = OUTPUT_DIR / "repeated_addresses.csv"
    if repeated_path.exists():
        with open(repeated_path, newline="", encoding="utf-8") as f:
            repeated = list(csv.DictReader(f))
        if repeated:
            p("-- Repeated addresses (repeated_addresses.csv) --")
            p(f"  Addresses used by 2+ distinct entities: {len(repeated)}")
            top = sorted(repeated, key=lambda r: int(r["distinct_entities"]), reverse=True)[:10]
            p("  Top 10 by distinct entity count:")
            for r in top:
                p(f"    {r['distinct_entities']:>4s} entities  -  {r['example_raw_address']}")
            p()

    out_path = OUTPUT_DIR / "summary_stats.txt"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote summary to {out_path}")
    print()
    print("\n".join(lines))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
