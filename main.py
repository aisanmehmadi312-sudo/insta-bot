import os
import logging
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# تنظیمات لاگ دقیق‌تر
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)

# دریافت توکن‌ها
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

# تنظیم جمینای
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-pro')
except Exception as e:
    logger.error(f"❌ Gemini Error: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Command /start received!")  # اینو تو لاگ چاپ می‌کنه
    await update.message.reply_text("سلام! من بیدارم. یه چیزی بگو!")

async def generate_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    logger.info(f"Message received: {user_text}") # متن پیام رو چاپ می‌کنه

    try:
        # تست ساده (اول ببینیم اصلا جمینای کار می‌کنه یا نه)
        response = model.generate_content(f"خلاصه بگو: {user_text}")
        logger.info("Gemini replied successfully")
        await update.message.reply_text(response.text)
    except Exception as e:
        logger.error(f"❌ Error generating content: {e}")
        await update.message.reply_text("❌ خطا در اتصال به هوش مصنوعی.")

if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), generate_content))
    
    print("🤖 BOT STARTED AND READY...")
    application.run_polling()
        
