#!/usr/bin/env bash
#
# GitHub dependency heartbeat.
#
# Reports the exceptions that need a human, never bot activity: an open
# Dependabot pull request, a Renovate pull request that is conflicting or
# failing, an open Dependabot alert, or a Renovate app that has gone quiet.
#
# Runs once (--once) or every INTERVAL_SECONDS, and publishes one retained JSON
# verdict to MQTT_BASE_TOPIC/state. Home Assistant consumes that topic; this
# process never talks to Home Assistant.
#
# Configuration (environment):
#   GITHUB_TOKEN_FILE  default GitHub token file (required)
#   GITHUB_TOKEN_FILE_SUHLIG
#   GITHUB_TOKEN_FILE_UHLIG_IT
#                      optional per-owner overrides. A fine-grained PAT is scoped
#                      to a single resource owner, so covering both owners needs
#                      two tokens; a classic PAT can serve as the default for
#                      both. The owner is the part before the first `/`.
#   REPOS              comma-separated owner/repo list (required)
#   INTERVAL_SECONDS   loop interval, default 1800
#   STUCK_DAYS         Renovate PR age that counts as stuck, default 7
#   SILENT_DAYS        no Renovate activity that counts as silent, default 21
#   MQTT_HOST          default 127.0.0.1
#   MQTT_PORT          default 1883
#   MQTT_USER          optional
#   MQTT_PASSWORD      optional
#   MQTT_BASE_TOPIC    default github/heartbeat
#
# Exit status: 0 when a cycle completed, non-zero only on a configuration or
# startup error. The verdict lives in the payload, never in the exit code.
#
# See docs/github-heartbeat.md for the contract and the delivery path.

set -euo pipefail
export LC_ALL=C
IFS=$'\n\t'

# The filter runs in jq: $repo and $days are jq variables, not shell ones.
# shellcheck disable=SC2016
readonly PR_FILTER='
  def failing:
    (.conclusion // .state // "none") as $c
    | ($c == "FAILURE" or $c == "ERROR" or $c == "TIMED_OUT"
       or $c == "ACTION_REQUIRED" or $c == "CANCELLED"
       or $c == "STARTUP_FAILURE");
  .[]
  | select(.author != null)
  | select(.author.login | test("renovate|dependabot"))
  | (now - (.createdAt | fromdateiso8601)) as $age
  | (if (.author.login | test("dependabot")) then "needs you"
     elif .mergeable == "CONFLICTING" then "conflict"
     elif ((.statusCheckRollup // []) | any(.[]; failing)) then "checks failing"
     elif $age > ($days * 86400) then "stale"
     else empty
     end) as $reason
  | {
      repo: $repo,
      number: .number,
      reason: $reason,
      title: .title,
      url: .url,
      age_days: ($age / 86400 | floor)
    }
'

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

die() {
  log "error: $*"
  exit 1
}

require_number() {
  local name=$1 value=$2
  [[ $value =~ ^[0-9]+$ ]] || die "$name must be a non-negative integer, got '$value'"
}

short_hash() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | cut -c1-12
  else
    shasum -a 256 | cut -c1-12
  fi
}

# ---------------------------------------------------------------- configuration

once=false
for arg in "$@"; do
  case $arg in
    --once) once=true ;;
    *) die "unknown argument: $arg" ;;
  esac
done

stuck_days=${STUCK_DAYS:-7}
silent_days=${SILENT_DAYS:-21}
interval=${INTERVAL_SECONDS:-1800}
token_file=${GITHUB_TOKEN_FILE:-}
repos_csv=${REPOS:-}
mqtt_host=${MQTT_HOST:-127.0.0.1}
mqtt_port=${MQTT_PORT:-1883}
mqtt_user=${MQTT_USER:-}
mqtt_password=${MQTT_PASSWORD:-}
base_topic=${MQTT_BASE_TOPIC:-github/heartbeat}
base_topic=${base_topic%/}

require_number STUCK_DAYS "$stuck_days"
require_number SILENT_DAYS "$silent_days"
require_number INTERVAL_SECONDS "$interval"
require_number MQTT_PORT "$mqtt_port"
(( interval > 0 )) || die "INTERVAL_SECONDS must be positive"
(( mqtt_port > 0 && mqtt_port < 65536 )) || die "MQTT_PORT must be between 1 and 65535"

[[ -n $token_file ]] || die "GITHUB_TOKEN_FILE is not set"
[[ -n $repos_csv ]] || die "REPOS is not set"

repos=()
while IFS= read -r repo; do
  if [[ -n $repo ]]; then
    repos+=("$repo")
  fi
done < <(printf '%s\n' "$repos_csv" | tr ',' '\n' \
           | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e '/^$/d')
(( ${#repos[@]} > 0 )) || die "REPOS contains no repositories"

readonly state_topic="$base_topic/state"
readonly status_topic="$base_topic/status"
readonly expected=${#repos[@]}

mqtt_args=(-h "$mqtt_host" -p "$mqtt_port")
if [[ -n $mqtt_user ]]; then
  mqtt_args+=(-u "$mqtt_user")
fi
if [[ -n $mqtt_password ]]; then
  mqtt_args+=(-P "$mqtt_password")
fi

tmpdir=$(mktemp -d)
cleanup() { rm -rf "$tmpdir"; }
trap cleanup EXIT

# ----------------------------------------------------------------------- plumbing

load_default_token() {
  [[ -r $token_file ]] || { log "cannot read $token_file"; return 1; }
  [[ -n $(tr -d '[:space:]' < "$token_file") ]] || { log "$token_file is empty"; return 1; }
}

# A fine-grained PAT belongs to exactly one resource owner, so the two owners in
# the repository list may need different tokens. Add a branch here when a third
# owner joins the check.
token_for_owner() {
  local owner=$1 file token
  case $owner in
    suhlig) file=${GITHUB_TOKEN_FILE_SUHLIG:-$token_file} ;;
    uhlig-it) file=${GITHUB_TOKEN_FILE_UHLIG_IT:-$token_file} ;;
    *) file=$token_file ;;
  esac
  [[ -r $file ]] || { log "cannot read token for $owner ($file)"; return 1; }
  token=$(tr -d '[:space:]' < "$file")
  [[ -n $token ]] || { log "token file for $owner is empty ($file)"; return 1; }
  printf '%s' "$token"
}

publish() {
  local topic=$1 payload=$2
  mosquitto_pub "${mqtt_args[@]}" -r -t "$topic" -m "$payload"
}

publish_status() {
  local status=$1
  publish "$status_topic" "$status" >/dev/null 2>&1 || log "cannot publish status '$status'"
}

# ------------------------------------------------------------------------- cycle

run_cycle() {
  local needs_file="$tmpdir/needs.ndjson"
  local alerts_file="$tmpdir/alerts.ndjson"
  local errors_file="$tmpdir/errors.ndjson"
  local err_file="$tmpdir/gh.err"
  : > "$needs_file"
  : > "$alerts_file"
  : > "$errors_file"

  local checked=0
  local unreadable=0
  local last_activity=0
  local repo token pr_json issues_json alert_out count dashboard ts err

  for repo in "${repos[@]}"; do
    if ! token=$(token_for_owner "${repo%%/*}"); then
      jq -n --arg repo "$repo" --arg error "no readable token for owner ${repo%%/*}" \
        '{repo: $repo, error: $error}' >> "$errors_file"
      continue
    fi
    export GH_TOKEN=$token
    if pr_json=$(gh pr list --repo "$repo" --state open --limit 100 \
                   --json number,title,createdAt,url,author,mergeable,statusCheckRollup \
                   2>"$err_file"); then
      if printf '%s' "$pr_json" \
           | jq -c --arg repo "$repo" --argjson days "$stuck_days" "$PR_FILTER" \
             >> "$needs_file"; then
        checked=$((checked + 1))
      else
        err=$(tr '\n' ' ' < "$err_file" | cut -c1-300)
        jq -n --arg repo "$repo" --arg error "cannot classify pull requests: $err" \
          '{repo: $repo, error: $error}' >> "$errors_file"
        log "warning: cannot classify pull requests for $repo"
      fi
    else
      err=$(tr '\n' ' ' < "$err_file" | cut -c1-300)
      jq -n --arg repo "$repo" --arg error "$err" \
        '{repo: $repo, error: $error}' >> "$errors_file"
      log "warning: cannot list pull requests for $repo: $err"
    fi

    # Renovate touches the Dependency Dashboard when it runs and finds changes,
    # so the freshest dashboard across all repositories is the liveness signal.
    # A single quiet repository proves nothing; all of them quiet does.
    #
    # Parse the response ourselves instead of using `gh api --jq`: on an HTTP
    # error gh can print the error body on stdout, which would otherwise end up
    # in the timestamp parser.
    if issues_json=$(gh api "repos/$repo/issues?state=open&per_page=100" 2>/dev/null); then
      dashboard=$(printf '%s' "$issues_json" \
                    | jq -r '[.[] | select(.title == "Dependency Dashboard")][0].updated_at // empty' \
                      2>/dev/null || true)
      if [[ -n $dashboard ]]; then
        ts=$(jq -nr --arg t "$dashboard" '$t | fromdateiso8601 | floor' 2>/dev/null || true)
        if [[ -n $ts ]] && (( ts > last_activity )); then
          last_activity=$ts
        fi
      fi
    fi

    # Open security alerts, where the repository has them enabled. "disabled"
    # is a normal state, not a permission problem.
    if alert_out=$(gh api --paginate --slurp \
                     "repos/$repo/dependabot/alerts?state=open&per_page=100" \
                     2>"$err_file"); then
      count=$(printf '%s' "$alert_out" | jq 'add | length' 2>/dev/null || echo 0)
      if [[ $count =~ ^[0-9]+$ ]] && (( count > 0 )); then
        jq -n --arg repo "$repo" --argjson count "$count" \
          '{repo: $repo, count: $count}' >> "$alerts_file"
      fi
    elif grep -qi 'disabled' "$err_file"; then
      :
    else
      unreadable=$((unreadable + 1))
      log "warning: Dependabot alerts unreadable for $repo: $(tr '\n' ' ' < "$err_file" | cut -c1-200)"
    fi
  done

  local now idle since silent_json='null' silent_key=''
  now=$(date -u +%s)
  if (( last_activity > 0 )); then
    idle=$(( (now - last_activity) / 86400 ))
    if (( idle > silent_days )); then
      since=$(jq -nr --argjson t "$last_activity" '$t | todate | split("T")[0]')
      silent_json=$(jq -n --arg since "$since" --argjson days "$idle" \
        '{since: $since, days: $days}')
      silent_key="silent:$since"
    fi
  else
    silent_json=$(jq -n '{since: null, days: null,
      reason: "no Dependency Dashboard found in any repository; cannot tell whether Renovate runs"}')
    silent_key='silent:none'
  fi

  local needs_json alerts_json errors_json
  local needs_count alerts_count errors_count state fingerprint payload
  needs_json=$(jq -s '.' "$needs_file")
  alerts_json=$(jq -s '.' "$alerts_file")
  errors_json=$(jq -s '.' "$errors_file")
  needs_count=$(jq 'length' <<< "$needs_json")
  alerts_count=$(jq 'length' <<< "$alerts_json")
  errors_count=$(jq 'length' <<< "$errors_json")

  state=ok
  if (( needs_count > 0 || alerts_count > 0 || errors_count > 0 || checked != expected )) \
     || [[ $silent_json != null ]]; then
    state=attention
  fi

  # The fingerprint covers only the set of exceptions, never ages or
  # timestamps, so an unchanged set does not re-notify tomorrow.
  {
    jq -r '"\(.repo)#\(.number):\(.reason)"' "$needs_file" | sort
    jq -r '"alert \(.repo):\(.count)"' "$alerts_file" | sort
    jq -r '"error \(.repo)"' "$errors_file" | sort
    if [[ -n $silent_key ]]; then
      printf '%s\n' "$silent_key"
    fi
  } > "$tmpdir/fingerprint.txt"
  fingerprint=$(short_hash < "$tmpdir/fingerprint.txt")

  payload=$(jq -n \
    --arg state "$state" \
    --argjson checked "$checked" \
    --argjson expected "$expected" \
    --arg checked_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg fingerprint "$fingerprint" \
    --argjson needs "$needs_json" \
    --argjson alerts "$alerts_json" \
    --argjson silent "$silent_json" \
    --argjson unreadable "$unreadable" \
    --argjson errors "$errors_json" \
    '{state: $state, checked: $checked, expected: $expected,
      checked_at: $checked_at, fingerprint: $fingerprint,
      needs: $needs, alerts: $alerts, silent: $silent,
      unreadable: $unreadable, errors: $errors}')

  publish "$state_topic" "$payload" || log "cannot publish state to $state_topic"
  LAST_PAYLOAD=$payload

  log "state=$state checked=$checked/$expected needs=$needs_count alerts=$alerts_count unreadable=$unreadable errors=$errors_count fingerprint=$fingerprint"
}

# -------------------------------------------------------------------------- main

# Fail fast on a bad mount rather than looping forever with no default token.
load_default_token || exit 1
publish_status online

if $once; then
  run_cycle || exit 1
  printf '%s\n' "$LAST_PAYLOAD"
  exit 0
fi

trap 'publish_status offline; exit 0' TERM INT

while true; do
  run_cycle || log "cycle failed"
  sleep "$interval" &
  wait $!
done
