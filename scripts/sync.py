#!/usr/bin/env python3
"""Mirror official US price statistics (BLS CPI/PPI/avg prices, BEA PCE) into data/.

Layout written:
  data/bls/<survey>/meta/<file>.tsv        lookup tables (series, item, area, footnote, ...)
  data/bls/<survey>/values/<year>.csv      series_id,year,period,value,footnote_codes
  data/bea/<table>.csv                     PCE price index rows
  state/manifest.json                      sha256 + fetch time per source file

Values are copied verbatim as text (no float conversion), so the mirror is lossless.
Git history = vintage history: every BLS revision shows up as a diff.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATE = ROOT / "state" / "manifest.json"

# BLS (Akamai) 403s generic clients. It wants a UA with a contact email and NO URL in it.
CONTACT = os.environ.get("BLS_CONTACT_EMAIL", "")
UA = f"us-price-stats/1.0 ({CONTACT})" if CONTACT else "us-price-stats/1.0"
HEADERS = {"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "gzip, deflate"}

session = requests.Session()
session.headers.update(HEADERS)


def get(url: str, retries: int = 4) -> requests.Response:
    for i in range(retries):
        try:
            r = session.get(url, timeout=300)
            if r.status_code == 200:
                return r
            if r.status_code in (403, 404):
                r.raise_for_status()
        except requests.HTTPError:
            raise
        except requests.RequestException as e:
            print(f"  retry {i+1} {url}: {e}", file=sys.stderr)
        time.sleep(5 * (i + 1))
    raise RuntimeError(f"failed after {retries} tries: {url}")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def load_manifest() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def save_manifest(m: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    write_if_changed(STATE, json.dumps(m, indent=2, sort_keys=True) + "\n")


def decode(body: bytes) -> str:
    # Most BLS files are ASCII/UTF-8, but some docs are Windows-1252 (curly quotes etc.).
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1252", "replace")


def write_if_changed(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")  # bytes in/out: no platform encoding or newline translation
    if path.exists() and path.read_bytes() == data:
        return False
    path.write_bytes(data)
    return True


# ---------------------------------------------------------------- BLS
def list_bls_files(base: str, survey: str) -> tuple[list[str], str]:
    html = get(f"{base}/{survey}/").text
    names = set()
    for href in re.findall(r'href="([^"]+)"', html, flags=re.I):
        name = href.rstrip("/").rsplit("/", 1)[-1]
        if name.startswith(f"{survey}.") and not name.lower().endswith((".htm", ".html")):
            names.add(name)
    return sorted(names), html


def pick_data_files(files: list[str], survey: str) -> list[str]:
    # data.0.Current only covers recent decades (cu starts in 1997); older history and
    # discontinued series live in the topical files. Take all of them, Current first so
    # its rows win when the same observation appears twice.
    current = f"{survey}.data.0.Current"
    rest = [f for f in files if ".data." in f and f != current]
    return ([current] if current in files else []) + rest


def clean_tsv(raw: str) -> list[list[str]]:
    rows = []
    for line in raw.splitlines():
        if line.strip():
            rows.append([c.strip() for c in line.split("\t")])
    return rows


def sync_bls_survey(base: str, survey: str, manifest: dict) -> list[str]:
    print(f"[bls/{survey}]")
    out = DATA / "bls" / survey
    files, listing = list_bls_files(base, survey)
    if not files:
        raise RuntimeError(f"no files listed for {survey}")
    # The directory listing carries each file's timestamp+size: unchanged listing = nothing new.
    lkey = f"bls/{survey}/listing"
    if manifest.get(lkey, {}).get("sha256") == sha(listing.encode()) and not os.environ.get("FORCE"):
        print("  listing unchanged, skipping")
        return []
    data_files = pick_data_files(files, survey)
    meta_files = [f for f in files if ".data." not in f and not f.endswith(".txt")]
    docs = [f for f in files if f.endswith(".txt")]
    changed: list[str] = []

    # metadata + docs: small, store cleaned as-is
    for name in meta_files + docs:
        body = get(f"{base}/{survey}/{name}").content
        key = f"bls/{survey}/{name}"
        if manifest.get(key, {}).get("sha256") == sha(body):
            continue
        text = decode(body)
        if name in docs:
            dest = out / "doc" / name
            write_if_changed(dest, text)
        else:
            rows = clean_tsv(text)
            dest = out / "meta" / f"{name.split('.', 1)[1]}.tsv"
            write_if_changed(dest, "\n".join("\t".join(r) for r in rows) + "\n")
        manifest[key] = {"sha256": sha(body), "fetched": now()}
        changed.append(key)

    # data: split by year so each file stays small and diffs stay readable
    blobs = {}
    for name in data_files:
        body = get(f"{base}/{survey}/{name}").content
        blobs[name] = body
    combined_sha = sha(b"".join(blobs[n] for n in sorted(blobs)))
    key = f"bls/{survey}/data"
    if manifest.get(key, {}).get("sha256") == combined_sha:
        print("  data unchanged")
        manifest[lkey] = {"sha256": sha(listing.encode())}
        return changed

    by_year: dict[str, dict[tuple, tuple]] = defaultdict(dict)
    for name in data_files:  # Current first: setdefault keeps its version of duplicates
        rows = clean_tsv(decode(blobs[name]))
        header, rows = rows[0], rows[1:]
        assert header[:4] == ["series_id", "year", "period", "value"], header
        for r in rows:
            r = (r + [""] * 5)[:5]
            by_year[r[1]].setdefault((r[0], r[1], r[2]), tuple(r))
    by_year = {y: set(d.values()) for y, d in by_year.items()}

    vals = out / "values"
    vals.mkdir(parents=True, exist_ok=True)
    for year, rows in sorted(by_year.items()):
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["series_id", "year", "period", "value", "footnote_codes"])
        w.writerows(sorted(rows))
        if write_if_changed(vals / f"{year}.csv", buf.getvalue()):
            changed.append(f"bls/{survey}/values/{year}.csv")
    for stale in vals.glob("*.csv"):
        if stale.stem not in by_year:
            stale.unlink()
            changed.append(f"removed {stale}")

    latest = max(((y, p) for y, rs in by_year.items() for (_, _, p, _, _) in rs if p.startswith("M") and p != "M13"), default=None)
    manifest[key] = {"sha256": combined_sha, "fetched": now(), "files": sorted(blobs),
                     "latest_period": f"{latest[0]}-{latest[1]}" if latest else None,
                     "rows": sum(len(v) for v in by_year.values())}
    print(f"  {manifest[key]['rows']} rows, latest {manifest[key]['latest_period']}")
    manifest[lkey] = {"sha256": sha(listing.encode())}
    return changed


# ---------------------------------------------------------------- BEA
def sync_bea_table(table: str, key_: str, manifest: dict) -> list[str]:
    print(f"[bea/{table}]")
    url = ("https://apps.bea.gov/api/data?method=GetData&datasetname=NIPA"
           f"&TableName={table}&Frequency=M&Year=ALL&ResultFormat=JSON&UserID={key_}")
    j = get(url).json()
    res = j["BEAAPI"].get("Results", {})
    if "Error" in res or "Data" not in res:
        raise RuntimeError(f"BEA error: {json.dumps(res)[:500]}")
    cols = ["TableName", "SeriesCode", "LineNumber", "LineDescription", "TimePeriod",
            "METRIC_NAME", "CL_UNIT", "UNIT_MULT", "DataValue", "NoteRef"]
    rows = sorted((tuple(str(d.get(c, "")) for c in cols) for d in res["Data"]),
                  key=lambda r: (r[4], int(r[2] or 0)))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    w.writerows(rows)
    path = DATA / "bea" / f"{table}.csv"
    if not write_if_changed(path, buf.getvalue()):
        print("  unchanged")
        return []
    manifest[f"bea/{table}"] = {"fetched": now(), "rows": len(rows),
                                "latest_period": rows[-1][4] if rows else None}
    return [f"bea/{table}.csv"]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    cfg = yaml.safe_load((ROOT / "config.yml").read_text(encoding="utf-8"))
    base = os.environ.get("BLS_BASE_URL", cfg["bls"]["base_url"]).rstrip("/")
    only = set(filter(None, os.environ.get("ONLY", "").split(",")))
    manifest = load_manifest()
    changed, failures = [], []

    for survey in cfg["bls"]["surveys"]:
        if only and survey not in only:
            continue
        try:
            changed += sync_bls_survey(base, survey, manifest)
        except Exception as e:  # keep going; one survey failing shouldn't block others
            failures.append(f"bls/{survey}: {e}")
            print(f"  FAILED: {e}", file=sys.stderr)

    bea_key = os.environ.get("BEA_API_KEY")
    if bea_key and not only:
        for table in (cfg.get("bea") or {}).get("tables", {}):
            try:
                changed += sync_bea_table(table, bea_key, manifest)
            except Exception as e:
                failures.append(f"bea/{table}: {e}")

    save_manifest(manifest)
    summary = ROOT / "state" / "last_run.txt"
    write_if_changed(summary, "\n".join(changed) + "\n" if changed else "")
    print(f"\n{len(changed)} files changed, {len(failures)} failures")
    for f in failures:
        print("FAIL", f, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
