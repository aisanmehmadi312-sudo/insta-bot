import os
import logging
import threading
import g4f
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# تنظیمات لاگ (برای دیدن وضعیت)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

# --- سرور الکی برای بیدار نگه داشتن Render ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_fake_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    print(f"🌍 Fake server running on port {port}")
    server.serve_forever()

threading.Thread(target=run_fake_server, daemon=True).start()
# ---------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! من با موتور ChatGPT (g4f) آماده‌ام. یه موضوع بگو! 🧠")

async def generate_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    wait_msg = await update.message.reply_text("⏳ دارم از ChatGPT می‌پرسم (ممکن است کمی طول بکشد)...")
    
    prompt = f"به عنوان ادمین حرفه‌ای اینستاگرام، برای موضوع '{user_text}' ۳ ایده ریلز، یک کپشن و ۱۰ هشتگ فارسی بنویس."
    
    # تلاش مجدد (Retry) تا 3 بار
    for attempt in range(3):
        try:
            # درخواست به g4f
            response = g4f.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
            )
            
            # اگه جواب اومد
            if response and response.strip():
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
                await update.message.reply_text(response)
                return # موفقیت! از تابع خارج شو
            else:
                # اگه جواب خالی بود، دوباره تلاش کن
                logger.warning(f"Attempt {attempt + 1}: Received empty response.")
                if attempt < 2: # اگه هنوز جا برای تلاش هست
                    await context.bot.edit_message_text(
                        chat_id=update.effective_chat.id,
                        message_id=wait_msg.message_id,
                        text=f"⏳ پاسخ خالی بود. تلاش مجدد ({attempt + 2}/3)..."
                    )
                    time.sleep(5) # 5 ثانیه صبر کن
                continue

        except Exception as e:
            logger.error(f"Attempt {attempt + 1} failed: {e}")
            if attempt < 2: # اگه هنوز جا برای تلاش هست
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id, 
                    message_id=wait_msg.message_id, 
                    text=f"⏳ سرور شلوغ است. تلاش مجدد ({attempt + 2}/3)..."
                )
                time.sleep(5) # 5 ثانیه صبر کن
            continue
            
    # اگه بعد از 3 بار تلاش هم موفق نشد
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=wait_msg.message_id,
        text="❌ سرورهای رایگان ChatGPT در حال حاضر بسیار شلوغ هستند. لطفاً چند دقیقه دیگر دوباره امتحان کنید."
    )

if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), generate_content))
    print("🤖 BOT STARTED WITH G4F (Robust Retry Version)...")
    application.run_polling()
                
