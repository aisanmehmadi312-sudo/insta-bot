import os
import logging
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

# تنظیم گوگل
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-pro')
except Exception as e:
    logger.error(f"Config Error: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! من آماده‌ام. (نسخه سریع گوگل ⚡️)")

async def generate_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    # پیام انتظار
    wait_msg = await update.message.reply_text("⏳ در حال فکر کردن...")

    try:
        # پرامپت
        prompt = f"به عنوان ادمین اینستاگرام، برای موضوع '{user_text}' ۳ ایده ریلز، یک کپشن و ۱۰ هشتگ فارسی بنویس."
        
        # درخواست به گوگل
        response = model.generate_content(prompt)
        
        # حذف پیام انتظار
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
        
        # ارسال جواب
        await update.message.reply_text(response.text)

    except Exception as e:
        logger.error(f"Google Error: {e}")
        # اگه ارور داد، دقیق بگه چیه
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, 
            message_id=wait_msg.message_id, 
            text=f"❌ خطای گوگل: {e}"
        )

if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), generate_content))
    print("🤖 BOT STARTED WITH GOOGLE API...")
    application.run_polling()
                                                     
