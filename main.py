import os
import logging
import threading
import requests
import json  # برای بررسی پاسخ JSON
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# تنظیمات لاگ (برای دیدن وضعیت ربات)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
HF_TOKEN = os.environ.get("HF_TOKEN")

# --- آدرس API Hugging Face (مهم: آدرس استاندارد inference) ---
# آدرس API Inference همیشه api-inference.huggingface.co/models/ هست
# ارور قبلی که router.huggingface.co رو پیشنهاد داده بود، مربوط به یک مورد خاص بوده
API_URL = "https://api-inference.huggingface.co/models/gpt2"
headers = {"Authorization": f"Bearer {HF_TOKEN}"}
# ----------------------------------------------------------------

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
        await update.message.reply_text("سلام! ربات با GPT2 آماده‌ست. یه موضوع بگو! 🚀")

async def generate_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not HF_TOKEN:
        await update.message.reply_text("❌ خطا: توکن Hugging Face (HF_TOKEN) تنظیم نشده است. لطفاً آن را در Render Environment Variables وارد کنید.")
        return

    user_text = update.message.text
    wait_msg = await update.message.reply_text("⏳ دارم از GPT2 می‌پرسم (حداکثر ۲۰ ثانیه)...")

    try:
        # پرامپت ساده (GPT2 خیلی پیچیده نیست)
        prompt = f"Instagram content ideas for '{user_text}' in Persian (Farsi):\n"
        
        for i in range(3): # 3 بار تلاش میکنیم برای Cold Boot
            response = requests.post(
                API_URL, 
                headers=headers, 
                json={"inputs": prompt, "parameters": {"max_new_tokens": 200}},
                timeout=60 # 60 ثانیه برای پاسخ صبر میکنیم
            )
            
            # بررسی کد وضعیت HTTP
            if response.status_code == 200:
                try:
                    result = response.json()
                    if isinstance(result, list) and len(result) > 0 and 'generated_text' in result[0]:
                        final_text = result[0]['generated_text'].replace(prompt, "").strip()
                        if final_text:
                            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
                            await update.message.reply_text(f"**ایده‌های اینستاگرام برای {user_text}:**\n{final_text}")
                            return # موفقیت!
                        else:
                            raise Exception("Generated text is empty.")
                    else:
                        raise Exception(f"Invalid JSON structure. Response: {json.dumps(result)}")
                except json.JSONDecodeError:
                    # اگه جواب JSON نبود، محتوای خام رو نشون بده
                    raw_response_text = response.text
                    raise Exception(f"Hugging Face returned non-JSON data. Raw: {raw_response_text[:500]}...") # فقط 500 کاراکتر اول
            elif response.status_code == 503:
                # مدل در حال Cold Boot است
                error_details = response.json().get("error_details", {})
                estimated_time = error_details.get("estimated_time", 15)
                logger.info(f"Model is loading (Cold Boot), waiting for {estimated_time} seconds...")
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id, 
                    message_id=wait_msg.message_id, 
                    text=f"⚠️ مدل در حال بیدار شدن است (Cold Boot). لطفاً {int(estimated_time)} ثانیه دیگر دوباره امتحان کنید."
                )
                time.sleep(estimated_time + 5) # کمی بیشتر از زمان تخمینی صبر کن
            else:
                raise Exception(f"Hugging Face API Error: {response.status_code} - {response.text}")
        
        # اگه بعد از 3 بار تلاش هم نشد
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=wait_msg.message_id,
            text="❌ مدل Hugging Face نتوانست بیدار شود یا پاسخ دهد. لطفاً بعداً دوباره امتحان کنید."
        )


    except requests.exceptions.Timeout:
        logger.error("Request to Hugging Face timed out.")
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, 
            message_id=wait_msg.message_id, 
            text="❌ درخواست به هوش مصنوعی زمان‌بندی شد. ممکن است سرور شلوغ باشد. دوباره امتحان کنید."
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
    print("🤖 BOT STARTED WITH GPT2 (Final Robust Version)...")
    application.run_polling()
