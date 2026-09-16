"""
Find residential addresses that repeat across multiple DIFFERENT entities
in master_addresses.csv. MGL c.180/c.156B require the actual residential
address of each officer/director - the same address showing up as the
"residential address" for officers of many unrelated entities is a signal
worth investigating: an attorney or registered-agent office being listed
instead of a real home address, or one person/filer behind many entities.

Grouping is on the residential address only (not who's listed at it), so
the same person appearing at their own address across their own multiple
entities counts the same as an attorney's office appearing across many
different clients - that distinction is exactly what `people_and_entities`
and `distinct_entities` are for you to make by eye.

Usage:
    python scraper/find_repeated_addresses.py
    python scraper/find_repeated_addresses.py --min-entities 3

Reads:
    data/output/master_addresses.csv
Writes:
    data/output/repeated_addresses.csv   one row per repeated address,
                                          sorted by distinct entity count
                                          (most-reused first)
"""
import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

OUTPUT_DIR = Path("data/output")

_ABBR = {
    "STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR", "LANE": "LN",
    "BOULEVARD": "BLVD", "COURT": "CT", "PLACE": "PL", "TERRACE": "TER",
    "CIRCLE": "CIR", "HIGHWAY": "HWY", "PARKWAY": "PKWY", "SQUARE": "SQ",
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "SUITE": "STE", "APARTMENT": "APT", "FLOOR": "FL",
}


def norm_addr(s):
    """Same normalisation as parse_articles.py, so grouping here matches
    the res_eq_principal/res_eq_agent flags already computed there."""
    s = re.sub(r"[^\w\s]", " ", (s or "").upper())
    s = re.sub(r"\bUSA?\b|\bUNITED STATES\b", " ", s)
    toks = [_ABBR.get(t, t) for t in s.split()]
    return " ".join(toks)


def main(min_entities):
    path = OUTPUT_DIR / "master_addresses.csv"
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    groups = defaultdict(lambda: {
        "entities": set(), "people": [], "raw_examples": set(),
        "unit_flags": 0, "pobox_flags": 0, "co_flags": 0, "eq_agent_flags": 0,
        "row_count": 0,
    })
    for row in rows:
        addr = (row.get("filed_residential_address") or "").strip()
        if not addr:
            continue
        key = norm_addr(addr)
        if not key:
            continue
        g = groups[key]
        g["entities"].add(row["state_id"])
        g["people"].append(f'{row["person_name"]} ({row["entity_name"]})')
        g["raw_examples"].add(addr)
        g["row_count"] += 1
        if row.get("res_unit") == "1":
            g["unit_flags"] += 1
        if row.get("res_pobox") == "1":
            g["pobox_flags"] += 1
        if row.get("res_co") == "1":
            g["co_flags"] += 1
        if row.get("res_eq_agent") == "1":
            g["eq_agent_flags"] += 1

    out_rows = []
    for key, g in groups.items():
        n_entities = len(g["entities"])
        if n_entities < min_entities:
            continue
        out_rows.append({
            "normalized_address": key,
            "example_raw_address": sorted(g["raw_examples"])[0],
            "distinct_entities": n_entities,
            "total_officer_rows": g["row_count"],
            "state_ids": ";".join(sorted(g["entities"])),
            "people_and_entities": " | ".join(g["people"][:25]) + (" ..." if len(g["people"]) > 25 else ""),
            "any_unit_suffix": int(g["unit_flags"] > 0),
            "any_pobox": int(g["pobox_flags"] > 0),
            "any_care_of": int(g["co_flags"] > 0),
            "any_matches_resident_agent": int(g["eq_agent_flags"] > 0),
        })

    if not out_rows:
        print(f"No addresses shared across >= {min_entities} distinct entities.")
        return

    out_rows.sort(key=lambda r: r["distinct_entities"], reverse=True)

    out_path = OUTPUT_DIR / "repeated_addresses.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)
    print(f"Wrote {len(out_rows)} addresses used by >= {min_entities} distinct entities to {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-entities", type=int, default=2,
                     help="only include addresses used by at least this many distinct entities (default 2)")
    args = ap.parse_args()
    main(args.min_entities)
