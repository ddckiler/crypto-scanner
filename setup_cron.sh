#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCANNER_PATH="$SCRIPT_DIR/scanner.py"
LOG_PATH="$SCRIPT_DIR/scanner.log"

PYTHON_BIN="$(command -v python3 || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "❌ python3 не знайдено в PATH" >&2
  exit 1
fi

if [[ ! -f "$SCANNER_PATH" ]]; then
  echo "❌ scanner.py не знайдено за шляхом: $SCANNER_PATH" >&2
  exit 1
fi

CRON_LINE="0 * * * * cd \"$SCRIPT_DIR\" && \"$PYTHON_BIN\" \"$SCANNER_PATH\" >> \"$LOG_PATH\" 2>&1"

# Видалити старий рядок (якщо є), додати свіжий
( crontab -l 2>/dev/null | grep -vF "$SCANNER_PATH" || true ; echo "$CRON_LINE" ) | crontab -

echo "✅ Cron налаштовано — сканер запускатиметься щогодини"
echo "   python:  $PYTHON_BIN"
echo "   scanner: $SCANNER_PATH"
echo "   log:     $LOG_PATH"
echo "   rule:    0 * * * *"
