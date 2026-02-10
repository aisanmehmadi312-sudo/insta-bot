import os
import logging
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# تنظیمات لاگ (برای دیدن وضعیت ربات)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# دریافت توکن‌ها از محیط Render
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

# بررسی امنیت: اگر توکن‌ها نبودن، ارور بده
if not TELEGRAM_TOKEN or not GOOGLE_API_KEY:
    logging.error("❌ Fatal Error: TELEGRAM_TOKEN or GOOGLE_API_KEY is missing!")
    exit(1)

# اتصال به هوش مصنوعی گوگل
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-pro')
    logging.info("✅ Google Gemini Connected!")
except Exception as e:
    logging.error(f"❌ Gemini Error: {e}")

# دستور استارت
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=update.effective_chat.id, 
        text="سلام! 👋 من ربات هوشمند اینستاگرام هستم.\n\nیه موضوع بگو (مثلاً: 'فروش قهوه') تا برات ایده، کپشن و هشتگ بسازم! 🚀"
    )

# تابع اصلی تولید محتوا
async def generate_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    chat_id = update.effective_chat.id

    # پیام انتظار
    wait_msg = await context.bot.send_message(chat_id=chat_id, text="⏳ دارم فکر می‌کنم... لطفاً صبر کنید.")

    try:
        # ساختن دستور برای هوش مصنوعی
        prompt = f"""
        تو یک ادمین حرفه‌ای و خلاق اینستاگرام هستی.
        کاربر می‌خواهد درباره موضوع زیر پست بگذارد:
        "{user_text}"

        لطفاً خروجی زیر را تولید کن:
        1️⃣ **۳ ایده خلاقانه برای ریلز (Reels)** (سناریوی کوتاه).
        2️⃣ **یک کپشن جذاب** (با لحن صمیمی و ایموجی).
        3️⃣ **۱۰ تا هشتگ مرتبط** (فارسی و انگلیسی).

        پاسخ را مرتب و خوانا بنویس.
        """

        # درخواست به گوگل
        response = model.generate_content(prompt)
        ai_reply = response.text

        # حذف پیام انتظار و ارسال جواب
        await context.bot.delete_message(chat_id=chat_id, message_id=wait_msg.message_id)
        await context.bot.send_message(chat_id=chat_id, text=ai_reply, parse_mode='Markdown')

    except Exception as e:
        logging.error(f"Error: {e}")
        await context.bot.edit_message_text(
            chat_id=chat_id, 
            message_id=wait_msg.message_id, 
            text="❌ اوه! یه مشکلی پیش اومد. لطفاً دوباره امتحان کن."
        )

if __name__ == '__main__':
    # ساخت و اجرای ربات
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    start_handler = CommandHandler('start', start)
    msg_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), generate_content)
    
    application.add_handler(start_handler)
    application.add_handler(msg_handler)
    
    print("🤖 Bot is running on Render...")
    application.run_polling()
