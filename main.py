import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from openai import OpenAI
from supabase import create_client, Client
from telegram import Update
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
# OpenAI
client = None
if OPENAI_API_KEY:
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        logger.error(f"OpenAI Config Error: {e}")

# Supabase
supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        logger.error(f"Supabase Config Error: {e}")
else:
    logger.error("❌ Supabase URL or Key not found!")
# ---------------------------------------------

# --- مراحل مکالمه برای ساخت پروفایل ---
BUSINESS, AUDIENCE, TONE = range(3)

async def profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("خب، بیا پروفایل کسب‌وکارت رو بسازیم.\n\n**موضوع اصلی پیج شما چیست؟** (مثلاً: فروش آنلاین قهوه)")
    return BUSINESS

async def get_business(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['business'] = update.message.text
    await update.message.reply_text("عالی! حالا بگو **مخاطب هدفت چه کسانی هستند؟** (مثلاً: دانشجویان ۱۸ تا ۲۵ ساله)")
    return AUDIENCE

async def get_audience(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['audience'] = update.message.text
    await update.message.reply_text("و در آخر، **لحن برندت چیست؟** (مثلاً: صمیمی و دوستانه، رسمی، شوخ)")
    return TONE

async def get_tone_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['tone'] = update.message.text
    user_id = str(update.effective_user.id)
    
    profile_data = {
        'business': context.user_data['business'],
        'audience': context.user_data['audience'],
        'tone': context.user_data['tone'],
        'user_id': user_id
    }
    
    try:
        # ذخیره یا آپدیت در دیتابیس Supabase
        # upsert = اگه user_id وجود داشت، آپدیت کن، اگه نداشت، بساز
        data, count = supabase.table('profiles').upsert(profile_data, on_conflict='user_id').execute()
        await update.message.reply_text("✅ پروفایل شما با موفقیت ذخیره/آپدیت شد!")
    except Exception as e:
        logger.error(f"Supabase upsert Error: {e}")
        await update.message.reply_text(f"❌ خطا در ذخیره پروفایل: {e}")

    return ConversationHandler.END

async def cancel_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("عملیات ساخت پروفایل لغو شد.")
    return ConversationHandler.END

# --- دستورات اصلی ربات ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! 👋\nبرای ساخت یا ویرایش پروفایل کسب‌وکارت، دستور /profile رو بزن.\nبعد از اون، هر موضوعی بفرستی، بر اساس پروفایلت برات محتوا می‌سازم.")

async def generate_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    # چک کردن وجود پروفایل
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
    wait_msg = await update.message.reply_text("⏳ ...")

    try:
        # پرامپت هوشمند با استفاده از پروفایل کاربر
        prompt = f"""
        **شخصیت شما:**
        شما یک کارگردان خلاق و استراتژیست محتوای وایرال برای اینستاگرام هستی.

        **اطلاعات کسب‌وکار کاربر:**
        - موضوع پیج: {user_profile['business']}
        - مخاطب هدف: {user_profile['audience']}
        - لحن برند: {user_profile['tone']}

        **وظیفه:**
        با توجه دقیق به اطلاعات بالا، یک سناریوی کامل برای ریلز تولید کن.
        **موضوع امروز:** "{user_text}"

        **ساختار خروجی:**
        (از ساختار کارگردانی که قبلاً توافق کردیم، استفاده کن)
        """
        
        # درخواست به OpenAI
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        ai_reply = response.choices[0].message.content
        
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
        await update.message.reply_text(ai_reply)

    except Exception as e:
        logger.error(f"OpenAI/Generate Error: {e}")
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, 
            message_id=wait_msg.message_id, 
            text=f"❌ خطای OpenAI: {e}"
        )

if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # تعریف مکالمه برای پروفایل
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
    
    print("🤖 BOT STARTED WITH PROFILE & SUPABASE FEATURE...")
    application.run_polling()
                       
