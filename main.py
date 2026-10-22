import os
import logging
import threading
from datetime import datetime, timezone

import requests
from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    MessageHandler, filters
)

# RFX Agent OS - Telegram menu + basic market analysis
# Required environment variable on Render: BOT_TOKEN
# Crypto data: Binance public market-data API (no API key required).
# Gold data: Yahoo Finance chart endpoint (unofficial; availability may vary).

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("rfx-agent-os")

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))

web = Flask(__name__)

@web.get("/")
def health():
    return {
        "service": "RFX Agent OS",
        "status": "running",
        "bot_token_configured": bool(BOT_TOKEN),
        "time_utc": datetime.now(timezone.utc).isoformat(),
    }, 200

@web.get("/health")
def health_check():
    return {"status": "ok"}, 200


MARKETS = {
    "gold": {"label": "🥇 طلا (XAUUSD)", "symbol": "XAUUSD=X", "kind": "gold"},
    "btc": {"label": "₿ بیت‌کوین (BTCUSDT)", "symbol": "BTCUSDT", "kind": "crypto"},
    "eth": {"label": "♦️ اتریوم (ETHUSDT)", "symbol": "ETHUSDT", "kind": "crypto"},
    "zec": {"label": "🟣 زی‌کش (ZECUSDT)", "symbol": "ZECUSDT", "kind": "crypto"},
}
TIMEFRAMES = {
    "1m": {"label": "۱ دقیقه", "binance": "1m", "yahoo": "1m", "range": "1d"},
    "3m": {"label": "۳ دقیقه", "binance": "3m", "yahoo": "5m", "range": "5d"},
    "5m": {"label": "۵ دقیقه", "binance": "5m", "yahoo": "5m", "range": "5d"},
    "15m": {"label": "۱۵ دقیقه", "binance": "15m", "yahoo": "15m", "range": "5d"},
    "1h": {"label": "۱ ساعت", "binance": "1h", "yahoo": "1h", "range": "1mo"},
}

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 تحلیل بازار", callback_data="menu_analysis")],
        [InlineKeyboardButton("🥇 تحلیل طلا", callback_data="market_gold"),
         InlineKeyboardButton("₿ تحلیل بیت‌کوین", callback_data="market_btc")],
        [InlineKeyboardButton("🪙 تحلیل کریپتو", callback_data="menu_crypto"),
         InlineKeyboardButton("🎯 سیگنال معاملاتی", callback_data="menu_signal")],
        [InlineKeyboardButton("💰 مدیریت سرمایه", callback_data="menu_risk"),
         InlineKeyboardButton("⚙️ تنظیمات", callback_data="menu_settings")],
        [InlineKeyboardButton("❓ راهنما", callback_data="menu_help")],
    ])

def market_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(MARKETS["gold"]["label"], callback_data="market_gold")],
        [InlineKeyboardButton(MARKETS["btc"]["label"], callback_data="market_btc")],
        [InlineKeyboardButton(MARKETS["eth"]["label"], callback_data="market_eth"),
         InlineKeyboardButton(MARKETS["zec"]["label"], callback_data="market_zec")],
        [InlineKeyboardButton("⬅️ منوی اصلی", callback_data="home")],
    ])

def timeframe_menu(market_key):
    rows = []
    keys = list(TIMEFRAMES.keys())
    for i in range(0, len(keys), 2):
        rows.append([
            InlineKeyboardButton(TIMEFRAMES[keys[i]]["label"], callback_data=f"analyze:{market_key}:{keys[i]}")
        ] + ([
            InlineKeyboardButton(TIMEFRAMES[keys[i+1]]["label"], callback_data=f"analyze:{market_key}:{keys[i+1]}")
        ] if i + 1 < len(keys) else []))
    rows.append([InlineKeyboardButton("⬅️ انتخاب بازار", callback_data="menu_analysis"),
                 InlineKeyboardButton("🏠 منوی اصلی", callback_data="home")])
    return InlineKeyboardMarkup(rows)

def fetch_binance(symbol, interval, limit=120):
    url = "https://api.binance.com/api/v3/klines"
    response = requests.get(url, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=12)
    response.raise_for_status()
    raw = response.json()
    candles = []
    for row in raw:
        candles.append({
            "time": datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
            "open": float(row[1]), "high": float(row[2]), "low": float(row[3]),
            "close": float(row[4]), "volume": float(row[5]),
        })
    if len(candles) < 30:
        raise ValueError("داده‌های کافی از صرافی دریافت نشد.")
    return candles

def fetch_gold(interval_key):
    tf = TIMEFRAMES[interval_key]
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/XAUUSD=X"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(
        url, params={"interval": tf["yahoo"], "range": tf["range"]},
        headers=headers, timeout=12
    )
    response.raise_for_status()
    payload = response.json()
    result = payload.get("chart", {}).get("result")
    if not result:
        raise ValueError("منبع داده فعلاً برای طلا اطلاعاتی برنگرداند.")
    item = result[0]
    quote = item.get("indicators", {}).get("quote", [{}])[0]
    closes = quote.get("close") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    opens = quote.get("open") or []
    volumes = quote.get("volume") or []
    timestamps = item.get("timestamp") or []
    candles = []
    for i, close in enumerate(closes):
        if close is None or i >= len(highs) or highs[i] is None or lows[i] is None or opens[i] is None:
            continue
        candles.append({
            "time": datetime.fromtimestamp(timestamps[i], tz=timezone.utc) if i < len(timestamps) else datetime.now(timezone.utc),
            "open": float(opens[i]), "high": float(highs[i]), "low": float(lows[i]),
            "close": float(close), "volume": float(volumes[i] or 0) if i < len(volumes) else 0.0,
        })
    if len(candles) < 20:
        raise ValueError("داده‌های کافی برای تحلیل طلا دریافت نشد.")
    # Yahoo has no native 3m candles; aggregate 5m source approximately to 3m is not reliable.
    # Explicitly label 3m requests as sourced from 5m data.
    return candles

def ema(values, period):
    alpha = 2 / (period + 1)
    current = values[0]
    out = []
    for value in values:
        current = alpha * value + (1 - alpha) * current
        out.append(current)
    return out

def calc_rsi(closes, period=14):
    if len(closes) <= period:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
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
    last = closes[-1]
    e9 = ema(closes, 9)[-1]
    e21 = ema(closes, 21)[-1]
    e50 = ema(closes, min(50, max(10, len(closes)-1)))[-1]
    rsi = calc_rsi(closes)
    recent = candles[-30:]
    support = min(c["low"] for c in recent)
    resistance = max(c["high"] for c in recent)
    if e9 > e21 and last > e50:
        trend = "📈 صعودی"
    elif e9 < e21 and last < e50:
        trend = "📉 نزولی"
    else:
        trend = "↔️ خنثی / نوسانی"

    if rsi is not None and rsi >= 70:
        momentum = "اشباع خرید احتمالی"
    elif rsi is not None and rsi <= 30:
        momentum = "اشباع فروش احتمالی"
    else:
        momentum = "در محدوده میانی"

    if e9 > e21 and rsi is not None and 40 <= rsi <= 68:
        bias = "تمایل صعودی؛ منتظر تأیید کندل و مدیریت ریسک بمان."
    elif e9 < e21 and rsi is not None and 32 <= rsi <= 60:
        bias = "تمایل نزولی؛ منتظر تأیید کندل و مدیریت ریسک بمان."
    else:
        bias = "سیگنال قطعی وجود ندارد؛ بهتر است صبر کنی تا جهت روشن‌تر شود."

    return {
        "price": last, "trend": trend, "rsi": rsi, "momentum": momentum,
        "support": support, "resistance": resistance, "bias": bias,
        "ema9": e9, "ema21": e21, "ema50": e50,
        "last_time": candles[-1]["time"],
    }

def fmt_price(value):
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.4f}"
    return f"{value:.8f}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *به RFX Agent OS خوش آمدی*\\n\\n"
        "از منوی زیر یک وظیفه انتخاب کن. برای تحلیل، بازار و تایم‌فریم را انتخاب کن.\\n"
        "داده‌ها از منابع عمومی دریافت می‌شوند؛ ممکن است تأخیر یا قطعی داشته باشند."
    )
    await update.effective_message.reply_text(text, reply_markup=main_menu(), parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "راهنما:\\n"
        "• از «تحلیل بازار» بازار و تایم‌فریم را انتخاب کن.\\n"
        "• تحلیل شامل روند EMA، RSI و محدوده حمایت/مقاومت اخیر است.\\n"
        "• این نسخه تحلیل ساده است، نه تضمین سود یا سیگنال قطعی.\\n"
        "• برای کارکرد ربات، متغیر BOT_TOKEN باید در Render تنظیم شده باشد.",
        reply_markup=main_menu()
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "home":
        await query.edit_message_text("🏠 منوی اصلی RFX Agent OS", reply_markup=main_menu())
        return
    if data in ("menu_analysis", "menu_crypto"):
        await query.edit_message_text("بازار موردنظر را انتخاب کن:", reply_markup=market_menu())
        return
    if data.startswith("market_"):
        key = data.replace("market_", "", 1)
        if key not in MARKETS:
            await query.edit_message_text("این بازار شناخته نشد.", reply_markup=main_menu())
            return
        await query.edit_message_text(
            f"بازار انتخاب‌شده: {MARKETS[key]['label']}\\nحالا تایم‌فریم را انتخاب کن:",
            reply_markup=timeframe_menu(key)
        )
        return
    if data.startswith("analyze:"):
        try:
            _, market_key, tf_key = data.split(":")
            if market_key not in MARKETS or tf_key not in TIMEFRAMES:
                raise ValueError("invalid selection")
            market = MARKETS[market_key]
            tf = TIMEFRAMES[tf_key]
            await query.edit_message_text(
                f"⏳ در حال دریافت داده و تحلیل {market['label']} در تایم‌فریم {tf['label']}..."
            )
            if market["kind"] == "crypto":
                candles = fetch_binance(market["symbol"], tf["binance"])
            else:
                candles = fetch_gold(tf_key)
            result = analyze(candles)
            rsi_text = f"{result['rsi']:.1f}" if result["rsi"] is not None else "نامشخص"
            note = ""
            if market_key == "gold" and tf_key == "3m":
                note = "\\n⚠️ منبع عمومی طلا کندل ۳ دقیقه واقعی نداد؛ داده دریافتی از منبع ۵ دقیقه‌ای است."
            message = (
                f"📊 *تحلیل {market['label']}*\\n"
                f"⏱ تایم‌فریم انتخابی: {tf['label']}{note}\\n\\n"
                f"💵 قیمت آخرین کندل: `{fmt_price(result['price'])}`\\n"
                f"🧭 وضعیت روند: {result['trend']}\\n"
                f"📉 RSI(14): `{rsi_text}` — {result['momentum']}\\n"
                f"〰️ EMA 9: `{fmt_price(result['ema9'])}`\\n"
                f"〰️ EMA 21: `{fmt_price(result['ema21'])}`\\n"
                f"〰️ EMA 50: `{fmt_price(result['ema50'])}`\\n\\n"
                f"🟢 حمایت تقریبی اخیر: `{fmt_price(result['support'])}`\\n"
                f"🔴 مقاومت تقریبی اخیر: `{fmt_price(result['resistance'])}`\\n\\n"
                f"🧠 برداشت الگوریتم: {result['bias']}\\n\\n"
                f"🕒 زمان آخرین داده: {result['last_time'].strftime('%Y-%m-%d %H:%M UTC')}\\n"
                "⚠️ این خروجی تحلیل تکنیکال ساده است، نه توصیه مالی و نه تضمین ورود موفق."
            )
            await query.edit_message_text(
                message, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 تحلیل دوباره", callback_data=f"analyze:{market_key}:{tf_key}")],
                    [InlineKeyboardButton("⬅️ انتخاب تایم‌فریم", callback_data=f"market_{market_key}")],
                    [InlineKeyboardButton("🏠 منوی اصلی", callback_data="home")],
                ])
            )
        except Exception as exc:
            logger.exception("Market analysis failed")
            await query.edit_message_text(
                "❌ نتوانستم داده بازار را دریافت یا تحلیل کنم. ممکن است API موقتاً در دسترس نباشد یا نماد پشتیبانی نشود.\\n"
                f"جزئیات: {str(exc)[:250]}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 تلاش دوباره", callback_data=f"analyze:{market_key}:{tf_key}")],
                    [InlineKeyboardButton("🏠 منوی اصلی", callback_data="home")],
                ])
            )
        return
    if data == "menu_signal":
        await query.edit_message_text(
            "🎯 سیگنال معاملاتی\\n\\nدر این نسخه، به‌جای صدور سیگنال قطعی، تحلیل روند و RSI ارائه می‌شود. "
            "برای ورود، حتماً تأیید قیمت، حد ضرر و ریسک معامله را خودت بررسی کن.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📊 شروع تحلیل", callback_data="menu_analysis")],
                                               [InlineKeyboardButton("🏠 منوی اصلی", callback_data="home")]])
        )
        return
    if data == "menu_risk":
        await query.edit_message_text(
            "💰 مدیریت سرمایه\\nبرای محاسبه حجم، باید موجودی حساب، درصد ریسک و فاصله حد ضرر مشخص باشد.\\n"
            "این بخش در نسخه بعدی به فرم محاسبه‌گر دکمه‌ای تبدیل می‌شود.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 منوی اصلی", callback_data="home")]])
        )
        return
    if data == "menu_settings":
        await query.edit_message_text(
            "⚙️ تنظیمات\\nدر حال حاضر تایم‌فریم هنگام تحلیل انتخاب می‌شود. تنظیمات پیشرفته در نسخه بعدی اضافه می‌شود.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 منوی اصلی", callback_data="home")]])
        )
        return
    if data == "menu_help":
        await query.edit_message_text(
            "❓ راهنما\\n۱) تحلیل بازار را بزن.\\n۲) نماد را انتخاب کن.\\n۳) تایم‌فریم را انتخاب کن.\\n۴) نتیجه تحلیل را ببین.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 منوی اصلی", callback_data="home")]])
        )
        return

async def text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "برای اجرای وظایف از دکمه‌های منو استفاده کن. دستور /start منوی اصلی را باز می‌کند.",
        reply_markup=main_menu()
    )

def run_web():
    web.run(host="0.0.0.0", port=PORT, use_reloader=False)

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing. Add your Telegram bot token in Render → Environment "
            "as BOT_TOKEN, then redeploy."
        )
    threading.Thread(target=run_web, daemon=True).start()
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_fallback))
    logger.info("Starting RFX Agent OS Telegram bot with polling.")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
