#!/usr/bin/env bash
# Fire a simulated Telegram update at the LOCAL server (no Telegram needed).
# Usage: bash scripts/send_test.sh "your message here"
#
# Reads the bot's webhook_secret from the DB (via the proxy on :5433) for the
# signature header, and uses a unique update_id each run so the idempotency
# guard doesn't skip it.
set -euo pipefail

MSG="${1:-who is nikola tesla}"
BOT_ID="dd5e3e52-3fe9-42dd-b9f7-26f92831dffb"
TOKEN_HASH="d5860f4e0051d080912605d41d7f365cfdb5bfe1768701f3c9b84f1fba33c967"
CHAT_ID="959519454"
PORT="${PORT:-8000}"

SECRET="$(psql -h 127.0.0.1 -p 5433 -U postgres -d ethiogram -tA \
  -c "SELECT webhook_secret FROM bots WHERE id='${BOT_ID}';")"
UPDATE_ID="$(date +%s)"   # unique per run

curl -s -X POST "http://localhost:${PORT}/webhook/${TOKEN_HASH}" \
  -H "Content-Type: application/json" \
  -H "X-Telegram-Bot-Api-Secret-Token: ${SECRET}" \
  -d "{\"update_id\":${UPDATE_ID},\"message\":{\"message_id\":1,\"from\":{\"id\":${CHAT_ID},\"first_name\":\"Dev\"},\"chat\":{\"id\":${CHAT_ID},\"type\":\"private\"},\"date\":1700000000,\"text\":\"${MSG}\"}}"
echo
