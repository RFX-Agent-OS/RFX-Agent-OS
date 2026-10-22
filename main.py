import os
import time
import logging
import threading
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes
)

# ============================================================
# RFX Agent OS V2.0 — public market data, no trading execution
# Required Render environment variable: TELEGRAM_BOT_TOKEN
# ============================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("rfx-agent-os")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "10000"))
HTTP_TIMEOUT = 12
USER_AGENT = "RFX-Agent-OS/2.0 (market-data-bot)"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

app = Flask(__name__)

CRYPTO = {
    "BTCUSDT": {"name": "Bitcoin (BTC/USDT)", "binance": "BTCUSDT", "bybit": "BTCUSDT",
                "okx": "BTC-USDT", "gate": "BTC_USDT", "kucoin": "BTC-USDT", "kraken": "XBTUSDT"},
    "ETHUSDT": {"name": "Ethereum (ETH/USDT)", "binance": "ETHUSDT", "bybit": "ETHUSDT",
                "okx": "ETH-USDT", "gate": "ETH_USDT", "kucoin": "ETH-USDT", "kraken": "ETHUSDT"},
    "SOLUSDT": {"name": "Solana (SOL/USDT)", "binance": "SOLUSDT", "bybit": "SOLUSDT",
                "okx": "SOL-USDT", "gate": "SOL_USDT", "kucoin": "SOL-USDT", "kraken": "SOLUSDT"},
    "ZECUSDT": {"name": "Zcash (ZEC/USDT)", "binance": "ZECUSDT", "bybit": "ZECUSDT",
                "okx": "ZEC-USDT", "gate": "ZEC_USDT", "kucoin": "ZEC-USDT", "kraken": "ZECUSDT"},
}

TIMEFRAMES = {
    "1m": {"label": "1 دقیقه", "binance": "1m", "bybit": "1", "okx": "1m", "gate": "1m", "kucoin": "1min", "yahoo": "1m"},
    "3m": {"label": "3 دقیقه", "binance": "3m", "bybit": "3", "okx": "3m", "gate": "3m", "kucoin": "3min", "yahoo": "1m"},
    "5m": {"label": "5 دقیقه", "binance": "5m", "bybit": "5", "okx": "5m", "gate": "5m", "kucoin": "5min", "yahoo": "5m"},
    "15m": {"label": "15 دقیقه", "binance": "15m", "bybit": "15", "okx": "15m", "gate": "15m", "kucoin": "15min", "yahoo": "15m"},
    "1h": {"label": "1 ساعت", "binance": "1h", "bybit": "60", "okx": "1H", "gate": "1h", "kucoin": "1hour", "yahoo": "60m"},
}

def get_json(url, params=None):
    response = SESSION.get(url, params=params, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    return response.json()

def normalize_rows(rows, source, reverse=False):
    """Normalize candle rows to dictionaries: time, open, high, low, close, volume."""
    if reverse:
        rows = list(reversed(rows))
    result = []
    for row in rows:
        try:
            # Source adapters pass [timestamp, open, high, low, close, volume].
            ts, o, h, l, c, v = row[:6]
            ts = float(ts)
            # Convert milliseconds to seconds where needed.
            if ts > 10_000_000_000:
                ts /= 1000
            result.append({
                "time": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "open": float(o), "high": float(h), "low": float(l),
                "close": float(c), "volume": float(v or 0),
            })
        except (ValueError, TypeError, IndexError, OverflowError):
            continue
    return result

def fetch_binance(symbol, tf):
    interval = TIMEFRAMES[tf]["binance"]
    data = get_json("https://api.binance.com/api/v3/klines",
                    {"symbol": symbol, "interval": interval, "limit": 120})
    return normalize_rows([[r[0], r[1], r[2], r[3], r[4], r[5]] for r in data], "binance")

def fetch_bybit(symbol, tf):
    interval = TIMEFRAMES[tf]["bybit"]
    data = get_json("https://api.bybit.com/v5/market/kline",
                    {"category": "spot", "symbol": symbol, "interval": interval, "limit": 120})
    if str(data.get("retCode")) != "0":
        raise RuntimeError("Bybit API: " + str(data.get("retMsg", "unknown error")))
    rows = data.get("result", {}).get("list", [])
    # Bybit returns newest first, and timestamps are milliseconds.
    return normalize_rows([[r[0], r[1], r[2], r[3], r[4], r[5]] for r in rows], "bybit", reverse=True)

def fetch_okx(symbol, tf):
    inst = CRYPTO[symbol]["okx"]
    bar = TIMEFRAMES[tf]["okx"]
    data = get_json("https://www.okx.com/api/v5/market/candles",
                    {"instId": inst, "bar": bar, "limit": "120"})
    if data.get("code") != "0":
        raise RuntimeError("OKX API: " + str(data.get("msg", "unknown error")))
    rows = data.get("data", [])
    # OKX: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
    return normalize_rows([[r[0], r[1], r[2], r[3], r[4], r[5]] for r in rows], "okx", reverse=True)

def fetch_gate(symbol, tf):
    pair = CRYPTO[symbol]["gate"]
    interval = TIMEFRAMES[tf]["gate"]
    data = get_json("https://api.gateio.ws/api/v4/spot/candlesticks",
                    {"currency_pair": pair, "interval": interval, "limit": 120})
    # Gate rows: [timestamp, quote_volume, close, high, low, open, base_volume]
    rows = [[r[0], r[5], r[3], r[4], r[2], r[6]] for r in data]
    return normalize_rows(rows, "gate")

def fetch_kucoin(symbol, tf):
    pair = CRYPTO[symbol]["kucoin"]
    interval = TIMEFRAMES[tf]["kucoin"]
    data = get_json("https://api.kucoin.com/api/v1/market/candles",
                    {"symbol": pair, "type": interval})
    if data.get("code") != "200000":
        raise RuntimeError("KuCoin API: " + str(data.get("msg", "unknown error")))
    rows = data.get("data", [])
    # KuCoin rows: [time, open, close, high, low, volume, turnover]
    return normalize_rows([[r[0], r[1], r[3], r[4], r[2], r[5]] for r in rows], "kucoin", reverse=True)

def fetch_crypto(symbol, tf):
    errors = []
    providers = [
        ("Binance", lambda: fetch_binance(CRYPTO[symbol]["binance"], tf)),
        ("Bybit", lambda: fetch_bybit(CRYPTO[symbol]["bybit"], tf)),
        ("OKX", lambda: fetch_okx(symbol, tf)),
        ("Gate.io", lambda: fetch_gate(symbol, tf)),
        ("KuCoin", lambda: fetch_kucoin(symbol, tf)),
    ]
    for name, provider in providers:
        try:
            candles = provider()
            if len(candles) >= 20:
                log.info("Market data loaded: %s %s from %s (%s candles)", symbol, tf, name, len(candles))
                return candles[-120:], name
            errors.append(f"{name}: تعداد کندل ناکافی ({len(candles)})")
        except Exception as exc:
            errors.append(f"{name}: {str(exc)[:180]}")
            log.warning("Provider failed (%s, %s, %s): %s", symbol, tf, name, exc)
    raise RuntimeError("؛ ".join(errors[-5:]))

def fetch_gold(tf):
    """Yahoo Finance chart API for XAUUSD=X. It may be unavailable from some hosting regions."""
    interval = TIMEFRAMES[tf]["yahoo"]
    range_value = "5d" if interval in ("1m", "5m", "15m") else "1mo"
    errors = []
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            url = f"https://{host}/v8/finance/chart/XAUUSD%3DX"
            data = get_json(url, {"range": range_value, "interval": interval, "includePrePost": "true"})
            chart = data.get("chart", {})
            if chart.get("error"):
                raise RuntimeError(str(chart["error"]))
            result = chart.get("result") or []
            if not result:
                raise RuntimeError("Yahoo Finance داده‌ای برنگرداند")
            item = result[0]
            timestamps = item.get("timestamp") or []
            q = (item.get("indicators", {}).get("quote") or [{}])[0]
            rows = []
            for i, ts in enumerate(timestamps):
                vals = [q.get(k, [None] * len(timestamps))[i] for k in ("open", "high", "low", "close", "volume")]
                if any(v is None for v in vals[:4]):
                    continue
                rows.append([ts, vals[0], vals[1], vals[2], vals[3], vals[4] or 0])
            candles = normalize_rows(rows, "yahoo")
            if len(candles) >= 20:
                log.info("Gold data loaded from %s (%s candles)", host, len(candles))
                return candles[-120:], "Yahoo Finance"
            raise RuntimeError(f"کندل کافی نیست: {len(candles)}")
        except Exception as exc:
            errors.append(f"{host}: {str(exc)[:160]}")
            log.warning("Gold provider failed (%s): %s", host, exc)
    # Current price fallback only: do not pretend this is enough for candle analysis.
    try:
        item = get_json("https://api.gold-api.com/price/XAU")
        price = float(item.get("price"))
        raise RuntimeError(
            f"قیمت لحظه‌ای طلا از منبع جایگزین دریافت شد: {price:.2f}؛ "
            "اما کندل تاریخی برای تحلیل در دسترس نیست. جزئیات Yahoo: " + "؛ ".join(errors)
        )
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError("داده کندلی طلا دریافت نشد. " + "؛ ".join(errors))

def ema(values, period):
    if not values:
        return []
    alpha = 2 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return out

def rsi(values, period=14):
    if len(values) <= period:
        return None
    gains, losses = [], []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def analyze(candles):
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    last = closes[-1]
    e20 = ema(closes, 20)[-1]
    e50 = ema(closes, min(50, max(2, len(closes) - 1)))[-1]
    momentum = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 and closes[-6] else 0
    rv = rsi(closes)
    support = min(lows[-20:])
    resistance = max(highs[-20:])
    if last > e20 > e50:
        trend = "صعودی"
    elif last < e20 < e50:
        trend = "نزولی"
    else:
        trend = "خنثی / در حال نوسان"
    if rv is None:
        rsi_text = "نامشخص"
    else:
        rsi_text = f"{rv:.1f}"
    if rv is not None and rv >= 70:
        rsi_state = "اشباع خرید احتمالی"
    elif rv is not None and rv <= 30:
        rsi_state = "اشباع فروش احتمالی"
    else:
        rsi_state = "میانه"
    if last > resistance * 0.999:
        structure = "قیمت نزدیک سقف ۲۰ کندل اخیر است؛ شکست هنوز باید تأیید شود."
    elif last < support * 1.001:
        structure = "قیمت نزدیک کف ۲۰ کندل اخیر است؛ شکست نزولی نیاز به تأیید دارد."
    else:
        structure = "قیمت داخل محدوده ۲۰ کندل اخیر قرار دارد."
    return {
        "price": last, "trend": trend, "rsi": rsi_text, "rsi_state": rsi_state,
        "momentum": momentum, "support": support, "resistance": resistance,
        "ema20": e20, "ema50": e50, "structure": structure,
        "last_time": candles[-1]["time"],
    }

def fmt_price(value):
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:.4f}"
    return f"{value:.8f}".rstrip("0").rstrip(".")

def report(title, tf, candles, source):
    a = analyze(candles)
    momentum_word = "مثبت" if a["momentum"] > 0 else ("منفی" if a["momentum"] < 0 else "خنثی")
    return (
        f"📊 <b>{title}</b>\n"
        f"⏱ تایم‌فریم: {TIMEFRAMES[tf]['label']}\n"
        f"💵 آخرین قیمت کندل: <b>{fmt_price(a['price'])}</b>\n"
        f"📈 روند EMA: <b>{a['trend']}</b>\n"
        f"⚡ تغییر ۵ کندل: {a['momentum']:+.2f}% ({momentum_word})\n"
        f"📉 RSI(14): {a['rsi']} — {a['rsi_state']}\n"
        f"🟢 حمایت تقریبی ۲۰ کندل: <b>{fmt_price(a['support'])}</b>\n"
        f"🔴 مقاومت تقریبی ۲۰ کندل: <b>{fmt_price(a['resistance'])}</b>\n"
        f"🧭 ساختار: {a['structure']}\n"
        f"🕒 زمان آخرین کندل: {a['last_time']}\n"
        f"🔌 منبع داده: {source}\n\n"
        "⚠️ این گزارش آموزشی و مبتنی بر داده‌های دریافتی است؛ سیگنال قطعی یا تضمین سود نیست. "
        "قبل از معامله، اسپرد و قیمت زنده بروکر را بررسی کن."
    )

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🥇 تحلیل طلا XAUUSD", callback_data="asset:GOLD")],
        [InlineKeyboardButton("₿ بیت‌کوین BTC", callback_data="asset:BTCUSDT"),
         InlineKeyboardButton("Ξ اتریوم ETH", callback_data="asset:ETHUSDT")],
        [InlineKeyboardButton("◎ سولانا SOL", callback_data="asset:SOLUSDT"),
         InlineKeyboardButton("Ⓩ زی‌کش ZEC", callback_data="asset:ZECUSDT")],
        [InlineKeyboardButton("🔄 راهنما / شروع مجدد", callback_data="home")],
    ])

def timeframe_menu(asset):
    rows = []
    keys = list(TIMEFRAMES.keys())
    for i in range(0, len(keys), 3):
        rows.append([
            InlineKeyboardButton(TIMEFRAMES[k]["label"], callback_data=f"tf:{asset}:{k}")
            for k in keys[i:i+3]
        ])
    rows.append([InlineKeyboardButton("⬅️ برگشت به بازارها", callback_data="home")])
    return InlineKeyboardMarkup(rows)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 <b>RFX Agent OS — نسخه 2.0</b>\n\n"
        "بازار موردنظر را انتخاب کن. ربات داده‌های عمومی بازار را از چند منبع امتحان می‌کند "
        "و در صورت خطای یک منبع، سراغ منبع بعدی می‌رود.\n\n"
        "در این نسخه فقط تحلیل و گزارش انجام می‌شود؛ معامله‌ای اجرا نمی‌شود."
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=main_menu(), parse_mode="HTML")
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=main_menu(), parse_mode="HTML")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "دستورها:\n/start — منوی اصلی\n/health — وضعیت سرویس\n\n"
        "بازار و تایم‌فریم را از دکمه‌ها انتخاب کن."
    )

async def health_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ ربات فعال است. این فقط وضعیت سرویس را بررسی می‌کند، نه دسترسی لحظه‌ای به همه منابع بازار.")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    try:
        if data == "home":
            await start(update, context)
            return
        if data.startswith("asset:"):
            asset = data.split(":", 1)[1]
            if asset == "GOLD":
                title = "طلا (XAUUSD)"
            else:
                title = CRYPTO.get(asset, {}).get("name", asset)
            await query.edit_message_text(
                f"بازار انتخاب‌شده: <b>{title}</b>\nتایم‌فریم را انتخاب کن:",
                reply_markup=timeframe_menu(asset), parse_mode="HTML"
            )
            return
        if data.startswith("tf:"):
            _, asset, tf = data.split(":", 2)
            if tf not in TIMEFRAMES:
                await query.edit_message_text("تایم‌فریم نامعتبر است. /start را بزن.")
                return
            await query.edit_message_text(
                f"⏳ در حال دریافت داده‌های {('طلا' if asset == 'GOLD' else CRYPTO.get(asset, {}).get('name', asset))} "
                f"در تایم‌فریم {TIMEFRAMES[tf]['label']}...\nلطفاً چند لحظه صبر کن."
            )
            if asset == "GOLD":
                candles, source = fetch_gold(tf)
                title = "طلا (XAUUSD)"
            else:
                if asset not in CRYPTO:
                    raise RuntimeError("بازار انتخاب‌شده پشتیبانی نمی‌شود.")
                candles, source = fetch_crypto(asset, tf)
                title = CRYPTO[asset]["name"]
            text = report(title, tf, candles, source)
            await query.edit_message_text(
                text, reply_markup=timeframe_menu(asset), parse_mode="HTML",
                disable_web_page_preview=True
            )
            return
    except Exception as exc:
        log.exception("Market analysis failed")
        msg = (
            "❌ <b>داده‌های بازار بارگذاری نشد.</b>\n\n"
            f"جزئیات: <code>{str(exc)[:900]}</code>\n\n"
            "این خطا ممکن است ناشی از محدودیت منطقه‌ای/IP یا در دسترس نبودن API باشد. "
            "منابع جایگزین امتحان شدند. کمی بعد دوباره تلاش کن."
        )
        try:
            await query.edit_message_text(msg, reply_markup=main_menu(), parse_mode="HTML")
        except Exception:
            log.exception("Could not send market-data error to Telegram")

@app.get("/")
def home():
    return jsonify({"service": "RFX Agent OS", "status": "running", "version": "2.0"})

@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "RFX Agent OS", "version": "2.0"})

async def run_telegram():
    if not TOKEN:
        log.error("TELEGRAM_BOT_TOKEN is missing; Telegram bot will not start.")
        return
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("health", health_cmd))
    application.add_handler(CallbackQueryHandler(callback_handler))
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=False)
    log.info("Telegram polling started")
    try:
        while True:
            await __import__("asyncio").sleep(3600)
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

def bot_thread():
    import asyncio
    try:
        asyncio.run(run_telegram())
    except Exception:
        log.exception("Telegram bot thread crashed")

if __name__ == "__main__":
    threading.Thread(target=bot_thread, daemon=True, name="telegram-bot").start()
    # Render web service health endpoint; Telegram polling runs in background thread.
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
