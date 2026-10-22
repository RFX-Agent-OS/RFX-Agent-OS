import os
import threading
from flask import Flask, request
import telebot
from groq import Groq

# =========================
# CONFIGURATION
# =========================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")

MODEL_NAME = "qwen/qwen3.8-27b"

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise RuntimeError(
        "Missing TELEGRAM_TOKEN or GROQ_API_KEY"
    )

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = Groq(api_key=GROQ_API_KEY)
app = Flask(__name__)

# =========================
# CONVERSATION MEMORY
# =========================

conversation_memory = {}
memory_lock = threading.Lock()

MAX_HISTORY_MESSAGES = 12

SYSTEM_PROMPT = """
You are RFX Agent OS, an intelligent trading assistant.

LANGUAGE:
- Respond in fluent, natural Persian by default.
- Be clear, practical, and professional.

CONVERSATION:
- Use the recent conversation history to understand follow-up questions.
- Do not claim to remember information that is not in the provided history.
- If the user sends /reset, the bot will clear that chat's history.

TRADING ANALYSIS:
- Help analyze gold, forex, Bitcoin, and other cryptocurrencies.
- Explain market structure, trends, support, resistance, and risk/reward.
- When chart data or screenshots are provided, analyze only what is visible.
- Never invent live prices, indicators, news, or market data.
- If live data is unavailable, ask the user to provide a current price or chart.
- Distinguish facts from assumptions and scenarios.

RISK MANAGEMENT:
- Explain stop loss, take profit, position sizing, and risk/reward.
- Ask for account balance and risk percentage when needed for position sizing.
- Explain the risks of leveraged trading.
- Never guarantee profits or describe a trade as risk-free.

IDENTITY:
- You are RFX Agent OS.
- Do not claim to have executed trades or accessed accounts unless
  an actual connected tool confirms that action.
"""

# =========================
# HOME
# =========================

@app.route("/")
def home():
    return "RFX Agent OS is running!", 200

# =========================
# TELEGRAM WEBHOOK
# =========================

@app.route("/" + TELEGRAM_TOKEN, methods=["POST"])
def telegram_webhook():
    update = telebot.types.Update.de_json(
        request.get_data().decode("utf-8")
    )
    bot.process_new_updates([update])
    return "OK", 200

# =========================
# START COMMAND
# =========================

@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(
        message,
        "سلام! 🤖\n"
        "من RFX Agent OS هستم.\n\n"
        "دستیار هوشمند تحلیل بازار و مدیریت ریسک.\n"
        "می‌توانم گفت‌وگوی اخیر همین چت را دنبال کنم.\n\n"
        "برای پاک‌کردن حافظه مکالمه، دستور /reset را بفرست."
    )

# =========================
# RESET MEMORY
# =========================

@bot.message_handler(commands=["reset"])
def reset_memory(message):
    chat_id = message.chat.id

    with memory_lock:
        conversation_memory.pop(chat_id, None)

    bot.reply_to(
        message,
        "🧠 حافظه مکالمه این چت پاک شد.\n"
        "می‌توانیم از یک گفت‌وگوی تازه شروع کنیم."
    )

# =========================
# AI CHAT
# =========================

@bot.message_handler(
    content_types=["text"],
    func=lambda message: bool(message.text)
)
def chat(message):
    chat_id = message.chat.id
    user_text = message.text.strip()

    if not user_text:
        return

    try:
        with memory_lock:
            history = conversation_memory.get(
                chat_id, []
            ).copy()

        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            }
        ]

        messages.extend(history)

        messages.append({
            "role": "user",
            "content": user_text
        })

        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=0.4,
            max_tokens=1500
        )

        answer = response.choices[0].message.content

        if not answer:
            answer = "پاسخی دریافت نشد. لطفاً دوباره تلاش کن."

        # Save the new exchange in conversation memory.
        with memory_lock:
            updated_history = history + [
                {
                    "role": "user",
                    "content": user_text
                },
                {
                    "role": "assistant",
                    "content": answer
                }
            ]

            conversation_memory[chat_id] = (
                updated_history[-MAX_HISTORY_MESSAGES:]
            )

        # Telegram messages have a length limit.
        for i in range(0, len(answer), 4000):
            bot.reply_to(message, answer[i:i + 4000])

    except Exception as e:
        print("AI ERROR:", repr(e))

        bot.reply_to(
            message,
            "خطایی هنگام دریافت پاسخ رخ داد.\n"
            "لطفاً Logs سرویس را بررسی کن."
        )

# =========================
# REGISTER WEBHOOK
# =========================

if RENDER_URL:
    bot.remove_webhook()
    bot.set_webhook(
        url=RENDER_URL + "/" + TELEGRAM_TOKEN
    )

# =========================
# RUN SERVER
# =========================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
