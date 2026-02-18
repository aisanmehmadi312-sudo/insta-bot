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
    await update.message.reply_text("خب، بیا پروفایل کسب‌وکارت رو بسازیم.\n\n**موضوع اصلی پیج شما چیست؟**")
    return BUSINESS

async def get_business(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['business'] = update.message.text
    await update.message.reply_text("عالی! حالا بگو **مخاطب هدفت چه کسانی هستند؟**")
    return AUDIENCE

async def get_audience(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['audience'] = update.message.text
    await update.message.reply_text("و در آخر، **لحن برندت چیست؟** (صمیمی، رسمی، شوخ)")
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
    wait_msg = await update.message.reply_text("⏳ سناریونویس در حال بررسی موضوع است...")

    try:
        # پرامپت سناریونویس با فیلتر هوشمند
        prompt = f"""
        **شخصیت شما (Persona):**
        شما یک سناریونویس محتوای وایرال و یک **استراتژیست برند بسیار دقیق** برای اینستاگرام هستی. مهم‌ترین وظیفه شما، حفظ **یکپارچگی و ثبات برند (Brand Consistency)** کاربر است. شما خلاق، مدرن و مسلط به زبان فارسی محاوره‌ای هستی.

        **وظیفه (Task):**
        شما باید یک سناریوی ریلز برای موضوعی که کاربر ارائه داده، تولید کنی. **اما قبل از هر کاری، باید یک بررسی مهم انجام دهی:**

        **قانون شماره ۱: بررسی ارتباط موضوع (Relevance Check)**
        1.  اطلاعات پروفایل کاربر و موضوع درخواستی امروز او را با دقت مقایسه کن.
        2.  **اگر** موضوع درخواستی کاربر (مثلاً: "گوشت گوسفند") **هیچ ارتباط منطقی** با "موضوع اصلی پیج" او (مثلاً: "فروش میوه") نداشت، **هرگز سناریو نساز.**
        3.  در این حالت، باید یک پاسخ محترمانه و کوتاه بنویسی و توضیح دهی که این موضوع با پروفایل تعریف شده هماهنگ نیست. (مثال پاسخ: "موضوع درخواستی شما با پروفایل کسب‌وکارتان (فروش میوه) مرتبط نیست. لطفاً موضوعی مرتبط با کسب‌وکارتان ارائه دهید.")
        4.  **فقط و فقط اگر** موضوع درخواستی با پروفایل مرتبط بود، آنگاه وظیفه اصلی (نوشتن سناریو) را انجام بده.

        **اطلاعات کسب‌وکار کاربر (User Profile):**
        - موضوع پیج: {user_profile['business']}
        - مخاطب هدف: {user_profile['audience']}
        - لحن برند: {user_profile['tone']}

        **موضوع امروز کاربر:** "{user_text}"

        ---
        **(در صورت مرتبط بودن موضوع، ساختار خروجی زیر را دنبال کن):**

        ### 🎬 سناریوی ریلز وایرال

        **عنوان قلاب‌کننده (Title/Hook):**
        [یک عنوان کوتاه، سوالی یا بحث‌برانگیز]

        **موزیک پیشنهادی (Music):**
        [اسم دقیق یک آهنگ ترند در اینستاگرام]

        **ساختار ویدیو (Video Structure):**

        **۱. صحنه اول: قلاب (Hook) - (۰ تا ۳ ثانیه)**
        - **تصویر:** [توصیف یک نمای سریع و جذاب]
        - **متن روی ویدیو:** [یک جمله کوتاه و جسورانه]

        **۲. صحنه دوم: بدنه اصلی (Core Value) - (۳ تا ۱۰ ثانیه)**
        - **تصویر:** [توصیف **حداقل ۳ کات سریع (Quick Cut)**]
        - **متن روی ویدیو:** [کلمات کلیدی کوتاه که با هر کات ظاهر می‌شوند]

        **۳. صحنه سوم: اوج و CTA - (۱۰ تا ۱۵ ثانیه)**
        - **تصویر:** [یک نمای خلاقانه و نهایی]
        - **متن روی ویدیو:** [فراخوان به اقدام واضح، مثلاً: "کپشن رو بخون!"]

        ---
        ### ✍️ کپشن پیشنهادی

        [یک کپشن کوتاه و صمیمی. **نباید شامل شعر باشد.**]
        - **شروع:** تکرار قلاب ویدیو.
        - **بدنه:** یک نکته کوتاه و مفید.
        - **سوال از مخاطب:** یک سوال ساده برای افزایش کامنت.

        ---
        ### #️⃣ هشتگ‌ها (۵ تا ۷ عدد)

        [بین ۵ تا ۷ هشتگ بسیار مرتبط و کلیدی. **فقط هشتگ، بدون توضیح.**]
        """
        
        # درخواست به OpenAI
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        ai_reply = response.choices[0].message.content
        
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
        await update.message.reply_text(ai_reply, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"OpenAI/Generate Error: {e}")
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, 
            message_id=wait_msg.message_id, 
            text=f"❌ خطای OpenAI: {e}"
        )

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
    
    print("🤖 BOT STARTED WITH SMART FILTER PROMPT...")
    application.run_polling()
