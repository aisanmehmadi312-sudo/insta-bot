import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from openai import OpenAI
from supabase import create_client, Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler,
    filters, ConversationHandler, CallbackQueryHandler
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
# --- تابع ثبت آمار ---
def log_event(user_id: str, event_type: str, content: str = ""):
    if not supabase: return
    try:
        data_to_insert = {'user_id': str(user_id), 'event_type': event_type, 'content': content}
        supabase.table('logs').insert(data_to_insert).execute()
    except Exception as e:
        logger.error(f"Supabase log event error: {e}")

# ---------------------------------------------

# --- مراحل مکالمه پروفایل ---
BUSINESS, GOAL, AUDIENCE, TONE = range(4)

async def profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    log_event(update.effective_user.id, 'profile_start')
    await update.message.reply_text("۱/۴ - موضوع اصلی پیج شما چیست؟\n(مثال: فروش آنلاین قهوه، آموزش یوگا)")
    return BUSINESS

async def get_business(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['business'] = update.message.text
    keyboard = [
        [InlineKeyboardButton("افزایش فروش", callback_data='goal_sales'), InlineKeyboardButton("آگاهی از برند", callback_data='goal_awareness')],
        [InlineKeyboardButton("آموزش به مخاطب", callback_data='goal_education'), InlineKeyboardButton("سرگرمی و کامیونیتی", callback_data='goal_community')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("۲/۴ - هدف اصلی شما از تولید محتوا چیست؟", reply_markup=reply_markup)
    return GOAL

async def get_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    button_text = next(btn.text for row in query.message.reply_markup.inline_keyboard for btn in row if btn.callback_data == query.data)
    context.user_data['goal'] = button_text
    await query.edit_message_text(text=f"✅ هدف: {button_text}")
    await context.bot.send_message(chat_id=update.effective_chat.id, text="۳/۴ - مخاطب هدف شما چه کسانی هستند؟\n(مثال: دانشجویان، مادران جوان)")
    return AUDIENCE

async def get_audience(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['audience'] = update.message.text
    keyboard = [
        [InlineKeyboardButton("صمیمی و دوستانه", callback_data='tone_friendly'), InlineKeyboardButton("رسمی و معتبر", callback_data='tone_formal')],
        [InlineKeyboardButton("انرژی‌بخش", callback_data='tone_energetic'), InlineKeyboardButton("شوخ و طنز", callback_data='tone_humorous')],
        [InlineKeyboardButton("آموزشی و تخصصی", callback_data='tone_educational')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("۴/۴ - لحن برند شما کدام است؟", reply_markup=reply_markup)
    return TONE

async def get_tone_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    button_text = next(btn.text for row in query.message.reply_markup.inline_keyboard for btn in row if btn.callback_data == query.data)
    context.user_data['tone'] = button_text
    await query.edit_message_text(text=f"✅ لحن: {button_text}")
    user_id = str(update.effective_user.id)
    profile_data = {
        'user_id': user_id,
        'business': context.user_data.get('business'),
        'goal': context.user_data.get('goal'),
        'audience': context.user_data.get('audience'),
        'tone': context.user_data.get('tone')
    }
    try:
        supabase.table('profiles').upsert(profile_data, on_conflict='user_id').execute()
        log_event(user_id, 'profile_saved_inline')
        await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ پروفایل شما ذخیره شد!")
    except Exception as e:
        logger.error(f"Supabase upsert Error: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطا در ذخیره پروفایل: {e}")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    log_event(update.effective_user.id, 'profile_cancel')
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text="عملیات لغو شد.")
    else:
        await update.message.reply_text("عملیات لغو شد.")
    return ConversationHandler.END

# ---------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_event(update.effective_user.id, 'start_command')
    await update.message.reply_text("سلام! 👋 برای ساخت پروفایل /profile را بزنید.")

async def generate_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    try:
        response = supabase.table('profiles').select("*").eq('user_id', user_id).execute()
        if not response.data:
            await update.message.reply_text("❌ اول باید پروفایلت رو با دستور /profile بسازی.")
            return
        user_profile = response.data[0]
        user_profile['goal'] = user_profile.get('goal', 'نامشخص')
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در خواندن پروفایل: {e}")
        return

    user_text = update.message.text
    wait_msg = await update.message.reply_text("⏳ در حال طراحی سناریو...")

    try:
        prompt = f"""
        **شخصیت:** تو یک متخصص تولید محتوای خلاق ایرانی برای اینستاگرام هستی.
        **ماموریت:** نوشتن یک نقشه ساخت کامل برای ریلز، فقط و فقط به زبان فارسی.

        **اطلاعات کاربر:**
        - کسب‌وکار: {user_profile['business']}
        - هدف: {user_profile['goal']}
        - مخاطب: {user_profile['audience']}
        - لحن: {user_profile['tone']}
        - موضوع: "{user_text}"

        ---
        **مراحل اجرا:**

        **۱. فیلتر:** اگر موضوع کاملا بی‌ربط بود (مثال: کسب‌وکار «یوگا» و موضوع «قیمت خودرو»)، فقط این جمله را بنویس: `موضوع «{user_text}» با پروفایل شما ارتباطی ندارد.`

        **۲. ایده‌پردازی:** در غیر این صورت، یک سناریوی کامل با ساختار زیر بنویس.

        **ساختار نقشه ساخت:**
        ### 🎬 نقشه ساخت ریلز: [عنوان جذاب و فارسی]

        ۱. قلاب (۰-۳ ثانیه):
        - تصویر: (شرح صحنه اول)
        - متن روی صفحه: (جمله کنجکاوکننده)

        ۲. بدنه اصلی (۴-۲۰ ثانیه):
        - تصویر: (شرح سکانس‌های اصلی)
        - گفتار: (متن صحبت‌ها)

        ۳. فراخوان به اقدام (۲۱-۳۰ ثانیه):
        - تصویر: (شرح صحنه پایانی)
        - متن روی صفحه: (درخواست واضح از مخاطب)
        
        ---
        ### ✍️ کپشن و هشتگ‌ها
        - کپشن: (کپشن جذاب و فارسی)
        - هشتگ‌ها: (۵ تا ۷ هشتگ فارسی)
        ---
        **قانون نهایی و بسیار مهم:**
        هرگز و تحت هیچ شرایطی از کاراکتر `*` برای بولد کردن متن استفاده نکن. کل پاسخ باید متن ساده و بدون هیچ‌گونه قالب‌بندی بولد باشد. این مهم‌ترین قانون است.
        """
        
        response = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
        ai_reply = response.choices[0].message.content.strip()
        
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
        
        is_rejection = ai_reply.startswith(f"موضوع «{user_text}»")
        message_to_send = f"توجه:\n{ai_reply}" if is_rejection else ai_reply

        # --- محافظ نهایی کد ---
        # اگر به هر دلیلی هوش مصنوعی دوباره از * استفاده کرد، آن را حذف می‌کنیم
        if message_to_send.count('*') > 0:
            logger.warning("AI violated the 'no-asterisk' rule. Sanitizing output.")
            message_to_send = message_to_send.replace('*', '')

        try:
            # حالا با parse_mode غیرفعال یا حذف شده ارسال می‌کنیم چون قالب‌بندی نداریم
            await update.message.reply_text(message_to_send)
            if not is_rejection: log_event(user_id, 'content_generated_final', user_text)
        except BadRequest as e: # این بخش حالا یک لایه امنیتی اضافه است
            log_event(user_id, 'final_fallback_error', user_text)
            logger.error(f"A very unexpected error occurred: {e}")
            await update.message.reply_text("یک خطای غیرمنتظره در ارسال پیام رخ داد. متن خام پاسخ:\n\n" + ai_reply)
        
        if is_rejection: log_event(user_id, 'topic_rejected_final', user_text)

    except Exception as e:
        log_event(user_id, 'general_error_final', str(e))
        logger.error(f"Error in generate_content: {e}")
        try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
        except Exception: pass
        await update.message.reply_text(f"❌ ببخشید، در پردازش درخواست شما مشکلی پیش آمد: {e}")

# ---------------------------------------------
if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('profile', profile_start)],
        states={
            BUSINESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_business)],
            GOAL: [CallbackQueryHandler(get_goal, pattern='^goal_')],
            AUDIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_audience)],
            TONE: [CallbackQueryHandler(get_tone_and_save, pattern='^tone_')],
        },
        fallbacks=[CommandHandler('cancel', cancel_profile), CallbackQueryHandler(cancel_profile, pattern='^cancel$')],
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), generate_content))
    
    print("🤖 BOT DEPLOYED WITH CODE-BASED SANITIZER!")
    application.run_polling()
