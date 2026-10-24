#!/usr/bin/env bash
# Temporary local harness: exercises heartbeat.sh with a fake gh and broker.
set -euo pipefail
export LC_ALL=C

here=$(cd "$(dirname "$0")" && pwd)
root=${TMPDIR:-/tmp}/hb-test
rm -rf "$root"
mkdir -p "$root/bin" "$root/fixtures"

python3 - "$root" <<'PY'
import json, sys, datetime
root = sys.argv[1]
now = datetime.datetime.now(datetime.timezone.utc)

def ago(days):
    return (now - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

def write(name, obj):
    with open(f"{root}/fixtures/{name}", "w") as f:
        json.dump(obj, f)

write("a_b_prs.json", [
    {"number": 1, "title": "Bump aruba from 2.4.1 to 3.0.1", "createdAt": ago(4),
     "url": "https://github.com/a/b/pull/1", "author": {"login": "dependabot[bot]"},
     "mergeable": "MERGEABLE", "statusCheckRollup": []},
    {"number": 2, "title": "Human work", "createdAt": ago(4),
     "url": "https://github.com/a/b/pull/2", "author": {"login": "suhlig"},
     "mergeable": "CONFLICTING", "statusCheckRollup": []},
    {"number": 5, "title": "Renovate but green", "createdAt": ago(1),
     "url": "https://github.com/a/b/pull/5", "author": {"login": "renovate[bot]"},
     "mergeable": "MERGEABLE", "statusCheckRollup": [{"conclusion": "SUCCESS"}]},
])
write("c_d_prs.json", [
    {"number": 3, "title": "Update thing", "createdAt": ago(2),
     "url": "https://github.com/c/d/pull/3", "author": {"login": "renovate[bot]"},
     "mergeable": "CONFLICTING", "statusCheckRollup": []},
    {"number": 4, "title": "Update another", "createdAt": ago(10),
     "url": "https://github.com/c/d/pull/4", "author": {"login": "renovate[bot]"},
     "mergeable": "MERGEABLE", "statusCheckRollup": [{"conclusion": "SUCCESS"}]},
    {"number": 6, "title": "Failing checks", "createdAt": ago(1),
     "url": "https://github.com/c/d/pull/6", "author": {"login": "renovate[bot]"},
     "mergeable": "MERGEABLE", "statusCheckRollup": [{"conclusion": "FAILURE"}]},
])
PY

cat > "$root/bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
args="$*"
case $args in
  "pr list --repo a/b"*) cat "$HB_FIXTURES/a_b_prs.json" ;;
  "pr list --repo c/d"*) cat "$HB_FIXTURES/c_d_prs.json" ;;
  "pr list --repo suhlig/tokencheck"*)
    printf '%s\n' "$GH_TOKEN" >> "$HB_TOKEN_LOG"
    exit 1 ;;
  "pr list --repo uhlig-it/tokencheck"*)
    printf '%s\n' "$GH_TOKEN" >> "$HB_TOKEN_LOG"
    exit 1 ;;
  "pr list --repo e/f"*)
    echo "HTTP 404: Not Found (https://api.github.com/repos/e/f)" >&2
    exit 1 ;;
  "api repos/a/b/issues"*) printf '[{"title":"Dependency Dashboard","updated_at":"2026-10-23T10:00:00Z"}]\n' ;;
  "api repos/c/d/issues"*) printf '[{"title":"Dependency Dashboard","updated_at":"2026-09-01T10:00:00Z"}]\n' ;;
  "api repos/e/f/issues"*) exit 1 ;;
  "api --paginate --slurp repos/a/b/dependabot"*) printf '[[{"number":1},{"number":2}]]\n' ;;
  "api --paginate --slurp repos/c/d/dependabot"*)
    echo "Dependabot alerts are disabled for this repository." >&2
    exit 1 ;;
  "api --paginate --slurp repos/e/f/dependabot"*)
    echo "Dependabot alerts are disabled for this repository." >&2
    exit 1 ;;
  *) echo "fake gh: unhandled: $args" >&2; exit 1 ;;
esac
EOF

cat > "$root/bin/mosquitto_pub" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$HB_MQTT_LOG"
EOF

chmod +x "$root/bin/gh" "$root/bin/mosquitto_pub"
printf 'fake-token\n' > "$root/token"

export PATH="$root/bin:$PATH"
export HB_FIXTURES="$root/fixtures"
export HB_MQTT_LOG="$root/mqtt.log"
export GITHUB_TOKEN_FILE="$root/token"
export REPOS="a/b,c/d,e/f"
export MQTT_USER=user
export MQTT_PASSWORD=pass
export HB_TOKEN_LOG="$root/token-seen"

bash "$here/heartbeat.sh" --once > "$root/out1.json"
bash "$here/heartbeat.sh" --once > "$root/out2.json"

python3 - "$root" <<'PY'
import json, sys
root = sys.argv[1]
one = json.load(open(f"{root}/out1.json"))
two = json.load(open(f"{root}/out2.json"))

assert one["state"] == "attention", one["state"]
assert one["checked"] == 2, one["checked"]
assert one["expected"] == 3, one["expected"]
assert len(one["needs"]) == 4, one["needs"]
assert one["alerts"] == [{"repo": "a/b", "count": 2}], one["alerts"]
assert one["silent"] is None, one["silent"]
assert one["unreadable"] == 0, one["unreadable"]
assert len(one["errors"]) == 1 and one["errors"][0]["repo"] == "e/f", one["errors"]
assert not any(n["number"] == 2 for n in one["needs"]), "human PR must be ignored"
assert not any(n["number"] == 5 for n in one["needs"]), "green PR must be ignored"
reasons = {n["number"]: n["reason"] for n in one["needs"]}
assert reasons == {1: "needs you", 3: "conflict", 4: "stale", 6: "checks failing"}, reasons
assert one["fingerprint"] == two["fingerprint"], "fingerprint must be stable"
print("payload assertions: ok")
PY

echo "--- state topic publishes"
grep -c '^\-h 127.0.0.1 -p 1883 -u user -P pass -r -t github/heartbeat/state' "$root/mqtt.log"
echo "--- status topic publishes"
grep -c '^\-h 127.0.0.1 -p 1883 -u user -P pass -r -t github/heartbeat/status -m online' "$root/mqtt.log"

echo "--- per-owner tokens"
printf 'suhlig-token\n' > "$root/token-suhlig"
printf 'uhlig-token\n' > "$root/token-uhlig-it"
GITHUB_TOKEN_FILE="$root/token-suhlig" GITHUB_TOKEN_FILE_UHLIG_IT="$root/token-uhlig-it" \
  REPOS="suhlig/tokencheck,uhlig-it/tokencheck" bash "$here/heartbeat.sh" --once > /dev/null 2>&1
python3 - "$root" <<'PY'
import sys
root = sys.argv[1]
seen = open(f"{root}/token-seen").read().split()
assert seen == ["suhlig-token", "uhlig-token"], seen
print("per-owner token assertions: ok")
PY

echo "--- error changes the fingerprint"
REPOS="a/b,c/d" bash "$here/heartbeat.sh" --once > "$root/out4.json" 2>/dev/null
python3 - "$root" <<'PY'
import json, sys
root = sys.argv[1]
one = json.load(open(f"{root}/out1.json"))
four = json.load(open(f"{root}/out4.json"))
assert four["errors"] == [], four["errors"]
assert four["needs"] == one["needs"], "removing the failing repo must not change the needs"
assert four["fingerprint"] != one["fingerprint"], "an error must change the fingerprint"
print("fingerprint-error assertions: ok")
PY

echo "--- no dashboard case"
python3 - "$root" <<'PY'
import sys
root = sys.argv[1]
p = f"{root}/bin/gh"
text = open(p).read()
# Mimic gh's behaviour on an HTTP error: print the error body on stdout and
# exit non-zero. The script must not feed that body to the timestamp parser.
text = text.replace('"api repos/a/b/issues"*) printf \'[{"title":"Dependency Dashboard","updated_at":"2026-10-23T10:00:00Z"}]\\n\' ;;',
                    '"api repos/a/b/issues"*) printf \'{"message":"Bad credentials"}\\n\'; exit 1 ;;')
text = text.replace('"api repos/c/d/issues"*) printf \'[{"title":"Dependency Dashboard","updated_at":"2026-09-01T10:00:00Z"}]\\n\' ;;',
                    '"api repos/c/d/issues"*) printf \'{"message":"Bad credentials"}\\n\'; exit 1 ;;')
open(p, "w").write(text)
PY
bash "$here/heartbeat.sh" --once > "$root/out3.json" 2> "$root/err3.log"
python3 - "$root" <<'PY'
import json, sys
root = sys.argv[1]
three = json.load(open(f"{root}/out3.json"))
assert three["state"] == "attention", three["state"]
assert three["silent"]["since"] is None, three["silent"]
assert "no Dependency Dashboard" in three["silent"]["reason"], three["silent"]
err = open(f"{root}/err3.log").read()
assert "jq: error" not in err, err
print("no-dashboard assertions: ok")
PY

echo "all tests passed"
