#!/usr/bin/env python3
"""Sanity checks before committing. Fails the run rather than commit a corrupted mirror."""
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VALUE = re.compile(r"^(-?\d+(\.\d+)?|-|)$")  # BLS uses "-" for unavailable (e.g. Oct 2025)
errors = []

for survey_dir in sorted((ROOT / "data" / "bls").glob("*")):
    vals = sorted((survey_dir / "values").glob("*.csv"))
    if not vals:
        errors.append(f"{survey_dir.name}: no value files")
        continue
    if not (survey_dir / "meta" / "series.tsv").exists():
        errors.append(f"{survey_dir.name}: missing meta/series.tsv")
    for f in vals:
        seen = set()
        with f.open() as fh:
            for row in csv.DictReader(fh):
                k = (row["series_id"], row["year"], row["period"])
                if k in seen:
                    errors.append(f"{f}: duplicate {k}")
                    break
                seen.add(k)
                if row["year"] != f.stem:
                    errors.append(f"{f}: year mismatch {k}")
                    break
                if not VALUE.match(row["value"]):
                    errors.append(f"{f}: bad value {row['value']!r} {k}")
                    break

# Headline CPI must exist back to 1913 if cu is mirrored.
cu = ROOT / "data" / "bls" / "cu" / "values"
if cu.exists():
    if not (cu / "1913.csv").exists():
        errors.append("cu: history should start in 1913")
    elif "CUUR0000SA0" not in (cu / "1913.csv").read_text():
        errors.append("cu: CUUR0000SA0 (CPI-U all items) missing from 1913")

for e in errors[:50]:
    print("ERROR", e)
print(f"validate: {len(errors)} errors")
sys.exit(1 if errors else 0)
