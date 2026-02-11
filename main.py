import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
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

# --- سرور الکی برای بیدار نگه داشتن Render ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is active!")

def run_fake_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    print(f"🌍 Fake server running on port {port}")
    server.serve_forever()

threading.Thread(target=run_fake_server, daemon=True).start()
# ---------------------------------------------

# تنظیم گوگل (با مدل فلش که سریع و پایدارتره)
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    # مدل Flash معمولاً برای همه بازه
    model = genai.GenerativeModel('gemini-1.5-flash')
except Exception as e:
    logger.error(f"Config Error: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! من با موتور Gemini Flash آماده‌ام. یه موضوع بگو! ⚡️")

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
        # اگه مدل فلش هم کار نکرد، مدل قدیمی رو تست کن
        try:
            fallback = genai.GenerativeModel('gemini-pro')
            response = fallback.generate_content(prompt)
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
            await update.message.reply_text(response.text)
        except Exception as e2:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id, 
                message_id=wait_msg.message_id, 
                text=f"❌ خطای گوگل: {e}\n(لطفاً مطمئن شو که API Key درست وارد شده و منقضی نشده)"
            )

if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), generate_content))
    
    print("🤖 BOT STARTED WITH GEMINI FLASH...")
    application.run_polling()
        
