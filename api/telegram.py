"""Vercel function — приймає Telegram webhook (кнопка / команда /scan),
валідує chat_id і secret_token, тригерить GitHub Actions workflow."""

import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = str(os.environ.get("TELEGRAM_CHAT_ID", ""))
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "ddckiler/crypto-scanner")
WORKFLOW_FILE = os.environ.get("WORKFLOW_FILE", "scanner.yml")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")


def _http(url: str, headers: dict, payload: dict, method: str = "POST") -> None:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={**headers, "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def send_message(text: str) -> None:
    _http(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        {},
        {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
    )


def answer_callback(callback_id: str, text: str = "") -> None:
    try:
        _http(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
            {},
            {"callback_query_id": callback_id, "text": text},
        )
    except Exception:
        pass


def trigger_workflow() -> None:
    _http(
        f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches",
        {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        {"ref": "main"},
    )


class handler(BaseHTTPRequestHandler):
    def _ok(self, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_GET(self) -> None:
        # Health-check
        self._ok()

    def do_POST(self) -> None:
        # Validate Telegram secret_token (set in setWebhook)
        if WEBHOOK_SECRET:
            got = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if got != WEBHOOK_SECRET:
                self._ok(401)
                return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            update = json.loads(raw)
        except json.JSONDecodeError:
            self._ok()
            return

        self._handle_update(update)
        self._ok()

    def _handle_update(self, update: dict) -> None:
        # Callback query (button press)
        cq = update.get("callback_query")
        if cq:
            chat = str(cq.get("message", {}).get("chat", {}).get("id", ""))
            if chat != TELEGRAM_CHAT_ID:
                return
            answer_callback(cq.get("id", ""), "🔄 Сканую...")
            self._dispatch()
            return

        # Plain text message — supports /scan
        msg = update.get("message") or update.get("edited_message") or {}
        chat = str(msg.get("chat", {}).get("id", ""))
        if chat != TELEGRAM_CHAT_ID:
            return

        text = (msg.get("text") or "").strip().lower()
        if text.startswith("/scan") or text == "сканувати":
            self._dispatch()

    def _dispatch(self) -> None:
        try:
            trigger_workflow()
            send_message("🔄 Сканую ринок... аналіз прийде через ~30 секунд.")
        except Exception as exc:
            send_message(f"⚠️ Не вдалось запустити сканер: {exc}")
