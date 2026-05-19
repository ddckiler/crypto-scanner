#!/usr/bin/env python3
"""Crypto Scanner — отримує дані з CoinGecko, аналізує через Claude
з урахуванням балансу та ризик-менеджменту, надсилає сигнали в Telegram."""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from anthropic import Anthropic
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env")

logging.basicConfig(
    filename=SCRIPT_DIR / "scanner.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logging.warning("Invalid %s=%r — using default %s", name, raw, default)
        return default


BALANCE_USD = _env_float("BALANCE_USD", 1000.0)
MAX_RISK_PCT = _env_float("MAX_RISK_PCT", 2.0)
MAX_LEVERAGE = _env_float("MAX_LEVERAGE", 10.0)

COINS = {
    "bitcoin":          ("₿", "Bitcoin",  "BTC"),
    "ethereum":         ("Ξ", "Ethereum", "ETH"),
    "solana":           ("◎", "Solana",   "SOL"),
    "the-open-network": ("💎", "Toncoin",  "TON"),
    "binancecoin":      ("🅱", "BNB",      "BNB"),
}

SIGNAL_EMOJI = {"LONG": "🟢", "SHORT": "🔴", "ЧЕКАТИ": "⚪"}


def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        },
        timeout=30,
    )
    resp.raise_for_status()


def fetch_prices() -> dict:
    """/coins/markets дає 1h/24h/7d зміни + high/low/volume — потрібно для SL/TP."""
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "ids": ",".join(COINS.keys()),
        "price_change_percentage": "1h,24h,7d",
        "per_page": 50,
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return {item["id"]: item for item in r.json()}


def build_prompt(prices: dict) -> str:
    risk_usd = BALANCE_USD * MAX_RISK_PCT / 100.0
    lines = []
    for coin_id, (_, name, symbol) in COINS.items():
        d = prices.get(coin_id) or {}
        if not d:
            continue
        lines.append(
            f"{name} ({symbol}): ціна ${d.get('current_price', 0):,.2f} | "
            f"1г: {d.get('price_change_percentage_1h_in_currency') or 0:+.2f}% | "
            f"24г: {d.get('price_change_percentage_24h_in_currency') or 0:+.2f}% | "
            f"7д: {d.get('price_change_percentage_7d_in_currency') or 0:+.2f}% | "
            f"24г high: ${d.get('high_24h') or 0:,.2f} | "
            f"24г low: ${d.get('low_24h') or 0:,.2f} | "
            f"vol24г: ${d.get('total_volume') or 0:,.0f}"
        )
    market_data = "\n".join(lines)

    return f"""Ти — досвідчений крипто-трейдер з 10+ років практики (спот + ф'ючерси).
Аналізуєш ринок для конкретного трейдера з фіксованим капіталом і ризик-профілем.

ПОРТФЕЛЬ ТРЕЙДЕРА:
- Доступний баланс: ${BALANCE_USD:,.2f}
- Максимальний ризик на одну угоду: {MAX_RISK_PCT}% балансу (~${risk_usd:,.2f})
- Максимально дозволене плече: {MAX_LEVERAGE:.0f}x

ДАНІ РИНКУ (зараз):
{market_data}

ЗАВДАННЯ: Для КОЖНОЇ монети дай аналіз у СТРОГОМУ форматі (без markdown, без зайвих пояснень).

Якщо сигнал LONG або SHORT — заповни ВСІ поля:
COIN: <тікер, напр. BTC>
SIGNAL: <LONG|SHORT>
REASON: <1-2 речення українською: чому такий сигнал, які рівні, який імпульс>
CONFIDENCE: <Високий|Середній|Низький>
ENTRY: <ціна входу, число без $ і ком>
STOP_LOSS: <ціна стопу, число>
TAKE_PROFIT: <ціна тейку, число; R:R мінімум 1.5>
LEVERAGE: <ціле число x; не більше {MAX_LEVERAGE:.0f}>
MARGIN_USD: <скільки $ маржі взяти з балансу — ціле число; підбери так, щоб втрата при стопі ≈ {MAX_RISK_PCT}% балансу (~${risk_usd:,.2f}) з урахуванням плеча>
---

Якщо сигнал ЧЕКАТИ — тільки 4 поля:
COIN: <тікер>
SIGNAL: ЧЕКАТИ
REASON: <чому чекаємо>
CONFIDENCE: <Високий|Середній|Низький>
---

В самому кінці одним рядком:
MARKET: <1 речення українською про загальний настрій ринку>

ПРАВИЛА РИЗИК-МЕНЕДЖМЕНТУ:
- Що вища впевненість + чіткіша картина → можна більше плече (макс. {MAX_LEVERAGE:.0f}x).
- Висока волатильність → менше плече, ширший стоп.
- Розрахунок маржі: margin × leverage × (|entry-stop|/entry) ≈ ризик у $.
- ENTRY реалістичний: LONG — на відкаті/підтримці; SHORT — на ретесті опору.
- Без чіткого сетапу — ЧЕКАТИ, не вигадуй сигнал."""


def _parse_number(value: str) -> Optional[float]:
    if not value:
        return None
    cleaned = value.replace("$", "").replace(",", "").replace("x", "").strip()
    # Беремо тільки перше число у рядку (на випадок коментарів типу "5x (обережно)")
    token = ""
    for ch in cleaned:
        if ch.isdigit() or ch in ".-+":
            token += ch
        elif token:
            break
    try:
        return float(token) if token else None
    except ValueError:
        return None


def parse_analysis(text: str) -> dict:
    result: dict = {"coins": {}, "market": ""}
    current: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("---"):
            if current.get("symbol"):
                result["coins"][current["symbol"].upper()] = current
            current = {}
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().upper()
        value = value.strip()
        if key == "COIN":
            current["symbol"] = value
        elif key == "SIGNAL":
            current["signal"] = value
        elif key == "REASON":
            current["reason"] = value
        elif key == "CONFIDENCE":
            current["confidence"] = value
        elif key == "ENTRY":
            current["entry"] = _parse_number(value)
        elif key == "STOP_LOSS":
            current["stop_loss"] = _parse_number(value)
        elif key == "TAKE_PROFIT":
            current["take_profit"] = _parse_number(value)
        elif key == "LEVERAGE":
            current["leverage"] = _parse_number(value)
        elif key == "MARGIN_USD":
            current["margin_usd"] = _parse_number(value)
        elif key == "MARKET":
            result["market"] = value
    if current.get("symbol"):
        result["coins"][current["symbol"].upper()] = current
    return result


def _fmt_price(value) -> str:
    if value is None:
        return "—"
    if value >= 1000:
        return f"${value:,.2f}"
    if value >= 1:
        return f"${value:,.3f}"
    return f"${value:,.5f}"


def format_message(prices: dict, analysis: dict) -> str:
    now = datetime.now().strftime("%d.%m %H:%M")
    risk_usd = BALANCE_USD * MAX_RISK_PCT / 100.0
    parts = [
        f"🤖 <b>Crypto Scanner</b> | {now}",
        f"💼 Баланс: ${BALANCE_USD:,.0f} | Ризик/угода: {MAX_RISK_PCT:g}% (~${risk_usd:.0f}) | Макс. плече: {MAX_LEVERAGE:.0f}x",
        "━━━━━━━━━━━━━━━━",
        "",
    ]
    for coin_id, (emoji, name, symbol) in COINS.items():
        d = prices.get(coin_id) or {}
        if not d:
            continue
        a = analysis["coins"].get(symbol.upper(), {})
        price = d.get("current_price") or 0
        ch1 = d.get("price_change_percentage_1h_in_currency") or 0
        ch24 = d.get("price_change_percentage_24h_in_currency") or 0
        signal = (a.get("signal") or "ЧЕКАТИ").upper()
        sig_emoji = SIGNAL_EMOJI.get(signal, "⚪")
        reason = a.get("reason", "—")
        confidence = a.get("confidence", "—")

        parts.append(f"{emoji} <b>{name} ({symbol})</b>")
        parts.append(f"💰 {_fmt_price(price)} | 1г: {ch1:+.2f}% | 24г: {ch24:+.2f}%")
        parts.append(f"{sig_emoji} <b>{signal}</b> — {reason}")
        parts.append(f"Впевненість: {confidence}")

        if signal in ("LONG", "SHORT"):
            entry = a.get("entry")
            sl = a.get("stop_loss")
            tp = a.get("take_profit")
            lev_raw = a.get("leverage") or 1.0
            margin_raw = a.get("margin_usd") or 0.0

            leverage = max(1, min(int(round(lev_raw)), int(MAX_LEVERAGE)))
            margin = max(0.0, min(float(margin_raw), BALANCE_USD))
            position_usd = margin * leverage
            pct_of_balance = (margin / BALANCE_USD * 100.0) if BALANCE_USD else 0.0

            parts.append(
                f"📍 Вхід: {_fmt_price(entry)}  🎯 Тейк: {_fmt_price(tp)}  🛑 Стоп: {_fmt_price(sl)}"
            )
            parts.append(
                f"⚖ Плече: {leverage}x | 💵 Маржа: ${margin:,.0f} "
                f"({pct_of_balance:.1f}% балансу) → позиція ${position_usd:,.0f}"
            )
        parts.append("")

    parts.append(f"📊 <b>Ринок загалом:</b> {analysis.get('market') or '—'}")
    parts.append("")
    parts.append("⚠️ Автоаналіз. Не фін-порада. Рішення завжди твоє.")
    return "\n".join(parts)


def analyze(prices: dict) -> dict:
    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = build_prompt(prices)
    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(
        block.text for block in msg.content if getattr(block, "type", None) == "text"
    )
    logging.info("Claude raw response:\n%s", text)
    return parse_analysis(text)


def main() -> int:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("Missing TELEGRAM_TOKEN or TELEGRAM_CHAT_ID in .env")
        print("ERROR: Missing TELEGRAM_TOKEN or TELEGRAM_CHAT_ID in .env", file=sys.stderr)
        return 1

    if not ANTHROPIC_API_KEY:
        logging.error("Missing ANTHROPIC_API_KEY in .env")
        try:
            send_telegram(
                "⚠️ Сканер не може запуститись: відсутній <code>ANTHROPIC_API_KEY</code> у .env"
            )
        except Exception:
            logging.exception("Telegram notification failed")
        print("ERROR: Missing ANTHROPIC_API_KEY in .env", file=sys.stderr)
        return 1

    logging.info("Scan started (balance=$%.2f, risk=%s%%, max_lev=%sx)", BALANCE_USD, MAX_RISK_PCT, MAX_LEVERAGE)

    try:
        prices = fetch_prices()
    except Exception:
        logging.exception("CoinGecko fetch failed")
        try:
            send_telegram("⚠️ Не вдалось отримати дані з ринку. Спробую через годину.")
        except Exception:
            logging.exception("Telegram error notification failed")
        return 1

    try:
        analysis = analyze(prices)
    except Exception as exc:
        logging.exception("Claude analysis failed")
        try:
            send_telegram(f"⚠️ Помилка AI-аналізу: {exc}")
        except Exception:
            logging.exception("Telegram error notification failed")
        return 1

    message = format_message(prices, analysis)

    try:
        send_telegram(message)
    except Exception:
        logging.exception("Telegram send failed")
        return 1

    logging.info("Scan complete, sent to Telegram")
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
