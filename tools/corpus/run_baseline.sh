#!/usr/bin/env bash
# Re-run the uvmls baseline over the real-world UVM corpus and print a compact summary.
#
#   run_baseline.sh [-s SERVER_DIR] [-o OUTDIR] [-c OLD_SUMMARY.json] [-j JOBS] [NAME ...]
#
#   -s  the uvmls server directory to run (default $UVMLS_SERVER, else $UVMLS_REPO/server). For a
#       fixed commit: git -C REPO archive SHA server | tar -x -C snap/SHA, then -s snap/SHA/server
#   -o  where results go (default results/latest); each case writes NAME.{stats,lint,elab,topology,graph}
#       .{json,err,time} there, plus summary.json
#   -c  compare with an earlier summary.json (default results/baseline/summary.json when it exists)
#   -j  cases run in parallel (default 4; timings are taken per process, so they stay comparable)
#   NAME  only these cases (see cases.txt)
#
# Environment: UVMLS_REPO (default: the repository this script lives in), UVMLS_PYTHON (default: its
# .venv python, else python3). Fetch the corpus first with tools/fetch_corpus.sh (README.md says what
# else is generated); the clones go to repos/ next to this script (git-ignored).
set -u
CORPUS=$(cd "$(dirname "$0")" && pwd)
REPO=${UVMLS_REPO:-$(cd "$CORPUS/../.." && pwd)}
if [ -z "${UVMLS_PYTHON:-}" ] && [ -x "$REPO/.venv/bin/python" ]; then PY=$REPO/.venv/bin/python; else PY=${UVMLS_PYTHON:-python3}; fi
SERVER=${UVMLS_SERVER:-$REPO/server}
OUT=$CORPUS/results/latest
CMP=$CORPUS/results/baseline/summary.json
JOBS=4
while getopts "s:o:c:j:" opt; do
  case $opt in s) SERVER=$(realpath "$OPTARG");; o) OUT=$(realpath -m "$OPTARG");; c) CMP=$OPTARG;;
               j) JOBS=$OPTARG;; *) exit 2;; esac
done
shift $((OPTIND - 1))
ONLY=" $* "
mkdir -p "$OUT"

export PYTHONPATH=$SERVER
source "$CORPUS/tools/env.sh"
echo "uvmls: $SERVER ($(cat "$SERVER/../.snapshot" 2>/dev/null || git -C "$SERVER" rev-parse --short HEAD 2>/dev/null))" > "$OUT/uvmls.txt"

run() {  # run NAME CMD ARGS...: NAME.CMD.{json,err,time}
  local name=$1 cmd=$2; shift 2
  local base=$OUT/$name.$cmd
  (cd "$CORPUS" && /usr/bin/time -f "%e %M %x" -o "$base.time" timeout 1200 \
     "$PY" -m uvmls "$cmd" "$@" > "$base.json" 2> "$base.err")
}

one() {  # one CASE-LINE
  IFS='|' read -r name mode dir flist tops test <<< "$1"
  local fl=() tp=() ts=()
  [ -n "$flist" ] && fl=(-f "$flist")
  IFS=',' read -ra t <<< "$tops"; for x in "${t[@]}"; do tp+=(--top "$x"); done
  [ -n "$test" ] && ts=(--test "$test")
  run "$name" stats "$dir" "${fl[@]}"
  run "$name" lint "$dir" "${fl[@]}" --json
  run "$name" elab "$dir" "${fl[@]}" "${tp[@]}" --json
  run "$name" topology "$dir" "${fl[@]}" "${ts[@]}" --json
  run "$name" graph "$dir" "${fl[@]}"
  echo "done $name" >&2
}

pids=()
while IFS= read -r line; do
  [[ -z $line || $line == \#* ]] && continue
  name=${line%%|*}
  [[ $ONLY != "  " && $ONLY != *" $name "* ]] && continue
  one "$line" &
  pids+=($!)
  while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do sleep 0.2; done
done < "$CORPUS/cases.txt"
wait

compare=()
[ -f "$CMP" ] && [ "$(realpath "$CMP")" != "$(realpath -m "$OUT/summary.json")" ] && compare=(--compare "$CMP")
python3 "$CORPUS/tools/summarize.py" "$OUT" "$CORPUS/cases.txt" --write "$OUT/summary.json" "${compare[@]}" $*
