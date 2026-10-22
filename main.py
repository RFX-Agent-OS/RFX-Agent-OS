import os
import telebot
from openai import OpenAI

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = OpenAI(api_key=OPENAI_API_KEY)

@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(
        message,
        "سلام! من RFX Agent OS هستم 🤖\n"
        "دستیار هوشمند تحلیل بازار طلا.\n"
        "سؤالت را بپرس."
    )

@bot.message_handler(func=lambda message: True)
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
            "خطا در اتصال به هوش مصنوعی. لطفاً بعداً تلاش کن."
        )

bot.infinity_polling()
