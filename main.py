import os
from flask import Flask, request
import telebot
from openai import OpenAI

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")

if not TELEGRAM_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("Missing environment variables")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = OpenAI(api_key=OPENAI_API_KEY)
app = Flask(__name__)

@app.route("/")
def home():
    return "RFX Agent OS is running!", 200

@app.route("/" + TELEGRAM_TOKEN, methods=["POST"])
def telegram_webhook():
    update = telebot.types.Update.de_json(
        request.get_data().decode("utf-8")
    )
    bot.process_new_updates([update])
    return "OK", 200

@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(
        message,
        "سلام! 🤖\n"
        "من RFX Agent OS هستم.\n"
        "دستیار هوشمند تحلیل بازار."
    )

@bot.message_handler(func=lambda message: bool(message.text))
def chat(message):
    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=message.text
        )
        bot.reply_to(message, response.output_text)
    except Exception:
        bot.reply_to(
            message,
            "در اتصال به هوش مصنوعی خطایی رخ داد. "
            "لطفاً بعداً دوباره تلاش کن."
        )

if RENDER_URL:
    bot.remove_webhook()
    bot.set_webhook(url=RENDER_URL + "/" + TELEGRAM_TOKEN)

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
