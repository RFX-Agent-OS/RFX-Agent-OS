import os
from flask import Flask, request
import telebot
from groq import Groq

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise RuntimeError(
        "Missing environment variables: "
        "TELEGRAM_TOKEN or GROQ_API_KEY"
    )

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = Groq(api_key=GROQ_API_KEY)
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
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "تو دستیار هوشمند RFX Agent OS هستی. "
                        "به فارسی روان، دقیق و مفید پاسخ بده. "
                        "در تحلیل بازار، ریسک‌ها را نیز توضیح بده."
                    )
                },
                {
                    "role": "user",
                    "content": message.text
                }
            ]
        )

        answer = response.choices[0].message.content
        bot.reply_to(message, answer or "پاسخی دریافت نشد.")

    except Exception as e:
        print("GROQ ERROR:", repr(e))
        bot.reply_to(
            message,
            "خطا در اتصال به هوش مصنوعی رخ داد. "
            "لطفاً کمی بعد دوباره تلاش کن."
        )


if RENDER_URL:
    bot.remove_webhook()
    bot.set_webhook(url=RENDER_URL + "/" + TELEGRAM_TOKEN)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
