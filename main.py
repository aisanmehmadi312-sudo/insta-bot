import os
import logging
import threading
import requests
import json  # برای بررسی پاسخ JSON
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# تنظیمات لاگ (برای دیدن وضعیت ربات)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
HF_TOKEN = os.environ.get("HF_TOKEN")

# --- تنظیمات Hugging Face (مدل Mistral-7B) ---
# لیست مدل‌های Mistral که معمولاً پایدارن
MISTRAL_MODELS = [
    "mistralai/Mistral-7B-Instruct-v0.2",
    "mistralai/Mistral-7B-v0.1",
    "TheBloke/Mistral-7B-Instruct-v0.2-GGUF" # یک مدل محبوب دیگر
]
HF_API_BASE_URL = "https://api-inference.huggingface.co/models/" # آدرس پایه API
# ( Router.huggingface.co برای بعضی مدل‌ها هنوز خوب کار نمیکنه)

# --- سرور الکی برای بیدار نگه داشتن Render ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive and well! (Serving dummy page)")

def run_fake_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    print(f"🌍 Fake server running on port {port}")
    server.serve_forever()

threading.Thread(target=run_fake_server, daemon=True).start()
# ---------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not HF_TOKEN:
        await update.message.reply_text("❌ خطا: توکن Hugging Face (HF_TOKEN) تنظیم نشده است. لطفاً آن را در Render Environment Variables وارد کنید.")
    else:
        await update.message.reply_text("سلام! من با موتور Mistral آماده‌ام. یه موضوع بگو! 🌪️")

async def query_huggingface(payload, model_name):
    # این تابع به Hugging Face وصل میشه
    API_URL = f"{HF_API_BASE_URL}{model_name}"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    
    # 3 بار تلاش کن (برای Cold Boot)
    for i in range(3):
        try:
            response = requests.post(API_URL, headers=headers, json=payload, timeout=90) # افزایش زمان انتظار
            response.raise_for_status() # اگه کد وضعیت HTTP بد بود (مثل 400 یا 500) ارور بده
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.warning(f"Attempt {i+1} failed for model {model_name}: {e}")
            if response.status_code == 503: # مدل در حال Cold Boot
                logger.info("Model is loading, waiting for 30 seconds...")
                time.sleep(30) # 30 ثانیه صبر کن
            else:
                raise # ارور دیگه بود، مستقیم بده بیرون
    raise Exception(f"Failed to query model {model_name} after multiple attempts.")


async def generate_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not HF_TOKEN:
        await update.message.reply_text("❌ خطا: توکن Hugging Face (HF_TOKEN) تنظیم نشده است. لطفاً آن را در Render Environment Variables وارد کنید.")
        return

    user_text = update.message.text
    wait_msg = await update.message.reply_text("⏳ دارم از Hugging Face می‌پرسم (کمی صبر کن)...")

    try:
        # پرامپت
        prompt_text = f"<s>[INST] You are an expert Instagram admin. Write 3 Reels ideas, 1 caption, and 10 hashtags in PERSIAN (Farsi) for this topic: '{user_text}'. Keep it professional and engaging. [/INST]"
        
        payload = {"inputs": prompt_text, "parameters": {"max_new_tokens": 1000, "return_full_text": False}} # return_full_text: False برای جواب تمیزتر

        result = None
        for model_name in MISTRAL_MODELS: # مدل‌ها رو یکی یکی امتحان کن
            try:
                result = await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id, 
                    message_id=wait_msg.message_id, 
                    text=f"⏳ در حال پرسیدن از مدل {model_name}..."
                )
                response_data = await query_huggingface(payload, model_name)
                
                if isinstance(response_data, list) and len(response_data) > 0 and 'generated_text' in response_data[0]:
                    final_text = response_data[0]['generated_text'].strip()
                    if final_text: # اگه جواب خالی نبود
                        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
                        await update.message.reply_text(final_text)
                        return # موفقیت!
                    else:
                        raise Exception("Generated text is empty from model {model_name}.")
                else:
                    raise Exception(f"Invalid response structure from model {model_name}. Response: {json.dumps(response_data)}")
            except Exception as e:
                logger.error(f"Error with model {model_name}: {e}")
                # اگه این مدل کار نکرد، میره سراغ مدل بعدی

        # اگه هیچ مدلی کار نکرد
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, 
            message_id=wait_msg.message_id, 
            text="❌ هیچ یک از مدل‌های Hugging Face نتوانستند پاسخ دهند. لطفاً بعداً دوباره امتحان کنید."
        )

    except requests.exceptions.Timeout:
        logger.error("Request to Hugging Face timed out.")
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, 
            message_id=wait_msg.message_id, 
            text="❌ درخواست به هوش مصنوعی زمان‌بندی شد (Timeout). لطفاً دوباره امتحان کنید."
        )
    except Exception as e:
        logger.error(f"General Error: {e}")
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, 
            message_id=wait_msg.message_id, 
            text=f"❌ خطای نامشخص: {e}"
        )

if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), generate_content))
    print("🤖 BOT STARTED WITH HUGGING FACE (Robust Version)...")
    application.run_polling()
    
