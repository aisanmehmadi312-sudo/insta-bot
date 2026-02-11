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

# تنظیم گوگل (با قابلیت پیدا کردن خودکار مدل)
genai.configure(api_key=GOOGLE_API_KEY)

# تابع هوشمند برای انتخاب مدل
def get_best_model():
    try:
        # لیست مدل‌های موجود رو بگیر
        models = genai.list_models()
        for m in models:
            # دنبال مدلی بگرد که قابلیت تولید محتوا داشته باشه و اسمش gemini باشه
            if 'generateContent' in m.supported_generation_methods and 'gemini' in m.name:
                logger.info(f"✅ Found working model: {m.name}")
                return genai.GenerativeModel(m.name)
        
        # اگه پیدا نکرد، دیفالت رو بذار gemini-pro (قدیمی ولی شاید کار کنه)
        logger.warning("⚠️ No specific Gemini model found, trying default.")
        return genai.GenerativeModel('gemini-pro')
    except Exception as e:
        logger.error(f"❌ Error listing models: {e}")
        return genai.GenerativeModel('gemini-pro')

# مدل رو انتخاب کن
model = get_best_model()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! من با بهترین مدل موجود وصل شدم. یه موضوع بگو! 🚀")

async def generate_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    wait_msg = await update.message.reply_text("⏳ ...")

    try:
        prompt = f"به عنوان ادمین حرفه‌ای اینستاگرام، برای موضوع '{user_text}' ۳ ایده ریلز، یک کپشن و ۱۰ هشتگ فارسی بنویس."
        response = model.generate_content(prompt)
        
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
        await update.message.reply_text(response.text)

    except Exception as e:
        logger.error(f"Google Error: {e}")
        # اگه باز هم ارور داد، تلاش مجدد با مدل جایگزین
        try:
            fallback_model = genai.GenerativeModel('gemini-1.0-pro')
            response = fallback_model.generate_content(prompt)
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
            await update.message.reply_text(response.text)
        except Exception as e2:
             await context.bot.edit_message_text(
                chat_id=update.effective_chat.id, 
                message_id=wait_msg.message_id, 
                text=f"❌ خطای نهایی: {e}\n(مطمئن شو که API Key گوگل اعتبار داره و منقضی نشده)"
            )

if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), generate_content))
    print("🤖 BOT STARTED (Auto-Model Selection)...")
    application.run_polling()
    
