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

# اتصال به Google Gemini
client = None
model = None
if GOOGLE_API_KEY:
    try:
        genai.configure(api_key=GOOGLE_API_KEY)
        # اول مدل جدید و سریع Flash رو امتحان کن
        model = genai.GenerativeModel('gemini-1.5-flash')
    except Exception as e:
        logger.error(f"Google Flash Model Config Error: {e}")
        # اگه نشد، مدل قدیمی‌تر Pro رو امتحان کن
        try:
            model = genai.GenerativeModel('gemini-pro')
        except Exception as e2:
            logger.error(f"Google Pro Model Config Error: {e2}")
else:
    logger.error("❌ Google API Key not found!")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not model:
        await update.message.reply_text("❌ کلید Google API تنظیم نشده یا اشتباه است!")
    else:
        await update.message.reply_text("سلام! من با موتور Gemini آماده‌ام. یه موضوع بگو! ✨")

async def generate_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not model:
        await update.message.reply_text("❌ کلید Google API تنظیم نشده است!")
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
            text=f"❌ خطای Gemini: {e}\n(مطمئن شو API Key درسته و اعتبار داره)"
        )

if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), generate_content))
    print("🤖 BOT STARTED WITH GOOGLE GEMINI...")
    application.run_polling()
    
