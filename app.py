"""Vercel Flask app — приймає Telegram webhook (кнопка / команда /scan),
валідує chat_id і secret_token, тригерить GitHub Actions workflow."""

import json
import os
import urllib.request

from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = str(os.environ.get("TELEGRAM_CHAT_ID", ""))
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "ddckiler/crypto-scanner")
WORKFLOW_FILE = os.environ.get("WORKFLOW_FILE", "scanner.yml")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")


def _http(url: str, headers: dict, payload: dict) -> None:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
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


def dispatch() -> None:
    try:
        trigger_workflow()
        send_message("🔄 Сканую ринок... аналіз прийде через ~30 секунд.")
    except Exception as exc:
        send_message(f"⚠️ Не вдалось запустити сканер: {exc}")


def handle_update(update: dict) -> None:
    cq = update.get("callback_query")
    if cq:
        chat = str(cq.get("message", {}).get("chat", {}).get("id", ""))
        if chat != TELEGRAM_CHAT_ID:
            return
        answer_callback(cq.get("id", ""), "🔄 Сканую...")
        dispatch()
        return

    msg = update.get("message") or update.get("edited_message") or {}
    chat = str(msg.get("chat", {}).get("id", ""))
    if chat != TELEGRAM_CHAT_ID:
        return

    text = (msg.get("text") or "").strip().lower()
    if text.startswith("/scan") or text == "сканувати":
        dispatch()


@app.route("/", methods=["GET"])
def root():
    return "ok"


@app.route("/api/telegram", methods=["POST", "GET"])
def webhook():
    if request.method == "GET":
        return "ok"

    if WEBHOOK_SECRET:
        got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if got != WEBHOOK_SECRET:
            return ("unauthorized", 401)

    update = request.get_json(silent=True) or {}
    handle_update(update)
    return "ok"
