import os
import time
import threading
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from flask import Flask, request
import telebot
from telebot import types
from groq import Groq

# =========================
# RFX Agent OS - Starter v1
# =========================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")
MODEL_NAME = os.environ.get("MODEL_NAME", "qwen/qwen3.8-27b")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise RuntimeError("Missing TELEGRAM_TOKEN or GROQ_API_KEY environment variable")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = Groq(api_key=GROQ_API_KEY)
app = Flask(__name__)

# In-memory only: this resets if Render restarts.
user_state = {}
chat_memory = {}
state_lock = threading.Lock()
MAX_HISTORY = 8

SYSTEM_PROMPT = """
تو دستیار فارسی‌زبان RFX Agent OS هستی.
تحلیل بازار باید مبتنی بر داده‌ای باشد که در پیام ورودی به تو داده شده است.
هرگز قیمت زنده، خبر، اندیکاتور یا نتیجه‌ای را که داده‌اش موجود نیست جعل نکن.
تحلیل را احتمالی بیان کن، ریسک را یادآوری کن و در نبود داده کافی صریح بگو.
این نسخه فقط تحلیل و آموزش ارائه می‌کند و هیچ معامله‌ای اجرا نمی‌کند.
"""

BINANCE_BASE = "https://api.binance.com"
YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/XAUUSD=X"
NEWS_RSS = "https://news.google.com/rss/search?q=gold+OR+Federal+Reserve+OR+inflation+OR+Bitcoin+when:1d&hl=en-US&gl=US&ceid=US:en"

SYMBOLS = [
    ("BTCUSDT", "₿ بیت‌کوین"),
    ("ETHUSDT", "🔷 اتریوم"),
    ("SOLUSDT", "🟣 سولانا"),
    ("BNBUSDT", "🟡 BNB"),
    ("XRPUSDT", "✖️ XRP"),
    ("DOGEUSDT", "🐕 Dogecoin"),
    ("ADAUSDT", "🔵 Cardano"),
    ("TRXUSDT", "🔴 TRON"),
    ("LINKUSDT", "🔗 Chainlink"),
    ("AVAXUSDT", "🔺 Avalanche"),
    ("LTCUSDT", "Ł Litecoin"),
    ("BCHUSDT", "Bitcoin Cash"),
    ("DOTUSDT", "Polkadot"),
    ("TONUSDT", "Toncoin"),
    ("SUIUSDT", "Sui"),
    ("SHIBUSDT", "Shiba Inu"),
    ("PEPEUSDT", "Pepe"),
]
TIMEFRAMES = [("1m", "1 دقیقه"), ("5m", "5 دقیقه"), ("15m", "15 دقیقه"),
              ("1h", "۱ ساعت"), ("4h", "۴ ساعت"), ("1d", "روزانه")]

def main_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🥇 تحلیل طلای جهانی", callback_data="gold"),
        types.InlineKeyboardButton("₿ تحلیل کریپتو", callback_data="crypto"),
    )
    kb.add(
        types.InlineKeyboardButton("📰 اخبار بازار", callback_data="news"),
        types.InlineKeyboardButton("📊 اندیکاتورها", callback_data="indicators"),
    )
    kb.add(
        types.InlineKeyboardButton("💧 نقدینگی و ساختار", callback_data="structure"),
        types.InlineKeyboardButton("⚡ اندیکاتور اختصاصی RFX", callback_data="rfx"),
    )
    kb.add(
        types.InlineKeyboardButton("💰 مدیریت ریسک", callback_data="risk"),
        types.InlineKeyboardButton("🔄 شروع دوباره", callback_data="reset"),
    )
    return kb

def back_menu():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="home"))
    return kb

def timeframe_menu(asset, symbol=None):
    kb = types.InlineKeyboardMarkup(row_width=3)
    for tf, label in TIMEFRAMES:
        data = f"analyze|{asset}|{symbol or 'XAUUSD'}|{tf}"
        kb.add(types.InlineKeyboardButton(label, callback_data=data))
    kb.add(types.InlineKeyboardButton("🔙 برگشت", callback_data="gold" if asset == "gold" else "crypto"))
    return kb

def crypto_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    for sym, label in SYMBOLS:
        kb.add(types.InlineKeyboardButton(label, callback_data=f"choosecoin|{sym}"))
    kb.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="home"))
    return kb

def gold_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🔍 تحلیل کامل", callback_data="goldfull"),
        types.InlineKeyboardButton("📈 جهت روند", callback_data="goldtrend"),
    )
    kb.add(
        types.InlineKeyboardButton("🧱 حمایت/مقاومت", callback_data="goldlevels"),
        types.InlineKeyboardButton("💧 نقدینگی و ساختار", callback_data="goldstructure"),
    )
    kb.add(types.InlineKeyboardButton("⏱ انتخاب تایم‌فریم", callback_data="gold_tf"))
    kb.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="home"))
    return kb

def indicators_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("RSI + MACD", callback_data="ind_rsi_macd"),
        types.InlineKeyboardButton("EMA + Supertrend*", callback_data="ind_ema"),
    )
    kb.add(
        types.InlineKeyboardButton("ATR و نوسان", callback_data="ind_atr"),
        types.InlineKeyboardButton("BOS / CHoCH*", callback_data="structure"),
    )
    kb.add(types.InlineKeyboardButton("🏠 منوی اصلی", callback_data="home"))
    return kb

def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def ema(values, period):
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    result = sum(values[:period]) / period
    for v in values[period:]:
        result = alpha * v + (1 - alpha) * result
    return result

def calc_rsi(closes, period=14):
    if len(closes) <= period:
        return None
    gains, losses = [], []
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0)) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_atr(highs, lows, closes, period=14):
    if len(closes) <= period:
        return None
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])))
    return sum(trs[-period:]) / min(period, len(trs))

def calculate_indicators(candles):
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    if len(closes) < 30:
        return {"error": "داده کندل کافی نیست."}
    e20, e50 = ema(closes, 20), ema(closes, 50)
    rsi = calc_rsi(closes, 14)
    macd_fast, macd_slow = ema(closes, 12), ema(closes, 26)
    macd_line = (macd_fast - macd_slow) if macd_fast is not None and macd_slow is not None else None
    atr = calc_atr(highs, lows, closes, 14)
    lookback = candles[-20:]
    support = min(c["low"] for c in lookback)
    resistance = max(c["high"] for c in lookback)
    trend = "نامشخص"
    if e20 is not None and e50 is not None:
        if closes[-1] > e20 > e50:
            trend = "تمایل صعودی بر اساس قیمت و EMA20/EMA50"
        elif closes[-1] < e20 < e50:
            trend = "تمایل نزولی بر اساس قیمت و EMA20/EMA50"
        else:
            trend = "ترکیبی/خنثی؛ تأیید روند کامل نیست"
    return {
        "last": closes[-1], "ema20": e20, "ema50": e50, "rsi14": rsi,
        "macd_line": macd_line, "atr14": atr, "support20": support,
        "resistance20": resistance, "trend": trend, "candle_time": candles[-1].get("time")
    }

def fetch_crypto_candles(symbol, interval, limit=120):
    url = f"{BINANCE_BASE}/api/v3/klines"
    r = requests.get(url, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=12)
    r.raise_for_status()
    raw = r.json()
    candles = []
    for row in raw:
        candles.append({
            "time": datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "open": float(row[1]), "high": float(row[2]), "low": float(row[3]),
            "close": float(row[4]), "volume": float(row[5])
        })
    return candles

def fetch_gold_candles(interval):
    # Yahoo Finance is a free, unofficial endpoint; availability and intervals may change.
    interval_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "60m", "4h": "60m", "1d": "1d"}
    yahoo_interval = interval_map.get(interval, "5m")
    range_map = {"1m": "1d", "5m": "5d", "15m": "5d", "1h": "1mo", "4h": "3mo", "1d": "1y"}
    r = requests.get(
        YAHOO_BASE,
        params={"range": range_map.get(interval, "5d"), "interval": yahoo_interval},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=12
    )
    r.raise_for_status()
    obj = r.json()["chart"]["result"][0]
    timestamps = obj.get("timestamp") or []
    quote = obj["indicators"]["quote"][0]
    candles = []
    for i, ts in enumerate(timestamps):
        vals = [quote.get(k, [None] * len(timestamps))[i] for k in ("open", "high", "low", "close")]
        if any(v is None for v in vals):
            continue
        candles.append({
            "time": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "open": float(vals[0]), "high": float(vals[1]), "low": float(vals[2]),
            "close": float(vals[3]), "volume": 0
        })
    if not candles:
        raise ValueError("منبع فعلاً کندل طلا برنگرداند.")
    return candles

def groq_answer(prompt, user_id=None):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if user_id is not None:
        with state_lock:
            messages.extend(chat_memory.get(user_id, [])[-MAX_HISTORY:])
    messages.append({"role": "user", "content": prompt})
    response = client.chat.completions.create(
        model=MODEL_NAME, messages=messages, temperature=0.3, max_tokens=1200
    )
    answer = response.choices[0].message.content or "پاسخی دریافت نشد."
    if user_id is not None:
        with state_lock:
            hist = chat_memory.setdefault(user_id, [])
            hist.extend([{"role": "user", "content": prompt}, {"role": "assistant", "content": answer}])
            chat_memory[user_id] = hist[-MAX_HISTORY:]
    return answer

def split_send(chat_id, text, reply_markup=None):
    text = str(text)
    if len(text) <= 4000:
        bot.send_message(chat_id, text, reply_markup=reply_markup)
        return
    chunks = [text[i:i+3900] for i in range(0, len(text), 3900)]
    for i, chunk in enumerate(chunks):
        bot.send_message(chat_id, chunk, reply_markup=reply_markup if i == len(chunks)-1 else None)

def format_analysis(asset, symbol, tf, candles):
    stats = calculate_indicators(candles)
    if "error" in stats:
        return stats["error"]
    name = "طلای جهانی XAUUSD" if asset == "gold" else symbol
    data = (
        f"نماد: {name}\nتایم‌فریم درخواستی: {tf}\n"
        f"آخرین کندل داده‌شده: {stats['candle_time']}\n"
        f"آخرین قیمت کندل: {stats['last']:.5f}\n"
        f"روند الگوریتمی اولیه: {stats['trend']}\n"
        f"EMA20: {stats['ema20']:.5f}\nEMA50: {stats['ema50']:.5f}\n"
        f"RSI(14): {stats['rsi14']:.2f}\n"
        f"MACD line (12,26): {stats['macd_line']:.5f}\n"
        f"ATR(14): {stats['atr14']:.5f}\n"
        f"حمایت محلی (کمترین کف ۲۰ کندل): {stats['support20']:.5f}\n"
        f"مقاومت محلی (بیشترین سقف ۲۰ کندل): {stats['resistance20']:.5f}\n"
        f"تعداد کندل‌های استفاده‌شده: {len(candles)}\n"
        "توجه: داده‌ها از منبع عمومی دریافت شده‌اند؛ قیمت ممکن است با بروکر شما تفاوت داشته باشد."
    )
    prompt = (
        "بر اساس فقط داده‌های عددی زیر، گزارش تحلیلی فارسی تهیه کن. "
        "ساختار گزارش: وضعیت روند، مومنتوم، حمایت/مقاومت، سناریوی صعودی، سناریوی نزولی، "
        "شرایطی که تحلیل را باطل می‌کند و ریسک‌ها. اگر ورود قابل اتکا نیست، صریحاً بگو ورود نکن. "
        "سطوح یا قیمت‌هایی که در داده نیستند را اختراع نکن. این اعداد اندیکاتورها هستند و "
        "BOS/CHoCH، نقدینگی یا ICT در این نسخه هنوز به‌طور کامل محاسبه نشده‌اند.\n\n" + data
    )
    return "📊 داده‌های محاسبه‌شده:\n" + data + "\n\n🧠 تفسیر تحلیلی:\n" + groq_answer(prompt)

def fetch_news():
    r = requests.get(NEWS_RSS, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for item in root.findall(".//item")[:8]:
        title = item.findtext("title", default="بدون عنوان")
        link = item.findtext("link", default="")
        pubdate = item.findtext("pubDate", default="زمان نامشخص")
        source = item.find("source")
        source_name = source.text if source is not None and source.text else "منبع خبر"
        items.append((title, pubdate, source_name, link))
    return items

def send_news(chat_id):
    items = fetch_news()
    if not items:
        bot.send_message(chat_id, "فعلاً خبری از منبع دریافت نشد.")
        return
    lines = ["📰 اخبار مالی اخیر (از Google News RSS)\nزمان‌ها طبق فید منبع هستند؛ اهمیت خبر به‌صورت خودکار و قطعی تضمین نمی‌شود.\n"]
    for i, (title, pubdate, source, link) in enumerate(items, 1):
        lines.append(f"{i}. {title}\nمنبع: {source}\nزمان: {pubdate}\n{link}\n")
    lines.append("برای تحلیل اثر خبر، عنوان خبر را برای ربات بفرست. خبرها ممکن است تکراری یا ناقص باشند.")
    split_send(chat_id, "\n".join(lines), reply_markup=back_menu())

@bot.message_handler(commands=["start", "menu"])
def start(message):
    bot.send_message(
        message.chat.id,
        "🤖 به RFX Agent OS خوش آمدی.\n\n"
        "این نسخه آزمایشی است: تحلیل تکنیکال اولیه طلا و کریپتو و فید اخبار را دارد. "
        "اندیکاتور اختصاصی RFX هنوز پس از دریافت کد تو اضافه می‌شود.\n\nبازار را انتخاب کن:",
        reply_markup=main_menu()
    )

@bot.message_handler(commands=["reset"])
def reset(message):
    with state_lock:
        chat_memory.pop(message.from_user.id, None)
    bot.reply_to(message, "حافظه گفت‌وگوی همین نشست پاک شد.", reply_markup=main_menu())

@bot.callback_query_handler(func=lambda call: True)
def callback_router(call):
    try:
        data = call.data or ""
        bot.answer_callback_query(call.id)
        chat_id = call.message.chat.id
        uid = call.from_user.id

        if data in ("home", "reset"):
            if data == "reset":
                with state_lock:
                    chat_memory.pop(uid, None)
            bot.send_message(chat_id, "منوی اصلی RFX Agent OS:", reply_markup=main_menu())
        elif data == "gold":
            bot.send_message(chat_id, "🥇 تحلیل طلای جهانی XAUUSD\nنوع تحلیل را انتخاب کن:", reply_markup=gold_menu())
        elif data == "crypto":
            bot.send_message(chat_id, "₿ نماد ارز دیجیتال را انتخاب کن:", reply_markup=crypto_menu())
        elif data.startswith("choosecoin|"):
            symbol = data.split("|", 1)[1]
            bot.send_message(chat_id, f"نماد انتخاب‌شده: {symbol}\nتایم‌فریم را انتخاب کن:", reply_markup=timeframe_menu("crypto", symbol))
        elif data == "gold_tf":
            bot.send_message(chat_id, "تایم‌فریم تحلیل طلا را انتخاب کن:", reply_markup=timeframe_menu("gold", "XAUUSD"))
        elif data.startswith("analyze|"):
            _, asset, symbol, tf = data.split("|")
            bot.send_message(chat_id, "⏳ در حال دریافت داده و محاسبه اندیکاتورها... لطفاً صبر کن.")
            try:
                candles = fetch_gold_candles(tf) if asset == "gold" else fetch_crypto_candles(symbol, tf)
                result = format_analysis(asset, symbol, tf, candles)
                split_send(chat_id, result, reply_markup=back_menu())
            except Exception as e:
                print("DATA ERROR:", repr(e))
                bot.send_message(chat_id, "نتوانستم داده بازار را دریافت کنم. ممکن است منبع رایگان موقتاً در دسترس نباشد یا نماد/تایم‌فریم پشتیبانی نشود. کمی بعد دوباره تلاش کن.", reply_markup=back_menu())
        elif data == "goldfull":
            bot.send_message(chat_id, "برای تحلیل کامل، تایم‌فریم را انتخاب کن:", reply_markup=timeframe_menu("gold", "XAUUSD"))
        elif data in ("goldtrend", "goldlevels", "goldstructure"):
            bot.send_message(chat_id, "تایم‌فریم را انتخاب کن تا داده‌های واقعی بررسی شوند:", reply_markup=timeframe_menu("gold", "XAUUSD"))
        elif data == "news":
            bot.send_message(chat_id, "⏳ در حال دریافت فید خبرهای مالی اخیر...")
            try:
                send_news(chat_id)
            except Exception as e:
                print("NEWS ERROR:", repr(e))
                bot.send_message(chat_id, "فعلاً دریافت اخبار ممکن نشد. فید رایگان ممکن است موقتاً قطع یا محدود شده باشد.", reply_markup=back_menu())
        elif data == "indicators":
            bot.send_message(chat_id, "📊 ابزارهای موجود در نسخه آزمایشی:", reply_markup=indicators_menu())
        elif data in ("ind_rsi_macd", "ind_ema", "ind_atr", "structure"):
            bot.send_message(chat_id, "این گزینه فعلاً از همان تحلیل داده‌محور و اندیکاتورهای پایه استفاده می‌کند. برای تحلیل دقیق‌تر نماد و تایم‌فریم را از منوی طلا یا کریپتو انتخاب کن.", reply_markup=back_menu())
        elif data == "rfx":
            bot.send_message(chat_id, "⚡ بخش اندیکاتور اختصاصی RFX آماده اتصال است. کد Pine Script یا تصاویر واضح از کد و قوانین سیگنال را بفرست تا منطق واقعی آن را پیاده‌سازی کنیم.", reply_markup=back_menu())
        elif data == "risk":
            bot.send_message(chat_id, "💰 برای محاسبه حجم معامله، سرمایه، درصد ریسک، نماد، قیمت ورود و حد ضرر را در یک پیام بنویس. محاسبه دقیق XAUUSD به مشخصات قرارداد بروکر نیاز دارد.", reply_markup=back_menu())
        else:
            bot.send_message(chat_id, "این گزینه هنوز فعال نشده است.", reply_markup=main_menu())
    except Exception as e:
        print("CALLBACK ERROR:", repr(e))

@bot.message_handler(func=lambda message: bool(message.text))
def chat(message):
    text = message.text.strip()
    if text.startswith("/"):
        return
    try:
        answer = groq_answer(text, message.from_user.id)
        split_send(message.chat.id, answer)
    except Exception as e:
        print("GROQ ERROR:", repr(e))
        bot.reply_to(message, "خطا در اتصال به هوش مصنوعی رخ داد. لطفاً کمی بعد دوباره تلاش کن.")

@app.route("/")
def home():
    return "RFX Agent OS is running!", 200

@app.route("/" + TELEGRAM_TOKEN, methods=["POST"])
def telegram_webhook():
    update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
    bot.process_new_updates([update])
    return "OK", 200

if RENDER_URL:
    bot.remove_webhook()
    bot.set_webhook(url=RENDER_URL + "/" + TELEGRAM_TOKEN)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
