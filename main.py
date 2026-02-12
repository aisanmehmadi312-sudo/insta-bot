import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# تنظیمات لاگ
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
XAI_API_KEY = os.environ.get("XAI_API_KEY")

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

# اتصال به xAI (Grok) با استفاده از کتابخانه OpenAI
# نکته: Grok با پروتکل OpenAI سازگار است
client = None
if XAI_API_KEY:
    try:
        client = OpenAI(
            api_key=XAI_API_KEY,
            base_url="https://api.x.ai/v1", # آدرس سرور Grok
        )
    except Exception as e:
        logger.error(f"xAI Config Error: {e}")
else:
    logger.error("❌ XAI_API_KEY not found!")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! من با موتور Grok (xAI) آماده‌ام. یه موضوع بگو! 🌌")

async def generate_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not client:
        await update.message.reply_text("❌ کلید Grok تنظیم نشده است!")
        return

    user_text = update.message.text
    wait_msg = await update.message.reply_text("⏳ دارم از Grok می‌پرسم...")

    try:
        prompt = f"به عنوان ادمین حرفه‌ای اینستاگرام، برای موضوع '{user_text}' ۳ ایده ریلز، یک کپشن و ۱۰ هشتگ فارسی بنویس."
        
        response = client.chat.completions.create(
            model="grok-beta", # مدل رایگان و قوی Grok
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt},
            ],
        )
        
        ai_reply = response.choices[0].message.content
        
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
        await update.message.reply_text(ai_reply)

    except Exception as e:
        logger.error(f"Grok Error: {e}")
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, 
            message_id=wait_msg.message_id, 
            text=f"❌ خطای Grok: {e}"
        )

if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), generate_content))
    print("🤖 BOT STARTED WITH GROK API...")
    application.run_polling()
