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

# --- مراحل جدید مکالمه پروفایل با دکمه‌های Inline ---
BUSINESS, GOAL, AUDIENCE, TONE = range(4)

async def profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    log_event(update.effective_user.id, 'profile_start')
    await update.message.reply_text(
        "خب، بیا پروفایل کسب‌وکارت رو بسازیم.\n\n"
        "**۱/۴ - موضوع اصلی پیج شما چیست؟**\n"
        "(مثال: فروش آنلاین قهوه، آموزش یوگا، کلینیک روانشناسی)",
        parse_mode='Markdown'
    )
    return BUSINESS

async def get_business(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['business'] = update.message.text
    keyboard = [
        [InlineKeyboardButton("افزایش فروش", callback_data='goal_sales'), InlineKeyboardButton("آگاهی از برند", callback_data='goal_awareness')],
        [InlineKeyboardButton("آموزش به مخاطب", callback_data='goal_education'), InlineKeyboardButton("سرگرمی و کامیونیتی", callback_data='goal_community')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "عالی!\n\n"
        "**۲/۴ - هدف اصلی شما از تولید محتوا چیست؟**",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return GOAL

async def get_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer() # برای متوقف کردن انیمیشن لودینگ روی دکمه
    
    # ذخیره متن دکمه، نه callback_data
    button_text = next(btn.text for row in query.message.reply_markup.inline_keyboard for btn in row if btn.callback_data == query.data)
    context.user_data['goal'] = button_text
    
    # ویرایش پیام قبلی برای نشان دادن انتخاب
    await query.edit_message_text(text=f"✅ هدف شما: {button_text}")

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="بسیار خب.\n\n"
             "**۳/۴ - مخاطب هدف شما چه کسانی هستند؟**\n"
             "(مثال: دانشجویان، مادران جوان، مدیران کسب‌وکار)",
        parse_mode='Markdown'
    )
    return AUDIENCE

async def get_audience(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['audience'] = update.message.text
    keyboard = [
        [InlineKeyboardButton("صمیمی و دوستانه", callback_data='tone_friendly'), InlineKeyboardButton("رسمی و معتبر", callback_data='tone_formal')],
        [InlineKeyboardButton("انرژی‌بخش و انگیزشی", callback_data='tone_energetic'), InlineKeyboardButton("شوخ و طنز", callback_data='tone_humorous')],
        [InlineKeyboardButton("آموزشی و تخصصی", callback_data='tone_educational')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "و در آخر...\n\n"
        "**۴/۴ - لحن برند شما کدام است؟**",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return TONE

async def get_tone_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    button_text = next(btn.text for row in query.message.reply_markup.inline_keyboard for btn in row if btn.callback_data == query.data)
    context.user_data['tone'] = button_text
    
    await query.edit_message_text(text=f"✅ لحن شما: {button_text}")
    
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
        await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ پروفایل شما با موفقیت ذخیره/آپدیت شد!")
    except Exception as e:
        logger.error(f"Supabase upsert Error: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطا در ذخیره پروفایل: {e}")
        
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    log_event(update.effective_user.id, 'profile_cancel')
    context.user_data.clear()
    # اگر کاربر وسط کار با دکمه‌ها لغو کرد، باید پیام را ویرایش کنیم
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text="عملیات ساخت پروفایل لغو شد.")
    else:
        await update.message.reply_text("عملیات ساخت پروفایل لغو شد.")
    return ConversationHandler.END

# ---------------------------------------------

# --- دستورات اصلی ربات ---
# (توابع start و generate_content بدون تغییر باقی می‌مانند)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_event(update.effective_user.id, 'start_command')
    await update.message.reply_text("سلام! 👋\nبرای ساخت/ویرایش پروفایل، دستور /profile رو بزن.\nبعد از اون، هر موضوعی بفرستی، بر اساس پروفایلت برات سناریو ریلز می‌سازم.")

async def generate_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    try:
        response = supabase.table('profiles').select("*").eq('user_id', user_id).execute()
        if not response.data:
            await update.message.reply_text("❌ اول باید پروفایلت رو بسازی! لطفاً دستور /profile رو بزن.")
            return
        user_profile = response.data[0]
        if 'goal' not in user_profile or user_profile['goal'] is None:
             user_profile['goal'] = 'نامشخص'

    except Exception as e:
        await update.message.reply_text(f"❌ خطا در خواندن پروفایل از دیتابیس: {e}")
        return

    user_text = update.message.text
    wait_msg = await update.message.reply_text("⏳ در حال بررسی موضوع و طراحی سناریو...")

    try:
        prompt = f"""
        **Your Primary Task:**
        You are a viral content strategist. Create a professional Instagram Reel blueprint based on the user's profile.

        **User's Profile:**
        - **Business:** {user_profile['business']}
        - **Content Goal:** {user_profile['goal']}
        - **Audience:** {user_profile['audience']}
        - **Tone:** {user_profile['tone']}
        - **Today's Topic:** "{user_text}"

        ---
        **CRITICAL RULES:**
        1.  **Relevance First:** If the topic is completely irrelevant, reply ONLY with this exact Persian sentence:
            `موضوع «{user_text}» با پروفایل کسب‌وکار شما ارتباطی ندارد. لطفاً یک موضوع مرتبط ارائه دهید.`
        2.  **Markdown Quality Control:** Ensure your Markdown syntax is 100% perfect.

        ---
        **Blueprint Structure (if relevant):**
        (The blueprint's CTA should reflect the 'Content Goal'. A 'sales' goal needs a stronger CTA than a 'community' goal.)
        ### 🎬 Viral Reel Blueprint: [Engaging Title]
        **1. ATTENTION (0-3s): Hook** (*Visual:* ..., *On-Screen Text:* ...)
        **2. INTEREST (4-10s): Problem/Value** (*Visual:* ..., *Narration:* ...)
        **3. DESIRE (11-20s): Solution** (*Visual:* ..., *Narration:* ...)
        **4. ACTION (21-30s): CTA** (*Visual:* ..., *On-Screen Text:* ...)
        ---
        ### ✍️ Caption & Hashtags
        **Caption:** ...
        **Hashtags:** ...
        """
        
        response = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
        ai_reply = response.choices[0].message.content.strip()
        
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
        
        is_rejection = ai_reply.startswith(f"موضوع «{user_text}»")
        message_to_send = f"**توجه:**\n{ai_reply}" if is_rejection else ai_reply

        try:
            await update.message.reply_text(message_to_send, parse_mode='Markdown')
            if not is_rejection: log_event(user_id, 'content_generated_success', user_text)
        except BadRequest as e:
            if "Can't parse entities" in str(e):
                log_event(user_id, 'markdown_error', user_text)
                logger.error(f"Markdown parse error: {e}")
                fallback_text = "⚠️ هوش مصنوعی یک پاسخ با فرمت نوشتاری اشتباه تولید کرد. متن خام پاسخ:\n\n" + ai_reply
                await update.message.reply_text(fallback_text)
            else: raise e
        
        if is_rejection: log_event(user_id, 'topic_rejected', user_text)

    except Exception as e:
        log_event(user_id, 'general_error', str(e))
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
        fallbacks=[
            CommandHandler('cancel', cancel_profile),
            CallbackQueryHandler(cancel_profile, pattern='^cancel$')
        ],
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), generate_content))
    
    print("🤖 BOT DEPLOYED WITH INLINE KEYBOARD PROFILE!")
    application.run_polling()
                         
