import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from openai import OpenAI
from supabase import create_client, Client
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
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
# --- تابع ثبت آمار ---
def log_event(user_id: str, event_type: str, content: str = ""):
    if not supabase: return
    try:
        data_to_insert = {'user_id': str(user_id), 'event_type': event_type, 'content': content}
        supabase.table('logs').insert(data_to_insert).execute()
    except Exception as e:
        logger.error(f"Supabase log event error: {e}")

# ---------------------------------------------

# --- مراحل جدید مکالمه پروفایل با دکمه ---
BUSINESS, GOAL, AUDIENCE, TONE = range(4)

# دکمه‌های مربوط به مراحل
goal_keyboard = [
    ["افزایش فروش محصول/خدمات", "افزایش آگاهی از برند"],
    ["آموزش و ارائه ارزش به مخاطب", "سرگرمی و ساخت کامیونیتی"],
]
goal_markup = ReplyKeyboardMarkup(goal_keyboard, one_time_keyboard=True, resize_keyboard=True)

tone_keyboard = [
    ["صمیمی و دوستانه", "رسمی و معتبر"],
    ["انرژی‌بخش و انگیزشی", "شوخ و طنز"],
    ["آموزشی و تخصصی"],
]
tone_markup = ReplyKeyboardMarkup(tone_keyboard, one_time_keyboard=True, resize_keyboard=True)


async def profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع فرآیند ساخت پروفایل."""
    log_event(update.effective_user.id, 'profile_start')
    await update.message.reply_text(
        "خب، بیا پروفایل کسب‌وکارت رو بسازیم.\n\n"
        "**۱/۴ - موضوع اصلی پیج شما چیست؟**\n"
        "(مثال: فروش آنلاین قهوه، آموزش یوگا، کلینیک روانشناسی)",
        parse_mode='Markdown'
    )
    return BUSINESS

async def get_business(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت موضوع کسب‌وکار و پرسیدن هدف."""
    context.user_data['business'] = update.message.text
    await update.message.reply_text(
        "عالی!\n\n"
        "**۲/۴ - هدف اصلی شما از تولید محتوا چیست؟**\n"
        "(انتخاب این گزینه به من کمک می‌کند تا سناریوهایی بنویسم که شما را به هدفتان برساند)",
        reply_markup=goal_markup,
        parse_mode='Markdown'
    )
    return GOAL

async def get_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت هدف و پرسیدن مخاطب."""
    context.user_data['goal'] = update.message.text
    await update.message.reply_text(
        "بسیار خب.\n\n"
        "**۳/۴ - مخاطب هدف شما چه کسانی هستند؟**\n"
        "(هرچه دقیق‌تر توصیف کنی، من محتوای بهتری برایشان می‌سازم. مثال: دانشجویان، مادران جوان، مدیران کسب‌وکار)",
        reply_markup=ReplyKeyboardRemove(), # حذف دکمه‌های قبلی
        parse_mode='Markdown'
    )
    return AUDIENCE

async def get_audience(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت مخاطب و پرسیدن لحن."""
    context.user_data['audience'] = update.message.text
    await update.message.reply_text(
        "و در آخر...\n\n"
        "**۴/۴ - لحن برند شما کدام است؟**",
        reply_markup=tone_markup,
        parse_mode='Markdown'
    )
    return TONE

async def get_tone_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت لحن و ذخیره نهایی پروفایل."""
    context.user_data['tone'] = update.message.text
    user_id = str(update.effective_user.id)
    
    # ساخت دیکشنری کامل پروفایل
    profile_data = {
        'user_id': user_id,
        'business': context.user_data['business'],
        'goal': context.user_data['goal'],
        'audience': context.user_data['audience'],
        'tone': context.user_data['tone']
    }
    
    try:
        supabase.table('profiles').upsert(profile_data, on_conflict='user_id').execute()
        log_event(user_id, 'profile_saved')
        await update.message.reply_text(
            "✅ پروفایل شما با موفقیت ذخیره/آپدیت شد!\n"
            "از الان به بعد، هر موضوعی بفرستی، بر اساس این پروفایل جدید برات سناریو می‌سازم.",
            reply_markup=ReplyKeyboardRemove()
        )
    except Exception as e:
        logger.error(f"Supabase upsert Error: {e}")
        await update.message.reply_text(f"❌ خطا در ذخیره پروفایل: {e}", reply_markup=ReplyKeyboardRemove())
        
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """لغو فرآیند ساخت پروفایل."""
    log_event(update.effective_user.id, 'profile_cancel')
    context.user_data.clear()
    await update.message.reply_text(
        "عملیات ساخت پروفایل لغو شد.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# ---------------------------------------------

# --- دستورات اصلی ربات ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_event(update.effective_user.id, 'start_command')
    await update.message.reply_text("سلام! 👋\nبرای ساخت/ویرایش پروفایل، دستور /profile رو بزن.\nبعد از اون، هر موضوعی بفرستی، بر اساس پروفایلت برات سناریو ریلز می‌سازم.")

async def generate_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    try:
        # حالا باید ستون goal را هم از دیتابیس بخوانیم
        response = supabase.table('profiles').select("*, goal").eq('user_id', user_id).execute()
        if not response.data:
            await update.message.reply_text("❌ اول باید پروفایلت رو بسازی! لطفاً دستور /profile رو بزن.")
            return
        user_profile = response.data[0]
        # اطمینان از وجود داشتن کلید goal
        if 'goal' not in user_profile:
             user_profile['goal'] = 'نامشخص' # مقدار پیش‌فرض برای پروفایل‌های قدیمی

    except Exception as e:
        await update.message.reply_text(f"❌ خطا در خواندن پروفایل از دیتابیس: {e}")
        return

    user_text = update.message.text
    wait_msg = await update.message.reply_text("⏳ در حال بررسی موضوع و طراحی سناریو...")

    try:
        # --- پرامپت نهایی با فیلد جدید 'goal' ---
        prompt = f"""
        **Your Primary Task:**
        You are a viral content strategist. Your job is to create a professional Instagram Reel blueprint for the user's topic, based on their profile.

        **User's Profile:**
        - **Business:** {user_profile['business']}
        - **Content Goal:** {user_profile['goal']}
        - **Audience:** {user_profile['audience']}
        - **Tone:** {user_profile['tone']}
        - **Today's Topic:** "{user_text}"

        ---
        **CRITICAL RULES:**
        1.  **Relevance First:** Use common sense. If and ONLY IF the topic is completely irrelevant to the business, reply ONLY with this exact Persian sentence:
            `موضوع «{user_text}» با پروفایل کسب‌وکار شما ارتباطی ندارد. لطفاً یک موضوع مرتبط ارائه دهید.`
        2.  **Markdown Quality Control:** You MUST double-check your response to ensure your Markdown syntax is 100% perfect.

        ---
        **Blueprint Structure (if relevant):**
        (The blueprint structure should be created with the user's 'Content Goal' in mind. For example, a 'sales' goal needs a stronger CTA.)
        ### 🎬 Viral Reel Blueprint: [Engaging Title]
        **1. ATTENTION (0-3s): Hook**
        *   **Visual:** [Describe the first shot]
        *   **On-Screen Text:** [A powerful sentence]
        **2. INTEREST (4-10s): Problem/Value**
        *   **Visual:** [Describe the shots]
        *   **Narration:** [Explain the core idea]
        **3. DESIRE (11-20s): Solution**
        *   **Visual:** [Show the "aha!" moment]
        *   **Narration:** [Explain the benefit]
        **4. ACTION (21-30s): CTA**
        *   **Visual:** [Final satisfying shot]
        *   **On-Screen Text:** [e.g., "Save for later!"]
        ---
        ### ✍️ Caption & Hashtags
        **Caption:** [Write an engaging caption]
        **Hashtags:** [Provide 5-7 hashtags]
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
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
        except Exception as delete_error:
            logger.error(f"Could not delete wait message: {delete_error}")
        
        await update.message.reply_text(f"❌ ببخشید، در پردازش درخواست شما مشکلی پیش آمد: {e}")


if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # تعریف ConversationHandler جدید
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('profile', profile_start)],
        states={
            BUSINESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_business)],
            GOAL: [MessageHandler(filters.Regex(f'^({"|".join(sum(goal_keyboard, []))})$'), get_goal)],
            AUDIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_audience)],
            TONE: [MessageHandler(filters.Regex(f'^({"|".join(sum(tone_keyboard, []))})$'), get_tone_and_save)],
        },
        fallbacks=[CommandHandler('cancel', cancel_profile)],
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), generate_content))
    
    print("🤖 BOT DEPLOYED WITH BUTTON-BASED PROFILE CREATION!")
    application.run_polling()
        
