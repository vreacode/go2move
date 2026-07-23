#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

SERVER_HOST_DEFAULT="127.0.0.1"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

warn() {
  printf 'WARN: %s\n' "$*" >&2
}

json_get() {
  local json_payload="$1"
  local key="$2"
  python3 -c 'import json,sys
payload=json.loads(sys.argv[1])
value=payload
for part in sys.argv[2].split("."):
    if isinstance(value, dict):
        value=value.get(part)
    else:
        value=None
        break
if value is None:
    print("")
elif isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
' "$json_payload" "$key"
}

server_port() {
  local value
  value="$(env_value SERVER_PORT)"
  if [[ -z "${value}" ]]; then
    echo "8080"
  else
    echo "${value}"
  fi
}

auth_token() {
  local value
  value="$(env_value AUTH_TOKEN)"
  if [[ -z "${value}" ]]; then
    fail "AUTH_TOKEN is missing in ${REPO_ROOT}/.env"
  fi
  echo "${value}"
}

server_base_url() {
  local host="${SCREEN_RECORDING_SERVER_HOST:-${SERVER_HOST_DEFAULT}}"
  local port
  port="$(server_port)"
  echo "http://${host}:${port}"
}

ensure_server_ready() {
  local base_url="$1"
  local health_url="${base_url}/health"
  local status
  status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 "${health_url}" || true)"
  if [[ "${status}" != "200" ]]; then
    fail "server is not ready at ${health_url}. Start it first with bash scripts/start_demo_stack.sh"
  fi
}

call_dev_recording_api() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  local base_url
  base_url="$(server_base_url)"
  ensure_server_ready "${base_url}"

  local token
  token="$(auth_token)"
  local url="${base_url}${path}"
  local response_file http_code curl_args
  response_file="$(mktemp)"
  trap 'rm -f "${response_file}"' RETURN

  curl_args=(
    -sS
    -X "${method}"
    -H "Authorization: Bearer ${token}"
    -H "Content-Type: application/json"
    -o "${response_file}"
    -w "%{http_code}"
    --max-time 20
  )
  if [[ -n "${body}" ]]; then
    curl_args+=(-d "${body}")
  fi

  http_code="$(curl "${curl_args[@]}" "${url}" || true)"
  if [[ ! "${http_code}" =~ ^[0-9]{3}$ ]]; then
    fail "request to ${url} failed before the server returned an HTTP status"
  fi

  local response_body
  response_body="$(cat "${response_file}")"

  if [[ "${http_code}" == "401" ]]; then
    fail "request was rejected: missing or invalid AUTH_TOKEN in ${REPO_ROOT}/.env"
  fi
  if [[ "${http_code}" == "403" ]]; then
    fail "request was rejected: AUTH_TOKEN in ${REPO_ROOT}/.env does not match server auth_token"
  fi
  if [[ "${http_code}" -lt 200 || "${http_code}" -ge 300 ]]; then
    if [[ -n "${response_body}" ]]; then
      fail "request failed (${http_code}): ${response_body}"
    fi
    fail "request failed with HTTP ${http_code}"
  fi

  if [[ -z "${response_body}" ]]; then
    fail "request succeeded but returned an empty response body"
  fi
  printf '%s\n' "${response_body}"
}

print_dev_recording_status() {
  local json_payload="$1"
  local running backend_active backend_configured filename output_dir started_at enabled last_error
  running="$(json_get "${json_payload}" "running")"
  backend_active="$(json_get "${json_payload}" "backend_active")"
  backend_configured="$(json_get "${json_payload}" "backend_configured")"
  filename="$(json_get "${json_payload}" "filename")"
  output_dir="$(json_get "${json_payload}" "output_dir")"
  started_at="$(json_get "${json_payload}" "started_at")"
  enabled="$(json_get "${json_payload}" "enabled")"
  last_error="$(json_get "${json_payload}" "last_error")"

  printf 'screen recording enabled: %s\n' "${enabled:-false}"
  printf 'screen recording running: %s\n' "${running:-false}"
  printf 'configured backend: %s\n' "${backend_configured:-auto}"
  printf 'active backend: %s\n' "${backend_active:---}"
  printf 'current file: %s\n' "${filename:---}"
  printf 'output directory: %s\n' "${output_dir:---}"
  printf 'started at: %s\n' "${started_at:---}"
  if [[ -n "${last_error}" ]]; then
    printf 'last error: %s\n' "${last_error}"
  fi
}
