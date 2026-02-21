import os
import logging
import threading
import json
from http.server import HTTPServer, BaseHTTPRequestHandler

from openai import OpenAI
from supabase import create_client, Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler,
    filters, ConversationHandler, CallbackQueryHandler
)

# --- تنظیمات لاگ ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- دریافت توکن‌ها ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# --- سرور وب برای بیدار نگه داشتن Render ---
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

# --- تابع بررسی سلامت سرویس‌ها ---
async def check_services(update: Update) -> bool:
    """بررسی می‌کند که آیا دیتابیس و هوش مصنوعی متصل هستند یا خیر."""
    if not supabase or not client:
        await update.message.reply_text("❌ سیستم در حال حاضر با مشکل ارتباطی (دیتابیس یا هوش مصنوعی) روبروست. لطفاً بعداً تلاش کنید.")
        return False
    return True

# --- تابع ثبت آمار ---
def log_event(user_id: str, event_type: str, content: str = ""):
    if not supabase: return
    try:
        data_to_insert = {'user_id': str(user_id), 'event_type': event_type, 'content': content}
        supabase.table('logs').insert(data_to_insert).execute()
    except Exception as e:
        logger.error(f"Supabase log event error: {e}")

# ---------------------------------------------

# --- دستور /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_event(update.effective_user.id, 'start_command')
    welcome_message = (
        "سلام! 👋 به دستیار هوشمند تولید محتوای اینستاگرام خوش آمدید.\n\n"
        "🛠 **قدم اول:** برای اینکه بتوانم بهترین سناریوها را برای شما بنویسم، ابتدا باید پروفایل کسب‌وکار خود را بسازید. لطفاً روی دستور /profile کلیک کنید.\n\n"
        "✍️ **قدم دوم:** بعد از ساخت پروفایل، کافیست هر موضوعی که برای ریلز در نظر دارید را در اینجا تایپ کنید تا من برای آن ایده‌پردازی کنم!"
    )
    await update.message.reply_text(welcome_message)

# ---------------------------------------------

# --- مراحل مکالمه پروفایل ---
P_BUSINESS, P_GOAL, P_AUDIENCE, P_TONE = range(4)

async def profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_services(update): return ConversationHandler.END
    log_event(update.effective_user.id, 'profile_start')
    await update.message.reply_text("۱/۴ - موضوع اصلی پیج شما چیست؟\n(مثال: فروش آنلاین قهوه، آموزش یوگا، کلینیک دندانپزشکی)")
    return P_BUSINESS

async def get_business(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['business'] = update.message.text
    keyboard = [
        [InlineKeyboardButton("افزایش فروش", callback_data='goal_sales'), InlineKeyboardButton("آگاهی از برند", callback_data='goal_awareness')],
        [InlineKeyboardButton("آموزش به مخاطب", callback_data='goal_education'), InlineKeyboardButton("سرگرمی و کامیونیتی", callback_data='goal_community')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("۲/۴ - هدف اصلی شما از تولید محتوا چیست؟", reply_markup=reply_markup)
    return P_GOAL

async def get_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    button_text = next(btn.text for row in query.message.reply_markup.inline_keyboard for btn in row if btn.callback_data == query.data)
    context.user_data['goal'] = button_text
    await query.edit_message_text(text=f"✅ هدف: {button_text}")
    await context.bot.send_message(chat_id=update.effective_chat.id, text="۳/۴ - مخاطب هدف شما چه کسانی هستند؟\n(مثال: دانشجویان، مادران جوان، صاحبان کسب‌وکار)")
    return P_AUDIENCE

async def get_audience(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['audience'] = update.message.text
    keyboard = [
        [InlineKeyboardButton("صمیمی و دوستانه", callback_data='tone_friendly'), InlineKeyboardButton("رسمی و معتبر", callback_data='tone_formal')],
        [InlineKeyboardButton("انرژی‌بخش و انگیزشی", callback_data='tone_energetic'), InlineKeyboardButton("شوخ و طنز", callback_data='tone_humorous')],
        [InlineKeyboardButton("آموزشی و تخصصی", callback_data='tone_educational')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("۴/۴ - لحن برند شما کدام است؟", reply_markup=reply_markup)
    return P_TONE

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
    
    # ارسال وضعیت در حال تایپ
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    
    try:
        supabase.table('profiles').upsert(profile_data, on_conflict='user_id').execute()
        log_event(user_id, 'profile_saved')
        await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ پروفایل شما با موفقیت ذخیره شد!\nحالا می‌توانید موضوع ریلز خود را تایپ کنید.")
    except Exception as e:
        logger.error(f"Supabase upsert Error: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطا در ذخیره اطلاعات در دیتابیس.")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    log_event(update.effective_user.id, 'profile_cancel')
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text="عملیات ساخت پروفایل لغو شد.")
    else:
        await update.message.reply_text("عملیات ساخت پروفایل لغو شد.")
    return ConversationHandler.END

# ---------------------------------------------
# --- مراحل مکالمه تولید محتوا (ایده‌پردازی و سناریو) ---
IDEAS, EXPAND = range(4, 6)

async def check_profile_before_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_services(update): return ConversationHandler.END
    user_id = str(update.effective_user.id)
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    
    try:
        response = supabase.table('profiles').select("*").eq('user_id', user_id).execute()
        if not response.data:
            await update.message.reply_text("❌ اول باید پروفایلت رو با دستور /profile بسازی.")
            return ConversationHandler.END
        
        context.user_data['profile'] = response.data[0]
        context.user_data['topic'] = update.message.text
        return await generate_ideas(update, context)
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در خواندن اطلاعات از دیتابیس.")
        logger.error(f"Database read error: {e}")
        return ConversationHandler.END

async def generate_ideas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_profile = context.user_data['profile']
    topic = context.user_data['topic']
    wait_msg = await update.message.reply_text("⏳ در حال ایده‌پردازی و طوفان فکری...")
    
    # ارسال وضعیت در حال تایپ
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    try:
        prompt_ideation = f"""
        **شخصیت:** تو یک ایده‌پرداز خلاق برای اینستاگرام هستی.
        **ماموریت:** برای «موضوع» زیر، سه ایده کاملاً متفاوت و جذاب برای یک ریلز پیشنهاد بده.
        
        - **کسب‌وکار:** {user_profile['business']}
        - **موضوع:** "{topic}"

        **ساختار خروجی (بسیار مهم):**
        خروجی تو باید یک آبجکت JSON باشد که یک کلید به نام "ideas" دارد و مقدار آن یک لیست از سه ایده است. هر ایده دو کلید "title" و "hook" دارد.
        مثال دقیق خروجی:
        {{
            "ideas": [
                {{"title": "ایده ۱: زاویه دید آموزشی", "hook": "آیا می‌دانستید...؟"}},
                {{"title": "ایده ۲: زاویه دید داستانی", "hook": "روزی که فهمیدم..."}},
                {{"title": "ایده ۳: زاویه دید طنز", "hook": "وقتی می‌فهمی..."}}
            ]
        }}
        
        **قانون:** فقط و فقط همین ساختار JSON را خروجی بده.
        """
        response = client.chat.completions.create(
            model="gpt-4o", 
            response_format={"type": "json_object"}, 
            messages=[{"role": "user", "content": prompt_ideation}]
        )
        
        # استخراج اصولی JSON بر اساس ساختار جدید
        response_data = json.loads(response.choices[0].message.content)
        ideas_json = response_data.get("ideas", [])
        
        if not ideas_json or len(ideas_json) == 0:
            raise ValueError("لیست ایده‌ها در JSON خالی است.")

        context.user_data['ideas'] = ideas_json
        
        keyboard = []
        for i, idea in enumerate(ideas_json):
            button = InlineKeyboardButton(f"🎬 ساخت سناریوی ایده {i+1}", callback_data=f'expand_{i}')
            keyboard.append([button])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message_text = f"عالی! برای موضوع «{topic}»، سه ایده متفاوت پیدا کردم:\n\n"
        for i, idea in enumerate(ideas_json):
            message_text += f"**{idea['title']}**\n- قلاب: «{idea['hook']}»\n\n"
        message_text += "کدام یک را برایت به سناریوی کامل تبدیل کنم؟"
        
        await wait_msg.edit_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')
        log_event(str(update.effective_user.id), 'ideas_generated', topic)
        return EXPAND

    except Exception as e:
        log_event(str(update.effective_user.id), 'ideation_error', str(e))
        logger.error(f"Error in generate_ideas: {e}")
        await wait_msg.edit_text(f"❌ ببخشید، در مرحله ایده‌پردازی مشکلی پیش آمد.")
        return ConversationHandler.END

async def expand_idea(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    idea_index = int(query.data.split('_')[1])
    chosen_idea = context.user_data['ideas'][idea_index]
    user_profile = context.user_data['profile']
    
    await query.edit_message_text(f"✅ انتخاب شما: «{chosen_idea['title']}»\n⏳ در حال نوشتن سناریوی کامل...")
    
    # ارسال وضعیت در حال تایپ
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    try:
        prompt_expansion = f"""
        **شخصیت:** تو یک متخصص تولید محتوای ایرانی هستی.
        **ماموریت:** بر اساس ایده انتخابی، یک نقشه ساخت کامل برای ریلز بنویس.

        **اطلاعات پایه:**
        - کسب‌وکار: {user_profile['business']}
        - هدف: {user_profile.get('goal', 'نامشخص')}
        - مخاطب: {user_profile['audience']}
        - لحن: {user_profile['tone']}
        - **ایده انتخابی:** (عنوان: {chosen_idea['title']}, قلاب: {chosen_idea['hook']})

        ---
        **فیلتر ارتباط:**
        اگر موضوع انتخابی هیچ ارتباط منطقی و تجاری با کسب‌وکار نداشت، فقط بنویس:
        `موضوع با پروفایل شما ارتباطی ندارد.`

        ---
        **ساختار نقشه ساخت (در صورت مرتبط بودن - فقط فارسی):**
        ### 🎬 نقشه ساخت ریلز: {chosen_idea['title']}

        **۱. قلاب (۰-۳ ثانیه):**
        - **تصویر:** (شرح صحنه اول)
        - **متن روی صفحه:** «{chosen_idea['hook']}»

        **۲. بدنه اصلی (۴-۲۰ ثانیه):**
        - **تصویر:** (شرح سکانس‌ها)
        - **گفتار:** (متن صحبت‌ها)

        **۳. فراخوان به اقدام (۲۱-۳۰ ثانیه):**
        - **تصویر:** (شرح صحنه پایانی)
        - **متن روی صفحه:** (درخواست واضح از مخاطب)
        
        ---
        ### ✍️ کپشن و هشتگ‌ها
        - **کپشن:** (کپشن جذاب فارسی)
        - **هشتگ‌ها:** (۵ تا ۷ هشتگ فارسی)
        """
        response = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt_expansion}])
        ai_reply = response.choices[0].message.content.strip()

        is_rejection = ai_reply.startswith("موضوع با پروفایل")
        message_to_send = f"⚠️ توجه:\n{ai_reply}" if is_rejection else ai_reply

        try:
            # حالا با خیال راحت از Markdown استفاده می‌کنیم
            await context.bot.send_message(chat_id=update.effective_chat.id, text=message_to_send, parse_mode='Markdown')
            if not is_rejection: log_event(str(update.effective_user.id), 'expansion_success', chosen_idea['title'])
        except BadRequest as e:
            # اگر خطای Markdown رخ داد، به صورت متن ساده می‌فرستیم
            logger.warning(f"Markdown parsing failed, sending as plain text. Error: {e}")
            await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ خطا در قالب‌بندی نمایش. متن خام:\n\n" + message_to_send)
            
    except Exception as e:
        log_event(str(update.effective_user.id), 'expansion_error', str(e))
        logger.error(f"Error in expand_idea: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ در نوشتن سناریوی کامل مشکلی پیش آمد.")

    context.user_data.clear()
    return ConversationHandler.END


# ---------------------------------------------
if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    profile_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('profile', profile_start)],
        states={
            P_BUSINESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_business)],
            P_GOAL: [CallbackQueryHandler(get_goal, pattern='^goal_')],
            P_AUDIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_audience)],
            P_TONE: [CallbackQueryHandler(get_tone_and_save, pattern='^tone_')],
        },
        fallbacks=[CommandHandler('cancel', cancel_profile), CallbackQueryHandler(cancel_profile, pattern='^cancel$')],
    )

    content_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, check_profile_before_content)],
        states={
            EXPAND: [CallbackQueryHandler(expand_idea, pattern='^expand_')],
        },
        fallbacks=[CommandHandler('cancel', cancel_profile), CallbackQueryHandler(cancel_profile, pattern='^cancel$')],
    )
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(profile_conv_handler)
    application.add_handler(content_conv_handler)
    
    print("🤖 BOT DEPLOYED WITH ALL AI-SUGGESTED BEST PRACTICES!")
    application.run_polling()
    
