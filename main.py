import os
import logging
import threading
import google.generativeai as genai
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# تنظیمات لاگ
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

# --- سرور الکی برای بیدار نگه داشتن Render ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_fake_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler).serve_forever()

threading.Thread(target=run_fake_server, daemon=True).start()
# ---------------------------------------------

# --- تابع هوشمند برای پیدا کردن مدل فعال ---
def find_working_gemini_model():
    if not GOOGLE_API_KEY:
        logger.error("Google API Key not found.")
        return None, "کلید API گوگل تنظیم نشده است."
    try:
        genai.configure(api_key=GOOGLE_API_KEY)
        logger.info("🔍 Searching for available Gemini models...")
        # لیست تمام مدل‌ها رو بگیر
        for m in genai.list_models():
            # دنبال مدلی بگرد که قابلیت تولید محتوا داشته باشه
            if 'generateContent' in m.supported_generation_methods:
                logger.info(f"✅ Found a working model: {m.name}")
                # اولین مدلی که پیدا شد رو برگردون
                return genai.GenerativeModel(m.name), None
        
        # اگه هیچ مدلی پیدا نشد
        logger.error("❌ No models found that support 'generateContent'.")
        return None, "هیچ مدل فعالی برای تولید محتوا با این API Key پیدا نشد."

    except Exception as e:
        logger.error(f"Error while finding model: {e}")
        return None, f"خطا در اتصال به گوگل: {e}"

# در ابتدای برنامه، مدل فعال را پیدا کن
model, error_message = find_working_gemini_model()
# --------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if model:
        # اسم مدل پیدا شده رو به کاربر نشون بده
        await update.message.reply_text(f"سلام! من با مدل '{model.model_name}' آماده‌ام. یه موضوع بگو! ✨")
    else:
        await update.message.reply_text(f"❌ ربات نتوانست به گوگل وصل شود:\n{error_message}")

async def generate_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not model:
        await update.message.reply_text(f"❌ ربات به مدل گوگل وصل نیست:\n{error_message}")
        return

    user_text = update.message.text
    wait_msg = await update.message.reply_text("⏳ ...")

    try:
        prompt = f"به عنوان ادمین حرفه‌ای اینستاگرام، برای موضوع '{user_text}' ۳ ایده ریلز، یک کپشن و ۱۰ هشتگ فارسی بنویس."
        response = model.generate_content(prompt)
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
        await update.message.reply_text(response.text)

    except Exception as e:
        logger.error(f"Google Gemini Error: {e}")
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, 
            message_id=wait_msg.message_id, 
            text=f"❌ خطای Gemini: {e}"
        )

if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), generate_content))
    print("🤖 BOT STARTED (Diagnostic Mode)...")
    application.run_polling()
    
