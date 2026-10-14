#!/usr/bin/env bash
# End-to-end test against a local fake of download.bls.gov (same file formats).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d); trap 'kill $SRV 2>/dev/null; rm -rf "$WORK"' EXIT
mkdir -p "$WORK/srv/cu" "$WORK/repo"
cp -r "$ROOT/scripts" "$ROOT/config.yml" "$WORK/repo/"
printf 'bls:\n  base_url: x\n  surveys:\n    cu: test\n' > "$WORK/repo/config.yml"

S="$WORK/srv/cu"
printf 'series_id     \tyear\tperiod\t       value\tfootnote_codes\n' > $S/cu.data.0.Current
printf 'CUUR0000SA0   \t2025\tM09\t     324.800\t\n' >> $S/cu.data.0.Current
printf 'CUUR0000SA0   \t2025\tM10\t           -\tX\n' >> $S/cu.data.0.Current
# Like real BLS: Current starts in 1997; older history is only in the topical files,
# which can also repeat (and lag) recent observations. Current must win.
printf 'series_id     \tyear\tperiod\t       value\tfootnote_codes\n' > $S/cu.data.1.AllItems
printf 'CUUR0000SA0   \t1913\tM01\t       9.8\t\n'   >> $S/cu.data.1.AllItems
printf 'CUUR0000SA0   \t2025\tM09\t     999.999\t\n' >> $S/cu.data.1.AllItems
printf 'series_id\tarea_code\titem_code\n'           > $S/cu.series
printf 'CUUR0000SA0   \t0000\tSA0\n'                >> $S/cu.series
printf 'CPI documentation\n' > $S/cu.txt
cat > $S/index.html <<EOF
<a href="/pub/time.series/cu/cu.data.0.Current">cu.data.0.Current</a>
<a href="/pub/time.series/cu/cu.data.1.AllItems">cu.data.1.AllItems</a>
<a href="/pub/time.series/cu/cu.series">cu.series</a>
<a href="/pub/time.series/cu/cu.txt">cu.txt</a>
EOF
(cd "$WORK/srv" && python3 -m http.server 8765 >/dev/null 2>&1) & SRV=$!
sleep 1
export BLS_BASE_URL=http://127.0.0.1:8765

cd "$WORK/repo"
python3 scripts/sync.py
python3 scripts/validate.py
grep -q 'CUUR0000SA0,2025,M10,-,X' data/bls/cu/values/2025.csv
grep -q 'CUUR0000SA0,1913,M01,9.8,' data/bls/cu/values/1913.csv
! grep -q '999.999' data/bls/cu/values/2025.csv
grep -q 'latest_period": "2025-M10' state/manifest.json
test -f data/bls/cu/meta/series.tsv && test -f data/bls/cu/doc/cu.txt

# second run: listing unchanged -> nothing to do
python3 scripts/sync.py | grep -q "listing unchanged"

# revision: BLS revises Sep 2025, listing changes -> only 2025.csv rewritten
sed -i 's/324.800/324.900/' $S/cu.data.0.Current
echo "<!-- touched -->" >> $S/index.html
python3 scripts/sync.py
grep -q '324.900' data/bls/cu/values/2025.csv
grep -qx 'bls/cu/values/2025.csv' state/last_run.txt
! grep -q '1913' state/last_run.txt
echo "ALL TESTS PASSED"
