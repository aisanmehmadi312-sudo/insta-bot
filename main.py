import os
import logging
import threading
import requests
import json
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# تنظیمات لاگ
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
HF_TOKEN = os.environ.get("HF_TOKEN")

# --- آدرس پایه Hugging Face Inference API ---
# این آدرس استاندارد است و باید کار کند
HF_INFERENCE_API_BASE_URL = "https://api-inference.huggingface.co/models/"
# ---------------------------------------------

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

# تابع برای پیدا کردن اولین مدل Text Generation
def get_working_hf_model(hf_token):
    if not hf_token:
        logger.error("HF_TOKEN is not set, cannot query Hugging Face models.")
        return None, "Error: HF_TOKEN is missing."

    # لیست مدل‌های Text Generation که معمولاً رایگان و فعال هستند
    common_text_gen_models = [
        "gpt2",
        "distilgpt2",
        "facebook/opt-125m",
        "EleutherAI/gpt-neo-125m",
        "databricks/dolly-v2-3b", # این کمی بزرگتر است
    ]

    headers = {"Authorization": f"Bearer {hf_token}"}

    for model_name in common_text_gen_models:
        test_url = f"{HF_INFERENCE_API_BASE_URL}{model_name}"
        try:
            # یک درخواست کوچک برای تست مدل
            test_response = requests.post(
                test_url,
                headers=headers,
                json={"inputs": "test input", "parameters": {"max_new_tokens": 1}}
            )
            if test_response.status_code == 200:
                logger.info(f"✅ Found working Hugging Face model: {model_name}")
                return model_name, None
            elif test_response.status_code == 503:
                logger.info(f"Model {model_name} is loading (Cold Boot)...")
                # اگه Cold Boot بود، رد شو، شاید مدل بعدی بیدار باشه
                continue
            else:
                logger.warning(f"Model {model_name} returned {test_response.status_code}: {test_response.text}")
        except requests.exceptions.RequestException as e:
            logger.warning(f"Failed to connect to model {model_name}: {e}")
            continue # اگه این مدل وصل نشد، بعدی رو امتحان کن
    
    logger.error("❌ No working Hugging Face Text Generation model found among common ones.")
    return None, "Error: No active Hugging Face model found for text generation."

# پیدا کردن مدل موقع شروع ربات
HF_MODEL_ID, HF_MODEL_ERROR = get_working_hf_model(HF_TOKEN)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if HF_MODEL_ID:
        await update.message.reply_text(f"سلام! ربات با مدل {HF_MODEL_ID} (Hugging Face) آماده‌ست. یه موضوع بگو! 🚀")
    else:
        await update.message.reply_text(f"❌ ربات نتوانست به مدل Hugging Face وصل شود: {HF_MODEL_ERROR}")


async def generate_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not HF_MODEL_ID:
        await update.message.reply_text(f"❌ ربات نتوانست به مدل Hugging Face وصل شود: {HF_MODEL_ERROR}")
        return

    user_text = update.message.text
    wait_msg = await update.message.reply_text("⏳ دارم از Hugging Face می‌پرسم (ممکن است تا ۳۰ ثانیه طول بکشد)...")

    try:
        # پرامپت
        prompt = f"Instagram content ideas for '{user_text}' in Persian (Farsi):\n"
        
        # استفاده از مدل پیدا شده
        API_URL = f"{HF_INFERENCE_API_BASE_URL}{HF_MODEL_ID}"
        headers = {"Authorization": f"Bearer {HF_TOKEN}"}

        response = requests.post(
            API_URL, 
            headers=headers, 
            json={"inputs": prompt, "parameters": {"max_new_tokens": 200}},
            timeout=90 # 90 ثانیه برای پاسخ صبر میکنیم
        )
            
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
                raw_response_text = response.text
                raise Exception(f"Hugging Face returned non-JSON data. Raw: {raw_response_text[:500]}...")
        elif response.status_code == 503: # مدل در حال Cold Boot است
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=wait_msg.message_id,
                text="⚠️ مدل در حال بیدار شدن است (Cold Boot). لطفاً ۳۰ ثانیه دیگر دوباره امتحان کنید."
            )
        else:
            raise Exception(f"Hugging Face API Error: {response.status_code} - {response.text}")
        
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
    print("🤖 BOT STARTED WITH HUGGING FACE (Dynamic Model Selection)...")
    application.run_polling()
        
