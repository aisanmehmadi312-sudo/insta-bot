import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from openai import OpenAI
from supabase import create_client, Client
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler,
    filters, ConversationHandler
)

# تنظیمات لاگ
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- دریافت توکن‌ها ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

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

# --- اتصال به سرویس‌ها ---
client = None
if OPENAI_API_KEY:
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        logger.error(f"OpenAI Config Error: {e}")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        logger.error(f"Supabase Config Error: {e}")

# ---------------------------------------------

# --- مراحل مکالمه برای ساخت پروفایل ---
BUSINESS, AUDIENCE, TONE = range(3)

async def profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("خب، بیا پروفایل کسب‌وکارت رو بسازیم.\n\n**موضوع اصلی پیج شما چیست؟**", parse_mode='Markdown')
    return BUSINESS

async def get_business(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['business'] = update.message.text
    await update.message.reply_text("عالی! حالا بگو **مخاطب هدفت چه کسانی هستند؟**", parse_mode='Markdown')
    return AUDIENCE

async def get_audience(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['audience'] = update.message.text
    await update.message.reply_text("و در آخر، **لحن برندت چیست؟** (صمیمی، رسمی، شوخ)", parse_mode='Markdown')
    return TONE

async def get_tone_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['tone'] = update.message.text
    user_id = str(update.effective_user.id)
    
    profile_data = {'user_id': user_id, 'business': context.user_data['business'], 'audience': context.user_data['audience'], 'tone': context.user_data['tone']}
    
    try:
        supabase.table('profiles').upsert(profile_data, on_conflict='user_id').execute()
        await update.message.reply_text("✅ پروفایل شما با موفقیت ذخیره/آپدیت شد!")
    except Exception as e:
        logger.error(f"Supabase upsert Error: {e}")
        await update.message.reply_text(f"❌ خطا در ذخیره پروفایل: {e}")
    return ConversationHandler.END

async def cancel_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("عملیات ساخت پروفایل لغو شد.")
    return ConversationHandler.END

# ---------------------------------------------

# --- دستورات اصلی ربات ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! 👋\nبرای ساخت/ویرایش پروفایل، دستور /profile رو بزن.\nبعد از اون، هر موضوعی بفرستی، بر اساس پروفایلت برات سناریو ریلز می‌سازم.")

async def generate_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    try:
        response = supabase.table('profiles').select("*").eq('user_id', user_id).execute()
        if not response.data:
            await update.message.reply_text("❌ اول باید پروفایلت رو بسازی! لطفاً دستور /profile رو بزن.")
            return
        user_profile = response.data[0]
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در خواندن پروفایل از دیتابیس: {e}")
        return

    user_text = update.message.text
    wait_msg = await update.message.reply_text("⏳ دارم موضوع رو با پروفایلت چک می‌کنم...")

    try:
        # --- دستور (Prompt) جدید با قانون سخت‌گیرانه ---
        prompt = f"""
        **شخصیت شما (Persona):**
        تو یک استراتژیست برند بسیار دقیق و سخت‌گیر برای اینستاگرام هستی. مهم‌ترین وظیفه تو، حفظ یکپارچگی و ثبات برند (Brand Consistency) کاربر است.

        **اطلاعات کسب‌وکار کاربر (User Profile):**
        - موضوع اصلی پیج: {user_profile['business']}
        - مخاطب هدف: {user_profile['audience']}
        - لحن برند: {user_profile['tone']}

        **موضوع درخواستی کاربر:** "{user_text}"

        ---
        **وظیفه (Task):**
        
        **قانون شماره ۱ (مهم‌ترین قانون): بررسی دقیق ارتباط موضوع**
        1.  پروفایل کاربر و موضوع درخواستی را با دقت مقایسه کن.
        2.  **اگر** موضوع درخواستی کاربر **هیچ ارتباط مستقیم و واضحی** با "موضوع اصلی پیج" او نداشت، **هرگز و تحت هیچ شرایطی سناریو نساز.**
            *   **مثال برای رد کردن:** اگر پروفایل "فروش لوازم آرایشی" است و کاربر "تعمیر خودرو" را درخواست دهد، این کاملا بی‌ربط است.
            *   **مثال دیگر برای رد کردن:** اگر پروفایل "فروش موز" است و کاربر "خرس قطبی" را درخواست دهد، این هم کاملا بی‌ربط است.
        3.  در این حالت (یعنی در صورت کاملاً بی‌ربط بودن)، **فقط و فقط** این پاسخ کوتاه را بده:
            "موضوع «{user_text}» با پروفایل کسب‌وکار شما ارتباطی ندارد. لطفاً یک موضوع مرتبط ارائه دهید."

        **قانون شماره ۲: ساخت سناریو (فقط در صورت تایید قانون ۱)**
        *   **فقط و فقط اگر** موضوع کاملاً مرتبط بود، آنگاه وظیفه اصلی خودت یعنی نوشتن سناریوی کامل ریلز را انجام بده.
        
        **ساختار خروجی برای سناریو:**
        (ساختار کامل سناریو، کپشن و هشتگ که قبلاً توافق کردیم)
        ### 🎬 سناریوی ریلز وایرال
        ...
        """
        
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        ai_reply = response.choices[0].message.content
        
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
        
        final_message = ai_reply
        if len(ai_reply) < 200 and "### 🎬" not in ai_reply:
            final_message = f"**توجه:**\n{ai_reply}"

        try:
            await update.message.reply_text(final_message, parse_mode='Markdown')
        except BadRequest as e:
            if "Can't parse entities" in str(e):
                logger.warning(f"Markdown parse error. Sending as plain text. Error: {e}")
                fallback_text = "⚠️ فرمت پاسخ تولید شده توسط هوش مصنوعی مشکل داشت. متن خام پاسخ:\n\n" + ai_reply
                await update.message.reply_text(fallback_text)
            else:
                raise e

    except Exception as e:
        logger.error(f"Error in generate_content: {e}")
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
        except Exception as delete_error:
            logger.error(f"Could not delete wait message: {delete_error}")
        
        await update.message.reply_text(f"❌ ببخشید، در پردازش درخواست شما مشکلی پیش آمد.\n\nجزئیات فنی: {e}")


if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('profile', profile_start)],
        states={
            BUSINESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_business)],
            AUDIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_audience)],
            TONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_tone_and_save)],
        },
        fallbacks=[CommandHandler('cancel', cancel_profile)],
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), generate_content))
    
    print("🤖 BOT DEPLOYED SUCCESSFULLY WITH STRICT PROMPT!")
    application.run_polling()
                        
