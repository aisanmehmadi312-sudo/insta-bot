import os
import logging
import g4f
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# دریافت توکن تلگرام (دیگه گوگل لازم نیست)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! من دوباره زنده‌ام. یه موضوع بگو! 🧠")

async def generate_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    wait_msg = await update.message.reply_text("⏳ دارم از ChatGPT می‌پرسم...")

    try:
        # استفاده از g4f برای اتصال به مدل‌های رایگان
        prompt = f"به عنوان ادمین اینستاگرام، برای موضوع '{user_text}' ۳ ایده ریلز، یک کپشن و ۱۰ هشتگ فارسی بنویس."
        
        response = g4f.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
        )
        
        # اگه پاسخ اومد
        if response:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
            await update.message.reply_text(response)
        else:
            await update.message.reply_text("❌ پاسخ خالی بود.")

    except Exception as e:
        logger.error(f"Error: {e}")
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, 
            message_id=wait_msg.message_id, 
            text="❌ سرورهای رایگان شلوغ هستند. لطفاً دوباره تلاش کنید."
        )

if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), generate_content))
    
    print("🤖 BOT STARTED WITH G4F...")
    application.run_polling()
