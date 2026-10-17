"""Read-only UNECE UN/LOCODE release adapter.

Authoritative CSV release is used only when ALL three CodeList parts exist.
The legacy uploaded catalog remains a fallback until a complete release is provided.
No source row is an ACTIVE Port Master or IAM grant.
"""
import csv
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RELEASE=ROOT/"release"/"csv"
PARTS=tuple(RELEASE/f"UNLOCODE CodeListPart{i}.csv" for i in (1,2,3))
SUBDIVISIONS=RELEASE/"SubdivisionCodes.csv"
LEGACY=ROOT/"data"/"unlocode_source_2026_uploaded.csv"
PATTERN=re.compile(r"[A-Z]{2}[A-Z0-9]{3}\Z")


def _release_rows(path):
    # Official UN/LOCODE CSV typically lacks a header; detect either form.
    with path.open(encoding="utf-8-sig",newline="",errors="replace") as stream:
        rows=list(csv.reader(stream))
    for row in rows:
        if len(row)<5:
            continue
        country=row[1].strip().upper()
        location=row[2].strip().upper()
        code=country+location
        if not PATTERN.fullmatch(code):
            continue
        # Columns: change, country, location, name, name-ascii, subdivision,
        # function, status, date, iata, coordinates, remarks.
        name=(row[3] or row[4]).strip()
        if not name:
            continue
        yield {"unlocode":code,"country_code":country,
               "country_name":"","port_code":location,"port_name":name,
               "subdivision_code":row[5].strip() if len(row)>5 else "",
               "function":row[6].strip() if len(row)>6 else "",
               "unlocode_status":row[7].strip() if len(row)>7 else "",
               "iata_code":row[9].strip() if len(row)>9 else "",
               "coordinates":row[10].strip() if len(row)>10 else "",
               "source":"unece_release"}


def catalog():
    presence=[path.is_file() for path in PARTS]
    if any(presence) and not all(presence):
        raise RuntimeError("UNLOCODE_RELEASE_INCOMPLETE")
    if all(presence):
        records={}
        for path in PARTS:
            for row in _release_rows(path):
                # Never silently overwrite contradictory entries across release parts.
                prior=records.get(row["unlocode"])
                if prior is not None and prior!=row:
                    raise RuntimeError("UNLOCODE_RELEASE_CONFLICT:"+row["unlocode"])
                records[row["unlocode"]]=row
        if not records:
            raise RuntimeError("UNLOCODE_RELEASE_EMPTY")
        return list(records.values())
    with LEGACY.open(encoding="utf-8",newline="") as stream:
        return list(csv.DictReader(stream))


def subdivisions():
    if not SUBDIVISIONS.exists():
        return []
    with SUBDIVISIONS.open(encoding="utf-8-sig",newline="",errors="replace") as stream:
        return list(csv.reader(stream))
