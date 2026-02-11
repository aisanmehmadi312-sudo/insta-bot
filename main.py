import os
import logging
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# تنظیمات لاگ
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
HF_TOKEN = os.environ.get("HF_TOKEN")

# مدل قدرتمند و رایگان Mistral (جایگزین عالی برای GPT)
API_URL = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.2"
headers = {"Authorization": f"Bearer {HF_TOKEN}"}

# --- سرور الکی برای بیدار ماندن ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_fake_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler).serve_forever()

threading.Thread(target=run_fake_server, daemon=True).start()
# ----------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! من با موتور Mistral آماده‌ام. یه موضوع بگو! 🌪️")

async def generate_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    wait_msg = await update.message.reply_text("⏳ ...")

    try:
        # پرامپت انگلیسی (چون این مدل با انگلیسی بهتر کار می‌کنه، ولی خروجی فارسی می‌گیریم)
        prompt = f"<s>[INST] You are an expert Instagram admin. Write 3 Reels ideas, 1 caption, and 10 hashtags in PERSIAN (Farsi) for this topic: '{user_text}'. Keep it professional and engaging. [/INST]"
        
        response = requests.post(API_URL, headers=headers, json={"inputs": prompt, "parameters": {"max_new_tokens": 1000}})
        result = response.json()
        
        if 'error' in result:
            raise Exception(result['error'])
            
        final_text = result[0]['generated_text'].replace(prompt, "").strip()
        
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
        await update.message.reply_text(final_text)

    except Exception as e:
        logger.error(f"HF Error: {e}")
        # اگه مدل خواب بود، صبر کن و دوباره امتحان کن
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, 
            message_id=wait_msg.message_id, 
            text="⚠️ مدل در حال بیدار شدن است (Cold Boot). لطفاً ۳۰ ثانیه دیگر دوباره امتحان کنید."
        )

if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), generate_content))
    print("🤖 BOT STARTED WITH HUGGING FACE...")
    application.run_polling()
    
