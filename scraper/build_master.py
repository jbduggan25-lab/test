"""
Build one consolidated export merging the extracted officer/director data
(parsed_officers.csv) with each entity's metadata from the Secretary's
office export - one row per person, with that entity's current filing
info alongside the address as originally filed. This is the file to do
actual analysis on.

Usage:
    python scraper/build_master.py entities.csv

Reads:
    data/output/parsed_officers.csv
Writes:
    data/output/master_addresses.csv
"""
import csv
import sys
from pathlib import Path

OUTPUT_DIR = Path("data/output")


def load_entities(path):
    """Same blank-leading-row-tolerant loader as the other scripts."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    while rows and not any(cell.strip() for cell in rows[0]):
        rows.pop(0)
    if not rows:
        return {}
    header, data_rows = rows[0], rows[1:]
    by_id = {}
    for row in data_rows:
        d = dict(zip(header, row))
        idn = (d.get("ID Number") or "").strip()
        if idn:
            by_id[idn] = d
    return by_id


def main(entities_csv):
    entities = load_entities(entities_csv)

    officers_path = OUTPUT_DIR / "parsed_officers.csv"
    with open(officers_path, newline="", encoding="utf-8") as f:
        officers = list(csv.DictReader(f))

    out_rows = []
    for row in officers:
        state_id = row["state_id"]
        entity = entities.get(state_id, {})
        out_rows.append({
            "state_id": state_id,
            "entity_name": row["entity_name"] or entity.get("Entityname", ""),
            "entity_type": entity.get("EntityTypeDescriptor", ""),
            "date_of_organization": entity.get("DateOfOrganization", ""),
            "active_flag": entity.get("Activeflag", ""),
            "inactive_type": entity.get("Inactivetype", ""),
            "inactive_date": entity.get("InactiveDate", ""),
            "current_principal_addr1": entity.get("Addr1", ""),
            "current_principal_addr2": entity.get("Addr2", ""),
            "current_principal_city": entity.get("City", ""),
            "current_principal_state": entity.get("State", ""),
            "current_principal_zip": entity.get("PostalCode", ""),
            "current_agent_name": entity.get("Agentname", ""),
            "current_agent_addr1": entity.get("AgentAddr1", ""),
            "current_agent_addr2": entity.get("AgentAddr2", ""),
            "current_agent_city": entity.get("AgentCity", ""),
            "current_agent_state": entity.get("AgentState", ""),
            "current_agent_zip": entity.get("AgentPostalCode", ""),
            "title": row["title"],
            "person_name": row["name"],
            "filed_residential_address": row["residential_address"],
            "filed_po_address": row["po_address"],
            "filed_extra_address_lines": row["extra_address_lines"],
            "term_expires": row["term_expires"],
            "res_missing": row["res_missing"],
            "res_pobox": row["res_pobox"],
            "res_co": row["res_co"],
            "res_eq_principal": row["res_eq_principal"],
            "res_eq_agent": row["res_eq_agent"],
            "res_eq_po": row["res_eq_po"],
            "res_unit": row["res_unit"],
            "extraction_source": row["source"],
            "filing_no": row["filing_no"],
        })

    if not out_rows:
        print("No rows in parsed_officers.csv - nothing to write.")
        return

    out_path = OUTPUT_DIR / "master_addresses.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)
    print(f"Wrote {len(out_rows)} rows to {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
