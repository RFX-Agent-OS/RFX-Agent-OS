import os
import logging
import threading
import asyncio
from datetime import datetime, timezone

import requests
from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

# RFX Agent OS. Crypto candles use Bybit public API; gold candles use Yahoo Finance. Render token variable can be BOT_TOKEN, TELEGRAM_BOT_TOKEN, or TELEGRAM_TOKEN.
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", level=logging.INFO)
logger = logging.getLogger("rfx-agent-os")
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
PORT = int(os.getenv("PORT", "10000"))
web = Flask(__name__)

@web.get("/")
def health():
    return {"service": "RFX Agent OS", "status": "running", "bot_token_configured": bool(BOT_TOKEN),
            "time_utc": datetime.now(timezone.utc).isoformat()}, 200

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
    "1m": {"label": "۱ دقیقه", "bybit": "1", "yahoo": "1m", "range": "1d"},
    "3m": {"label": "۳ دقیقه", "bybit": "3", "yahoo": "5m", "range": "5d"},
    "5m": {"label": "۵ دقیقه", "bybit": "5", "yahoo": "5m", "range": "5d"},
    "15m": {"label": "۱۵ دقیقه", "bybit": "15", "yahoo": "15m", "range": "5d"},
    "1h": {"label": "۱ ساعت", "bybit": "60", "yahoo": "1h", "range": "1mo"},
}

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 تحلیل بازار", callback_data="menu_analysis")],
        [InlineKeyboardButton("🥇 تحلیل طلا", callback_data="market_gold"), InlineKeyboardButton("₿ تحلیل بیت‌کوین", callback_data="market_btc")],
        [InlineKeyboardButton("🪙 تحلیل کریپتو", callback_data="menu_crypto"), InlineKeyboardButton("🎯 سیگنال معاملاتی", callback_data="menu_signal")],
        [InlineKeyboardButton("💰 مدیریت سرمایه", callback_data="menu_risk"), InlineKeyboardButton("⚙️ تنظیمات", callback_data="menu_settings")],
        [InlineKeyboardButton("❓ راهنما", callback_data="menu_help")],
    ])

def market_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(MARKETS["gold"]["label"], callback_data="market_gold")],
        [InlineKeyboardButton(MARKETS["btc"]["label"], callback_data="market_btc")],
        [InlineKeyboardButton(MARKETS["eth"]["label"], callback_data="market_eth"), InlineKeyboardButton(MARKETS["zec"]["label"], callback_data="market_zec")],
        [InlineKeyboardButton("⬅️ منوی اصلی", callback_data="home")],
    ])

def timeframe_menu(market_key):
    keys = list(TIMEFRAMES)
    rows = []
    for i in range(0, len(keys), 2):
        row = [InlineKeyboardButton(TIMEFRAMES[keys[i]]["label"], callback_data=f"analyze:{market_key}:{keys[i]}")]
        if i + 1 < len(keys):
            row.append(InlineKeyboardButton(TIMEFRAMES[keys[i+1]]["label"], callback_data=f"analyze:{market_key}:{keys[i+1]}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ انتخاب بازار", callback_data="menu_analysis"), InlineKeyboardButton("🏠 منوی اصلی", callback_data="home")])
    return InlineKeyboardMarkup(rows)

def fetch_bybit(symbol, interval, limit=120):
    """Fetch public spot candles from Bybit V5; no API key is required."""
    r = requests.get(
        "https://api.bybit.com/v5/market/kline",
        params={"category": "spot", "symbol": symbol, "interval": interval, "limit": limit},
        headers={"User-Agent": "RFX-Agent-OS/1.0"}, timeout=15,
    )
    r.raise_for_status()
    payload = r.json()
    if payload.get("retCode") != 0:
        raise ValueError(f"Bybit: {payload.get('retMsg', 'خطای دریافت داده')}")
    rows = (payload.get("result") or {}).get("list") or []
    # Bybit returns candles newest-first; reverse them for indicator calculations.
    candles = []
    for row in reversed(rows):
        candles.append({
            "time": datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc),
            "open": float(row[1]), "high": float(row[2]), "low": float(row[3]),
            "close": float(row[4]), "volume": float(row[5]),
        })
    if len(candles) < 30:
        raise ValueError("داده‌های کافی از Bybit دریافت نشد؛ نماد ممکن است در بازار اسپات موجود نباشد.")
    return candles

def fetch_gold(tf_key):
    tf = TIMEFRAMES[tf_key]
    r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/XAUUSD=X",
                     params={"interval": tf["yahoo"], "range": tf["range"]},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
    r.raise_for_status()
    result = r.json().get("chart", {}).get("result")
    if not result:
        raise ValueError("منبع داده فعلاً برای طلا اطلاعاتی برنگرداند.")
    item = result[0]
    q = item.get("indicators", {}).get("quote", [{}])[0]
    closes, highs, lows, opens = q.get("close") or [], q.get("high") or [], q.get("low") or [], q.get("open") or []
    volumes, times = q.get("volume") or [], item.get("timestamp") or []
    candles = []
    for i, close in enumerate(closes):
        if close is None or i >= len(highs) or highs[i] is None or lows[i] is None or i >= len(opens) or opens[i] is None:
            continue
        candles.append({"time": datetime.fromtimestamp(times[i], tz=timezone.utc) if i < len(times) else datetime.now(timezone.utc),
                        "open": float(opens[i]), "high": float(highs[i]), "low": float(lows[i]), "close": float(close),
                        "volume": float(volumes[i] or 0) if i < len(volumes) else 0.0})
    if len(candles) < 20:
        raise ValueError("داده‌های کافی برای تحلیل طلا دریافت نشد.")
    return candles

def ema(values, period):
    alpha = 2 / (period + 1)
    current, out = values[0], []
    for value in values:
        current = alpha * value + (1-alpha) * current
        out.append(current)
    return out

def calc_rsi(closes, period=14):
    if len(closes) <= period:
        return None
    changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains, losses = [max(x, 0) for x in changes], [max(-x, 0) for x in changes]
    ag, al = sum(gains[:period])/period, sum(losses[:period])/period
    for i in range(period, len(gains)):
        ag = (ag*(period-1)+gains[i])/period
        al = (al*(period-1)+losses[i])/period
    if al == 0:
        return 100.0
    return 100 - 100/(1 + ag/al)

def analyze(candles):
    closes = [c["close"] for c in candles]
    last = closes[-1]
    e9, e21 = ema(closes, 9)[-1], ema(closes, 21)[-1]
    e50 = ema(closes, min(50, max(10, len(closes)-1)))[-1]
    rsi = calc_rsi(closes)
    recent = candles[-30:]
    if e9 > e21 and last > e50: trend = "📈 صعودی"
    elif e9 < e21 and last < e50: trend = "📉 نزولی"
    else: trend = "↔️ خنثی / نوسانی"
    if rsi is not None and rsi >= 70: momentum = "اشباع خرید احتمالی"
    elif rsi is not None and rsi <= 30: momentum = "اشباع فروش احتمالی"
    else: momentum = "در محدوده میانی"
    if e9 > e21 and rsi is not None and 40 <= rsi <= 68: bias = "تمایل صعودی؛ منتظر تأیید کندل و مدیریت ریسک بمان."
    elif e9 < e21 and rsi is not None and 32 <= rsi <= 60: bias = "تمایل نزولی؛ منتظر تأیید کندل و مدیریت ریسک بمان."
    else: bias = "سیگنال قطعی وجود ندارد؛ بهتر است صبر کنی تا جهت روشن‌تر شود."
    return {"price": last, "trend": trend, "rsi": rsi, "momentum": momentum,
            "support": min(c["low"] for c in recent), "resistance": max(c["high"] for c in recent),
            "bias": bias, "ema9": e9, "ema21": e21, "ema50": e50, "last_time": candles[-1]["time"]}

def fmt_price(v):
    if v >= 1000: return f"{v:,.2f}"
    if v >= 1: return f"{v:,.4f}"
    return f"{v:.8f}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("🤖 به RFX Agent OS خوش آمدی\n\nاز منوی زیر یک وظیفه انتخاب کن.", reply_markup=main_menu())

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("راهنما:\n• «تحلیل بازار» را بزن و بازار و تایم‌فریم را انتخاب کن.\n• تحلیل شامل EMA، RSI و حمایت/مقاومت تقریبی است.\n• این ابزار تحلیل ساده است و سود یا سیگنال قطعی تضمین نمی‌کند.", reply_markup=main_menu())

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if data == "home":
        await query.edit_message_text("🏠 منوی اصلی RFX Agent OS", reply_markup=main_menu()); return
    if data in ("menu_analysis", "menu_crypto"):
        await query.edit_message_text("بازار موردنظر را انتخاب کن:", reply_markup=market_menu()); return
    if data.startswith("market_"):
        key = data.replace("market_", "", 1)
        if key not in MARKETS:
            await query.edit_message_text("این بازار شناخته نشد.", reply_markup=main_menu()); return
        await query.edit_message_text(f"بازار انتخاب‌شده: {MARKETS[key]['label']}\nحالا تایم‌فریم را انتخاب کن:", reply_markup=timeframe_menu(key)); return
    if data.startswith("analyze:"):
        market_key, tf_key = "btc", "5m"
        try:
            _, market_key, tf_key = data.split(":")
            market, tf = MARKETS[market_key], TIMEFRAMES[tf_key]
            await query.edit_message_text(f"⏳ در حال دریافت داده و تحلیل {market['label']} در تایم‌فریم {tf['label']}...")
            candles = fetch_bybit(market["symbol"], tf["bybit"]) if market["kind"] == "crypto" else fetch_gold(tf_key)
            result = analyze(candles)
            rsi_text = f"{result['rsi']:.1f}" if result["rsi"] is not None else "نامشخص"
            note = "\n⚠️ منبع عمومی طلا کندل ۳ دقیقه واقعی نمی‌دهد؛ داده منبع ۵ دقیقه‌ای است." if market_key == "gold" and tf_key == "3m" else ""
            msg = (f"📊 تحلیل {market['label']}\n⏱ تایم‌فریم: {tf['label']}{note}\n\n"
                   f"💵 قیمت آخرین کندل: {fmt_price(result['price'])}\n🧭 روند: {result['trend']}\n"
                   f"📉 RSI(14): {rsi_text} — {result['momentum']}\nEMA 9: {fmt_price(result['ema9'])}\n"
                   f"EMA 21: {fmt_price(result['ema21'])}\nEMA 50: {fmt_price(result['ema50'])}\n\n"
                   f"🟢 حمایت تقریبی: {fmt_price(result['support'])}\n🔴 مقاومت تقریبی: {fmt_price(result['resistance'])}\n\n"
                   f"🧠 برداشت الگوریتم: {result['bias']}\n🕒 آخرین داده: {result['last_time'].strftime('%Y-%m-%d %H:%M UTC')}\n\n"
                   "⚠️ تحلیل ساده است؛ توصیه مالی یا تضمین سود نیست.")
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 تحلیل دوباره", callback_data=f"analyze:{market_key}:{tf_key}")],
                [InlineKeyboardButton("⬅️ انتخاب تایم‌فریم", callback_data=f"market_{market_key}")],
                [InlineKeyboardButton("🏠 منوی اصلی", callback_data="home")]]))
        except Exception as exc:
            logger.exception("Market analysis failed")
            await query.edit_message_text("❌ دریافت داده یا تحلیل ناموفق بود؛ ممکن است منبع موقتاً در دسترس نباشد.\n"
                f"جزئیات: {str(exc)[:200]}", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 تلاش دوباره", callback_data=f"analyze:{market_key}:{tf_key}")],
                [InlineKeyboardButton("🏠 منوی اصلی", callback_data="home")]]))
        return
    if data == "menu_signal":
        await query.edit_message_text("🎯 در این نسخه به‌جای سیگنال قطعی، تحلیل روند و RSI ارائه می‌شود. قبل از معامله، حد ضرر و ریسک را بررسی کن.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📊 شروع تحلیل", callback_data="menu_analysis")],[InlineKeyboardButton("🏠 منوی اصلی", callback_data="home")]])); return
    if data == "menu_risk":
        await query.edit_message_text("💰 برای محاسبه حجم معامله باید موجودی، درصد ریسک و فاصله حد ضرر مشخص باشد. فرم محاسبه‌گر هنوز فعال نیست.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 منوی اصلی", callback_data="home")]])); return
    if data == "menu_settings":
        await query.edit_message_text("⚙️ تایم‌فریم هنگام تحلیل انتخاب می‌شود. تنظیمات پیشرفته در نسخه بعدی قابل اضافه شدن است.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 منوی اصلی", callback_data="home")]])); return
    if data == "menu_help":
        await query.edit_message_text("❓ راهنما: تحلیل بازار ← انتخاب نماد ← انتخاب تایم‌فریم ← مشاهده نتیجه.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 منوی اصلی", callback_data="home")]]))

async def text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("برای اجرای وظایف از دکمه‌های منو استفاده کن. دستور /start منوی اصلی را باز می‌کند.", reply_markup=main_menu())

def run_web():
    web.run(host="0.0.0.0", port=PORT, use_reloader=False)

async def run_bot():
    if not BOT_TOKEN:
        raise RuntimeError("توکن تلگرام پیدا نشد. در Render متغیر BOT_TOKEN یا TELEGRAM_TOKEN را تنظیم کن.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_fallback))
    logger.info("Starting RFX Agent OS Telegram bot with asyncio event loop.")
    async with app:
        await app.start()
        if app.updater is None:
            raise RuntimeError("Telegram updater is not available.")
        await app.updater.start_polling(drop_pending_updates=True)
        try:
            await asyncio.Event().wait()
        finally:
            await app.updater.stop()
            await app.stop()

def main():
    threading.Thread(target=run_web, daemon=True).start()
    asyncio.run(run_bot())

if __name__ == "__main__":
    main()
