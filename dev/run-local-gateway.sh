#!/usr/bin/env bash
# Run a local Otari gateway for Triagewell development (no otari.ai account needed).
#
# Needs: git, uv, and Ollama with the models named in dev/otari-local.yml
# (ollama pull qwen3:0.6b; ollama pull qwen3:1.7b; ollama pull gemma3:4b).
# For code execution, also run Otari's sandbox container next to it:
#   docker run --rm -p 127.0.0.1:8180:8080 docker.io/mzdotai/otari-sandbox-container:latest
#
# The two secrets are generated on first run into dev/.env.local-gateway (gitignored).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(dirname "$here")"
src="$root/.otari-src"
secrets="$here/.env.local-gateway"

if [ ! -d "$src" ]; then
  git clone --depth 1 https://github.com/mozilla-ai/otari "$src"
fi
(cd "$src" && uv sync --no-dev -q)

if [ ! -f "$secrets" ]; then
  {
    echo "OTARI_MASTER_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    echo "TRIAGEWELL_SERVICE_KEY=tk-$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
    echo "OTARI_SECRET_KEY=$("$src/.venv/bin/otari" gen-secret-key | tail -1)"
  } > "$secrets"
  chmod 600 "$secrets"
  echo "Wrote $secrets. Put its TRIAGEWELL_SERVICE_KEY into .env as OTARI_API_KEY."
fi

set -a; . "$secrets"; set +a
export OTARI_SANDBOX_URL="${OTARI_SANDBOX_URL:-http://127.0.0.1:8180}"
cd "$here"
exec "$src/.venv/bin/otari" serve --config "$here/otari-local.yml"
