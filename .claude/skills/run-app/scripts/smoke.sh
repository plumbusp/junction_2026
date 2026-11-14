#!/usr/bin/env bash
# Smoke test for Spot the Spin. Usage: smoke.sh <base-url>
# Read-only except one POST to /api/analyze with a neutral sentence.
# Exit 1 if /api/health fails or any endpoint answers 5xx. Missing endpoints are reported, not failed.
set -u

BASE="${1:?usage: smoke.sh <base-url>}"
BASE="${BASE%/}"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

fail=0
pass_count=0

# check <label> <method> <path> <required:yes|no> [json-body]
check() {
  local label="$1" method="$2" path="$3" required="$4" body="${5:-}"
  local code
  if [ -n "$body" ]; then
    code=$(curl -s -o "$TMP" -w '%{http_code}' --max-time 20 -X "$method" \
      -H 'Content-Type: application/json' -d "$body" "$BASE$path")
  else
    code=$(curl -s -o "$TMP" -w '%{http_code}' --max-time 20 -X "$method" "$BASE$path")
  fi

  if [ "$code" = "000" ]; then
    echo "FAIL     $label  $method $path  no connection"
    [ "$required" = "yes" ] && fail=1
    return
  fi
  if [[ "$code" =~ ^5 ]]; then
    echo "FAIL     $label  $method $path  HTTP $code"
    fail=1
    return
  fi
  if [ "$code" = "404" ] && [ "$required" = "no" ]; then
    echo "MISSING  $label  $method $path  HTTP 404"
    return
  fi
  if [ "$required" = "yes" ] && [[ ! "$code" =~ ^2 ]]; then
    echo "FAIL     $label  $method $path  HTTP $code"
    fail=1
    return
  fi
  echo "PASS     $label  $method $path  HTTP $code"
  pass_count=$((pass_count + 1))
}

echo "Smoke test against $BASE"

# The health body must be our JSON. Some servers (for example a bare Traefik backend) answer 200 to every path,
# so without this gate the other PASS lines would prove nothing.
body=$(curl -s --max-time 10 "$BASE/api/health" || true)
if ! echo "$body" | grep -q '"ok": *true'; then
  echo "FAIL     $BASE/api/health did not return {\"ok\": true}. Is this the Spot the Spin app?"
  echo "RESULT: FAIL"
  exit 1
fi
echo "PASS     health body contains \"ok\": true"
pass_count=$((pass_count + 1))

check "health"        GET  /api/health   yes
check "round"         GET  /api/round    no
check "analyze"       POST /api/analyze  no '{"text":"The weather in the capital is mild this week."}'
check "metrics api"   GET  /api/metrics  no
check "frontend home" GET  /             no
check "analyze page"  GET  /analyze/     no
check "metrics page"  GET  /metrics/     no


echo "passed: $pass_count"
if [ "$fail" -ne 0 ]; then
  echo "RESULT: FAIL"
  exit 1
fi
echo "RESULT: OK"
