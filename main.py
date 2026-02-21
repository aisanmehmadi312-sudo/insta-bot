import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import json # برای بسته‌بندی داده در دکمه‌ها

from openai import OpenAI
from supabase import create_client, Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler,
    filters, ConversationHandler, CallbackQueryHandler
)

# --- تمام بخش‌های تنظیمات، توکن‌ها، سرور و اتصال به سرویس‌ها مثل قبل ---
# ... (کدهای این بخش‌ها بدون تغییر باقی می‌مانند) ...
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

# --- اتصال به سرویس‌ها ---
client = None
if OPENAI_API_KEY:
    try: client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e: logger.error(f"OpenAI Config Error: {e}")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try: supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e: logger.error(f"Supabase Config Error: {e}")

# ---------------------------------------------
# --- تابع ثبت آمار ---
def log_event(user_id: str, event_type: str, content: str = ""):
    if not supabase: return
    try:
        data_to_insert = {'user_id': str(user_id), 'event_type': event_type, 'content': content}
        supabase.table('logs').insert(data_to_insert).execute()
    except Exception as e:
        logger.error(f"Supabase log event error: {e}")

# ---------------------------------------------

# --- مراحل مکالمه پروفایل (بدون تغییر) ---
P_BUSINESS, P_GOAL, P_AUDIENCE, P_TONE = range(4)
# ... (تمام توابع مربوط به ساخت پروفایل از profile_start تا cancel_profile مثل کد قبلی باقی می‌مانند) ...

# ---------------------------------------------

# --- مراحل جدید مکالمه تولید محتوا ---
IDEAS, EXPAND = range(4, 6)

async def check_profile_before_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """یک پیش-تابع که پروفایل را چک کرده و مکالمه محتوا را شروع می‌کند."""
    user_id = str(update.effective_user.id)
    try:
        response = supabase.table('profiles').select("*").eq('user_id', user_id).execute()
        if not response.data:
            await update.message.reply_text("❌ اول باید پروفایلت رو با دستور /profile بسازی.")
            return ConversationHandler.END # مکالمه را خاتمه می‌دهد
        
        context.user_data['profile'] = response.data[0]
        context.user_data['topic'] = update.message.text
        return await generate_ideas(update, context) # مستقیم به مرحله ایده‌پردازی می‌رود
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در خواندن پروفایل: {e}")
        return ConversationHandler.END

async def generate_ideas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۱: تولید سه ایده متفاوت برای موضوع."""
    user_profile = context.user_data['profile']
    topic = context.user_data['topic']
    wait_msg = await update.message.reply_text("⏳ در حال ایده‌پردازی و طوفان فکری...")

    try:
        prompt_ideation = f"""
        **شخصیت:** تو یک ایده‌پرداز خلاق برای محتوای اینستاگرام هستی.
        **ماموریت:** برای «موضوع» زیر، سه ایده یا کانسپت کاملاً متفاوت و جذاب برای یک ریلز اینستاگرامی پیشنهاد بده. هر ایده باید یک «عنوان» و یک «قلاب» (جمله اول) منحصر به فرد داشته باشد.
        
        - **کسب‌وکار:** {user_profile['business']}
        - **موضوع:** "{topic}"

        **ساختار خروجی (بسیار مهم):**
        خروجی تو باید دقیقاً یک لیست JSON باشد که شامل سه آبجکت است. هر آبجکت دو کلید دارد: "title" و "hook". مثال:
        [
          {{"title": "ایده اول: زاویه دید تاریخی", "hook": "آیا می‌دانستید موز در ابتدا...؟"}},
          {{"title": "ایده دوم: زاویه دید سلامتی", "hook": "این سه خاصیت موز را هیچکس به شما نمی‌گوید."}},
          {{"title": "ایده سوم: زاویه دید سرگرمی", "hook": "با پوست موز چه کارهای عجیبی می‌توان کرد؟"}}
        ]
        
        **قانون:** فقط و فقط همین ساختار JSON را خروجی بده. هیچ متن اضافه یا توضیحی ننویس.
        """
        response = client.chat.completions.create(model="gpt-4o", response_format={"type": "json_object"}, messages=[{"role": "user", "content": prompt_ideation}])
        ideas_json = json.loads(response.choices[0].message.content)
        
        context.user_data['ideas'] = ideas_json # ذخیره ایده‌ها برای مرحله بعد
        
        keyboard = []
        for i, idea in enumerate(ideas_json):
            # callback_data باید کوتاه باشد، پس فقط شماره ایده را می‌فرستیم
            button = InlineKeyboardButton(f"🎬 دریافت سناریوی ایده {i+1}: {idea['title']}", callback_data=f'expand_{i}')
            keyboard.append([button])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message_text = f"عالی! برای موضوع «{topic}»، سه ایده متفاوت پیدا کردم:\n\n"
        for i, idea in enumerate(ideas_json):
            message_text += f"**ایده {i+1}: {idea['title']}**\n- قلاب: «{idea['hook']}»\n\n"
        message_text += "کدام یک را برایت به یک سناریوی کامل تبدیل کنم؟"
        
        await wait_msg.edit_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')
        
        log_event(str(update.effective_user.id), 'ideas_generated', topic)
        return EXPAND # برو به مرحله بعدی و منتظر کلیک کاربر باش

    except Exception as e:
        log_event(str(update.effective_user.id), 'ideation_error', str(e))
        logger.error(f"Error in generate_ideas: {e}")
        await wait_msg.edit_text(f"❌ ببخشید، در مرحله ایده‌پردازی مشکلی پیش آمد: {e}")
        return ConversationHandler.END

async def expand_idea(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۲: گسترش ایده انتخابی به یک سناریوی کامل."""
    query = update.callback_query
    await query.answer()

    idea_index = int(query.data.split('_')[1])
    chosen_idea = context.user_data['ideas'][idea_index]
    user_profile = context.user_data['profile']
    
    await query.edit_message_text(f"✅ شما ایده «{chosen_idea['title']}» را انتخاب کردید.\n⏳ در حال نوشتن سناریوی کامل...")

    try:
        # از پرامپت نهایی و کامل قبلی برای نوشتن سناریو استفاده می‌کنیم
        prompt_expansion = f"""
        **شخصیت تو:** تو یک متخصص تولید محتوای خلاق و کاربلد ایرانی هستی.
        **ماموریت اصلی تو:** بر اساس پروفایل کاربر و ایده‌ای که انتخاب کرده، یک نقشه ساخت کامل و حرفه‌ای برای یک ریلز اینستاگرامی بنویسی.

        **اطلاعات کاربر:**
        - کسب‌وکار: {user_profile['business']}
        - هدف اصلی محتوا: {user_profile.get('goal', 'نامشخص')}
        - مخاطب: {user_profile['audience']}
        - لحن: {user_profile['tone']}
        - **ایده انتخابی:** (عنوان: {chosen_idea['title']}, قلاب: {chosen_idea['hook']})

        ---
        **نقشه راه اجرای ماموریت:**
        یک سناریوی کامل بر اساس ساختار زیر به زبان فارسی روان بنویس.
        
        **ساختار نقشه ساخت:**
        ### 🎬 نقشه ساخت ریلز: {chosen_idea['title']}

        ۱. قلاب (۰-۳ ثانیه):
        - تصویر: (شرح صحنه اول مرتبط با قلاب)
        - متن روی صفحه: «{chosen_idea['hook']}»

        ۲. بدنه اصلی (۴-۲۰ ثانیه):
        - تصویر: (شرح سکانس‌های اصلی برای بسط ایده)
        - گفتار: (متن صحبت‌ها)

        ۳. فراخوان به اقدام (۲۱-۳۰ ثانیه):
        - تصویر: (شرح صحنه پایانی)
        - متن روی صفحه: (درخواست واضح از مخاطب)
        
        ---
        ### ✍️ کپشن و هشتگ‌ها
        - کپشن: (کپشن جذاب و فارسی)
        - هشتگ‌ها: (۵ تا ۷ هشتگ فارسی)
        ---
        **قانون نهایی:**
        هرگز از کاراکتر `*` برای بولد کردن استفاده نکن. کل پاسخ باید متن ساده و بدون قالب‌بندی بولد باشد.
        """
        response = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt_expansion}])
        ai_reply = response.choices[0].message.content.strip()

        # محافظ نهایی کد
        if '*' in ai_reply:
            logger.warning("AI violated the 'no-asterisk' rule. Sanitizing output.")
            ai_reply = ai_reply.replace('*', '')

        await context.bot.send_message(chat_id=update.effective_chat.id, text=ai_reply)
        log_event(str(update.effective_user.id), 'expansion_success', chosen_idea['title'])

    except Exception as e:
        log_event(str(update.effective_user.id), 'expansion_error', str(e))
        logger.error(f"Error in expand_idea: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ ببخشید، در نوشتن سناریوی کامل مشکلی پیش آمد: {e}")

    context.user_data.clear()
    return ConversationHandler.END


# ---------------------------------------------
if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # مکالمه ساخت پروفایل (مثل قبل)
    profile_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('profile', profile_start)],
        states={
            P_BUSINESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_business)],
            P_GOAL: [CallbackQueryHandler(get_goal, pattern='^goal_')],
            P_AUDIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_audience)],
            P_TONE: [CallbackQueryHandler(get_tone_and_save, pattern='^tone_')],
        },
        fallbacks=[CommandHandler('cancel', cancel_profile)],
    )

    # مکالمه جدید برای تولید محتوا
    content_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, check_profile_before_content)],
        states={
            EXPAND: [CallbackQueryHandler(expand_idea, pattern='^expand_')],
        },
        fallbacks=[CommandHandler('cancel', cancel_profile)],
    )
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(profile_conv_handler)
    application.add_handler(content_conv_handler) # جایگزین MessageHandler قبلی
    
    print("🤖 BOT DEPLOYED WITH MULTI-IDEA GENERATION!")
    application.run_polling()
        
