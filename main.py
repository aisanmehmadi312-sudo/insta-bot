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
    if not supabase or not client:
        await update.message.reply_text("❌ سیستم در حال حاضر با مشکل ارتباطی روبروست. لطفاً بعداً تلاش کنید.")
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
        "🛠 **پروفایل:** ابتدا با /profile پروفایل کسب‌وکارتان را بسازید.\n\n"
        "✍️ **سناریونویسی:** هر زمان موضوعی داشتید، فقط آن را تایپ کنید تا برایتان ایده بسازم.\n\n"
        "🏷 **هشتگ‌ساز:** برای دریافت هشتگ، از /hashtags استفاده کنید.\n\n"
        "🧠 **مربی ایده:** اگر خودت ایده‌ای نوشتی و میخوای بررسیش کنم، روی /coach کلیک کن."
    )
    await update.message.reply_text(welcome_message)

# ---------------------------------------------
# --- 1. مراحل مکالمه پروفایل ---
P_BUSINESS, P_GOAL, P_AUDIENCE, P_TONE = range(4)

async def profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_services(update): return ConversationHandler.END
    log_event(update.effective_user.id, 'profile_start')
    await update.message.reply_text("۱/۴ - موضوع اصلی پیج شما چیست؟\n(مثال: فروش آنلاین قهوه، آموزش یوگا)")
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
    await context.bot.send_message(chat_id=update.effective_chat.id, text="۳/۴ - مخاطب هدف شما چه کسانی هستند؟\n(مثال: دانشجویان، مادران جوان)")
    return P_AUDIENCE

async def get_audience(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['audience'] = update.message.text
    keyboard = [
        [InlineKeyboardButton("صمیمی و دوستانه", callback_data='tone_friendly'), InlineKeyboardButton("رسمی و معتبر", callback_data='tone_formal')],
        [InlineKeyboardButton("انرژی‌بخش", callback_data='tone_energetic'), InlineKeyboardButton("شوخ و طنز", callback_data='tone_humorous')],
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
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    
    try:
        supabase.table('profiles').upsert(profile_data, on_conflict='user_id').execute()
        log_event(user_id, 'profile_saved')
        await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ پروفایل شما ذخیره شد!\nحالا می‌توانید موضوع ریلز را تایپ کنید.")
    except Exception as e:
        logger.error(f"Supabase upsert Error: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطا در ذخیره دیتابیس.")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    log_event(update.effective_user.id, 'action_canceled')
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text="عملیات لغو شد.")
    else:
        await update.message.reply_text("عملیات لغو شد.")
    return ConversationHandler.END


# ---------------------------------------------
# --- 2. قابلیت جدید 1: هشتگ‌های هوشمند (/hashtags) ---
H_TOPIC = 5

async def hashtag_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_services(update): return ConversationHandler.END
    log_event(update.effective_user.id, 'hashtag_start')
    await update.message.reply_text(
        "🏷 **به ابزار هشتگ‌ساز هوشمند خوش آمدید!**\n\n"
        "لطفاً موضوع پست یا ریلز خود را بنویسید تا بهترین هشتگ‌ها را بر اساس پروفایلتان تولید کنم:"
    )
    return H_TOPIC

async def hashtag_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = str(update.effective_user.id)
    topic = update.message.text
    
    try:
        response = supabase.table('profiles').select("*").eq('user_id', user_id).execute()
        if not response.data:
            await update.message.reply_text("❌ اول باید پروفایلت رو با دستور /profile بسازی.")
            return ConversationHandler.END
        user_profile = response.data[0]
    except Exception as e:
        await update.message.reply_text("❌ خطا در خواندن اطلاعات از دیتابیس.")
        return ConversationHandler.END

    wait_msg = await update.message.reply_text("⏳ در حال استخراج و تحلیل بهترین هشتگ‌ها...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    try:
        prompt = f"""
        **شخصیت:** تو یک استراتژیست شبکه‌های اجتماعی ایرانی هستی.
        **ماموریت:** بر اساس پروفایل کسب‌وکار و موضوع پست کاربر، سه دسته هشتگ حرفه‌ای و کاملاً فارسی تولید کن.
        
        **اطلاعات کاربر:**
        - کسب‌وکار: {user_profile['business']}
        - مخاطب: {user_profile['audience']}
        - موضوع پست: "{topic}"

        **ساختار خروجی:**
        🎯 هشتگ‌های پربازدید:
        #هشتگ۱ #هشتگ۲ #هشتگ۳ #هشتگ۴ #هشتگ۵

        🔬 هشتگ‌های تخصصی:
        #هشتگ۱ #هشتگ۲ #هشتگ۳ #هشتگ۴ #هشتگ۵

        🤝 هشتگ‌های کامیونیتی:
        #هشتگ۱ #هشتگ۲ #هشتگ۳ #هشتگ۴ #هشتگ۵
        """
        
        response = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
        ai_reply = response.choices[0].message.content.strip()
        
        # حذف ستاره‌ها برای جلوگیری از ارور مارک‌داون
        if '*' in ai_reply: ai_reply = ai_reply.replace('*', '')

        await wait_msg.edit_text(ai_reply)
        log_event(user_id, 'hashtags_generated_success', topic)
            
    except Exception as e:
        log_event(user_id, 'hashtag_error', str(e))
        logger.error(f"Hashtag generation error: {e}")
        await wait_msg.edit_text("❌ مشکلی در تولید هشتگ‌ها پیش آمد.")

    return ConversationHandler.END

# ---------------------------------------------
# --- 3. قابلیت جدید 2: مربی ایده‌پردازی (/coach) ---
C_TEXT = 6

async def coach_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_services(update): return ConversationHandler.END
    log_event(update.effective_user.id, 'coach_start')
    await update.message.reply_text(
        "🧠 **به بخش مربی ایده خوش آمدید!**\n\n"
        "آیا خودتان ایده‌ای برای ریلز، کپشن یا متنی آماده کرده‌اید؟\n"
        "آن را اینجا بفرستید تا من مثل یک مشاور حرفه‌ای آن را بررسی کنم و راهکارهایی برای وایرال شدن و جذاب‌تر شدنش به شما پیشنهاد دهم."
    )
    return C_TEXT

async def coach_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = str(update.effective_user.id)
    user_idea_text = update.message.text
    
    try:
        response = supabase.table('profiles').select("*").eq('user_id', user_id).execute()
        if not response.data:
            await update.message.reply_text("❌ اول باید پروفایلت رو با دستور /profile بسازی تا بدونم کسب‌وکارت چیه.")
            return ConversationHandler.END
        user_profile = response.data[0]
    except Exception as e:
        await update.message.reply_text("❌ خطا در خواندن اطلاعات از دیتابیس.")
        return ConversationHandler.END

    wait_msg = await update.message.reply_text("🧐 در حال آنالیز ایده شما...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    try:
        prompt = f"""
        **شخصیت:** تو یک مربی سخت‌گیر اما سازنده و حرفه‌ای برای تولید محتوای اینستاگرام در ایران هستی.
        **ماموریت:** کاربر یک ایده یا متن خام برای پیجش نوشته است. وظیفه تو این است که این ایده را بر اساس پروفایلش نقد و بررسی کنی و نسخه بهتری پیشنهاد دهی.

        **اطلاعات پروفایل کاربر:**
        - کسب‌وکار: {user_profile['business']}
        - هدف: {user_profile.get('goal', 'نامشخص')}
        - مخاطب: {user_profile['audience']}
        - لحن برند: {user_profile['tone']}

        **ایده نوشته شده توسط کاربر:**
        "{user_idea_text}"

        **ساختار پاسخ تو (فقط به زبان فارسی و روان):**
        ۱. نقاط قوت ایده (چه چیزی در این متن خوب است؟)
        ۲. نقاط ضعف و جای بهبود (چه چیزی کم است؟ مثلاً قلاب ضعیف است یا کال‌تو‌اکشن ندارد؟ آیا با هدف کسب‌وکار همخوانی دارد؟)
        ۳. پیشنهاد اصلاحی من (یک نسخه بازنویسی شده، جذاب‌تر و حرفه‌ای‌تر از همان ایده کاربر را بنویس که قلاب قوی‌تر و ساختار بهتری داشته باشد.)

        **قانون مهم:** از هیچ‌گونه علامت ستاره (*) برای بولد کردن در پاسخ استفاده نکن. متن باید ساده و روان باشد.
        """
        
        response = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
        ai_reply = response.choices[0].message.content.strip()
        
        # حذف ستاره‌ها برای جلوگیری از ارور
        if '*' in ai_reply: ai_reply = ai_reply.replace('*', '')

        await wait_msg.edit_text(ai_reply)
        log_event(user_id, 'coach_analyzed_success')
            
    except Exception as e:
        log_event(user_id, 'coach_error', str(e))
        logger.error(f"Coach generation error: {e}")
        await wait_msg.edit_text("❌ مشکلی در آنالیز ایده پیش آمد.")

    return ConversationHandler.END

# ---------------------------------------------
# --- 4. مراحل مکالمه تولید محتوا (ایده‌پردازی و سناریو اصلی) ---
IDEAS, EXPAND = range(7, 9)

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
            message_text += f"ایده {i+1}: {idea['title']}\n- قلاب: «{idea['hook']}»\n\n"
        message_text += "کدام یک را برایت به سناریوی کامل تبدیل کنم؟"
        
        await wait_msg.edit_text(message_text, reply_markup=reply_markup)
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

        ۱. قلاب (۰-۳ ثانیه):
        - تصویر: (شرح صحنه اول)
        - متن روی صفحه: «{chosen_idea['hook']}»

        ۲. بدنه اصلی (۴-۲۰ ثانیه):
        - تصویر: (شرح سکانس‌ها)
        - گفتار: (متن صحبت‌ها)

        ۳. فراخوان به اقدام (۲۱-۳۰ ثانیه):
        - تصویر: (شرح صحنه پایانی)
        - متن روی صفحه: (درخواست واضح از مخاطب)
        
        ---
        ### ✍️ کپشن
        - کپشن: (کپشن جذاب فارسی)
        
        **قانون نهایی:** هرگز از کاراکتر `*` برای بولد کردن استفاده نکن.
        """
        response = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt_expansion}])
        ai_reply = response.choices[0].message.content.strip()

        is_rejection = ai_reply.startswith("موضوع با پروفایل")
        message_to_send = f"⚠️ توجه:\n{ai_reply}" if is_rejection else ai_reply
        
        # حذف ستاره‌ها
        if '*' in message_to_send: message_to_send = message_to_send.replace('*', '')

        try:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=message_to_send)
            if not is_rejection: log_event(str(update.effective_user.id), 'expansion_success', chosen_idea['title'])
        except BadRequest as e:
            logger.warning(f"Error sending message: {e}")
            await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ خطا در ارسال پیام.")
            
    except Exception as e:
        log_event(str(update.effective_user.id), 'expansion_error', str(e))
        logger.error(f"Error in expand_idea: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ در نوشتن سناریوی کامل مشکلی پیش آمد.")

    context.user_data.clear()
    return ConversationHandler.END


# ------------------------------
