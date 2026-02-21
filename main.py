import os
import logging
import threading
import json
import asyncio
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

from openai import OpenAI
from supabase import create_client, Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.error import BadRequest, Forbidden
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
ADMIN_ID = os.environ.get("ADMIN_ID") # آیدی ادمین

# محدودیت استفاده روزانه برای هر کاربر
DAILY_LIMIT = 5

# --- سرور وب برای بیدار نگه داشتن Render ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")
        
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

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

# --- توابع کمکی ---
async def check_services(update: Update) -> bool:
    message_target = update.callback_query.message if update.callback_query else update.message
    if not supabase or not client:
        await message_target.reply_text("❌ سیستم در حال حاضر با مشکل ارتباطی روبروست. لطفاً بعداً تلاش کنید.")
        return False
    return True

def log_event(user_id: str, event_type: str, content: str = ""):
    if not supabase: return
    try:
        data_to_insert = {'user_id': str(user_id), 'event_type': event_type, 'content': content}
        supabase.table('logs').insert(data_to_insert).execute()
    except Exception as e:
        logger.error(f"Supabase log event error: {e}")

async def get_today_usage(user_id: str = None) -> int:
    """تعداد درخواست‌های امروز را برمی‌گرداند. اگر user_id خالی باشد، کل استفاده ربات را می‌دهد."""
    if not supabase: return 0
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        query = supabase.table('logs').select("id", count="exact")\
            .in_('event_type', ['ideas_generated', 'hashtags_generated_success', 'coach_analyzed_success'])\
            .gte('created_at', f"{today}T00:00:00Z")
            
        if user_id:
            query = query.eq('user_id', user_id)
            
        response = query.execute()
        return response.count if response.count else 0
    except Exception as e:
        logger.error(f"Error checking usage: {e}")
        return 0

async def check_daily_limit(update: Update, user_id: str) -> bool:
    usage_count = await get_today_usage(user_id)
    if usage_count >= DAILY_LIMIT:
        message_target = update.callback_query.message if update.callback_query else update.message
        await message_target.reply_text(
            f"⚠️ **محدودیت استفاده روزانه**\n\n"
            f"شما امروز به سقف مجاز خود ({DAILY_LIMIT} درخواست) رسیده‌اید.\n"
            "برای حفظ کیفیت خدمات، لطفاً فردا دوباره مراجعه کنید. متشکریم! 🙏",
            parse_mode='Markdown'
        )
        return False
    return True

# ---------------------------------------------
# --- 👑 پنل مدیریت (Admin Panel) ---
A_BROADCAST = 10

def is_admin(user_id: int) -> bool:
    return ADMIN_ID and str(user_id) == str(ADMIN_ID)

async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نقطه ورود پنل ادمین (فقط با دستور /admin)"""
    if not is_admin(update.effective_user.id):
        return # اگر ادمین نبود، کلاً بی‌تفاوت عبور کن
    
    keyboard = [
        [InlineKeyboardButton("📊 آمار ربات", callback_data='admin_stats')],
        [InlineKeyboardButton("📢 ارسال پیام همگانی", callback_data='admin_broadcast_start')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("👑 **به پنل مدیریت خوش آمدید.**\nلطفاً یک گزینه را انتخاب کنید:", reply_markup=reply_markup, parse_mode='Markdown')

async def handle_admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت دکمه‌های ساده پنل ادمین (مثل نمایش آمار)"""
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer("شما دسترسی ندارید.", show_alert=True)
        return
        
    await query.answer()

    if query.data == 'admin_stats':
        try:
            # تعداد کل پروفایل‌ها (کاربران)
            prof_resp = supabase.table('profiles').select("id", count="exact").execute()
            total_users = prof_resp.count if prof_resp.count else 0
            
            # تعداد کل استفاده امروز
            total_usage_today = await get_today_usage()
            
            stats_msg = (
                "📊 **آمار زنده ربات:**\n\n"
                f"👥 کل کاربران ثبت‌نام شده: **{total_users}** نفر\n"
                f"🔥 کل درخواست‌های امروز: **{total_usage_today}** بار (هزینه API)\n"
            )
            await query.message.reply_text(stats_msg, parse_mode='Markdown')
        except Exception as e:
            await query.message.reply_text(f"❌ خطا در دریافت آمار: {e}")

# --- بخش ارسال پیام همگانی (Broadcast) ---
async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer("شما دسترسی ندارید.", show_alert=True)
        return ConversationHandler.END
        
    await query.answer()
    await query.message.reply_text(
        "📢 **ارسال پیام همگانی:**\n\n"
        "لطفاً پیامی که می‌خواهید برای تمام کاربران ارسال شود را اینجا تایپ کنید.\n"
        "(برای لغو از دستور /cancel استفاده کنید)"
    )
    return A_BROADCAST

async def admin_broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    
    broadcast_msg = update.message.text
    wait_msg = await update.message.reply_text("⏳ در حال استخراج لیست کاربران و شروع ارسال...")
    
    try:
        # دریافت تمام یوزرهای یکتا از جدول profiles
        response = supabase.table('profiles').select("user_id").execute()
        users = response.data
        
        if not users:
            await wait_msg.edit_text("❌ هیچ کاربری در دیتابیس یافت نشد.")
            return ConversationHandler.END
            
        success_count = 0
        fail_count = 0
        
        await wait_msg.edit_text(f"🚀 در حال ارسال پیام به {len(users)} کاربر...\nلطفاً صبور باشید.")
        
        for user in users:
            try:
                await context.bot.send_message(chat_id=user['user_id'], text=broadcast_msg)
                success_count += 1
                await asyncio.sleep(0.1) # جلوگیری از اسپم شدن ربات توسط تلگرام (Flood Limit)
            except Forbidden:
                # کاربر ربات را بلاک کرده است
                fail_count += 1
            except Exception as e:
                logger.error(f"Broadcast error for user {user['user_id']}: {e}")
                fail_count += 1
                
        result_msg = (
            "✅ **ارسال همگانی پایان یافت!**\n\n"
            f"📬 ارسال موفق: {success_count} نفر\n"
            f"🚫 کاربران بلاک‌کرده/ناموفق: {fail_count} نفر"
        )
        await update.message.reply_text(result_msg, parse_mode='Markdown')
        log_event(str(update.effective_user.id), 'admin_broadcast_sent', f"Success: {success_count}, Fail: {fail_count}")

    except Exception as e:
        logger.error(f"Database error during broadcast: {e}")
        await update.message.reply_text("❌ خطایی در ارتباط با دیتابیس رخ داد.")

    return ConversationHandler.END


# ---------------------------------------------
# --- منوی اصلی (Main Menu) ---
def get_main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("🎬 ایده‌پرداز و سناریونویس", callback_data='menu_scenario')],
        [InlineKeyboardButton("🏷 هشتگ‌ساز هوشمند", callback_data='menu_hashtags'), InlineKeyboardButton("🧠 مربی ایده", callback_data='menu_coach')],
        [InlineKeyboardButton("👤 تنظیمات پروفایل", callback_data='menu_profile'), InlineKeyboardButton("💳 وضعیت اعتبار", callback_data='menu_quota')]
    ]
    return InlineKeyboardMarkup(keyboard)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    log_event(str(user_id), 'opened_main_menu')
    
    welcome_text = (
        "سلام! 👋 به دستیار هوشمند تولید محتوای اینستاگرام خوش آمدید.\n\n"
        "من اینجا هستم تا صفر تا صد تولید محتوا را برایتان راحت کنم. از منوی زیر یکی از ابزارها را انتخاب کنید:"
    )
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(welcome_text, reply_markup=get_main_menu_keyboard())
    else:
        await update.message.reply_text(welcome_text, reply_markup=get_main_menu_keyboard())

async def handle_main_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(update.effective_user.id)
    await query.answer()

    if query.data == 'menu_scenario':
        text = ("🎬 **سناریونویس هوشمند:**\n\n"
                "برای استفاده از این بخش نیازی به زدن دکمه نیست! فقط کافیست **هر زمان** که خواستید، "
                "موضوع ریلز خود را به صورت متن عادی همینجا تایپ کنید تا برایتان ۳ ایده ناب طراحی کنم.\n"
                "(مثال: فواید خوردن قهوه در صبح)")
        await query.message.reply_text(text, parse_mode='Markdown')
        
    elif query.data == 'menu_quota':
        usage = await get_today_usage(user_id)
        remaining = max(0, DAILY_LIMIT - usage)
        text = (f"💳 **وضعیت اعتبار روزانه شما:**\n\n"
                f"🔹 کل سهمیه روزانه: {DAILY_LIMIT}\n"
                f"🔹 استفاده شده امروز: {usage}\n"
                f"✅ **اعتبار باقیمانده: {remaining}**\n\n"
                "(سهمیه شما هر شب ساعت ۱۲ شارژ می‌شود)")
        await query.message.reply_text(text, parse_mode='Markdown')

# ---------------------------------------------
# --- 1. مراحل مکالمه پروفایل ---
P_BUSINESS, P_GOAL, P_AUDIENCE, P_TONE = range(4)

async def profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_services(update): return ConversationHandler.END
    log_event(str(update.effective_user.id), 'profile_start')
    
    msg_text = "۱/۴ - موضوع اصلی پیج شما چیست؟\n(مثال: فروش آنلاین قهوه، آموزش یوگا)"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(msg_text)
    else:
        await update.message.reply_text(msg_text)
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
        await context.bot.send_message(
            chat_id=update.effective_chat.id, 
            text="✅ پروفایل شما ذخیره شد!\nحالا می‌توانید از طریق منوی اصلی از امکانات ربات استفاده کنید.",
            reply_markup=get_main_menu_keyboard()
        )
    except Exception as e:
        logger.error(f"Supabase upsert Error: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطا در ذخیره دیتابیس.")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    log_event(str(update.effective_user.id), 'action_canceled')
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text="عملیات لغو شد.")
    else:
        await update.message.reply_text("عملیات لغو شد.")
    return ConversationHandler.END


# ---------------------------------------------
# --- 2. قابلیت هشتگ‌های هوشمند ---
H_TOPIC = 5

async def hashtag_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_services(update): return ConversationHandler.END
    log_event(str(update.effective_user.id), 'hashtag_start')
    
    msg_text = (
        "🏷 **به ابزار هشتگ‌ساز هوشمند خوش آمدید!**\n\n"
        "لطفاً موضوع پست یا ریلز خود را تایپ کنید تا بهترین هشتگ‌ها را بر اساس پروفایلتان تولید کنم:"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(msg_text, parse_mode='Markdown')
    else:
        await update.message.reply_text(msg_text, parse_mode='Markdown')
    return H_TOPIC

async def hashtag_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = str(update.effective_user.id)
    if not await check_daily_limit(update, user_id): return ConversationHandler.END
        
    topic = update.message.text
    
    try:
        response = supabase.table('profiles').select("*").eq('user_id', user_id).execute()
        if not response.data:
            await update.message.reply_text("❌ اول باید پروفایلت رو با زدن روی 'تنظیمات پروفایل' بسازی.")
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
        if '*' in ai_reply: ai_reply = ai_reply.replace('*', '')

        await wait_msg.edit_text(ai_reply)
        log_event(user_id, 'hashtags_generated_success', topic)
    except Exception as e:
        log_event(user_id, 'hashtag_error', str(e))
        await wait_msg.edit_text("❌ مشکلی در تولید هشتگ‌ها پیش آمد.")
    return ConversationHandler.END

# ---------------------------------------------
# --- 3. قابلیت مربی ایده‌پردازی ---
C_TEXT = 6

async def coach_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_services(update): return ConversationHandler.END
    log_event(str(update.effective_user.id), 'coach_start')
    
    msg_text = (
        "🧠 **به بخش مربی ایده خوش آمدید!**\n\n"
        "آیا خودتان ایده‌ای برای ریلز، کپشن یا متنی آماده کرده‌اید؟\n"
        "آن را اینجا بفرستید تا من آن را بررسی کنم و راهکارهایی برای وایرال شدنش پیشنهاد دهم."
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(msg_text, parse_mode='Markdown')
    else:
        await update.message.reply_text(msg_text, parse_mode='Markdown')
    return C_TEXT

async def coach_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = str(update.effective_user.id)
    if not await check_daily_limit(update, user_id): return ConversationHandler.END

    user_idea_text = update.message.text
    try:
        response = supabase.table('profiles').select("*").eq('user_id', user_id).execute()
        if not response.data:
            await update.message.reply_text("❌ اول باید پروفایلت رو با دکمه 'تنظیمات پروفایل' بسازی.")
            return ConversationHandler.END
        user_profile = response.data[0]
    except Exception as e:
        await update.message.reply_text("❌ خطا در خواندن اطلاعات از دیتابیس.")
        return ConversationHandler.END

    wait_msg = await update.message.reply_text("🧐 در حال آنالیز ایده شما...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    try:
        prompt = f"""
        **شخصیت:** تو یک مربی سخت‌گیر اما سازنده برای تولید محتوای اینستاگرام هستی.
        **ماموریت:** کاربر یک ایده یا متن خام نوشته است. وظیفه تو نقد و بررسی آن و پیشنهاد نسخه بهتر است.

        **اطلاعات کاربر:**
        - کسب‌وکار: {user_profile['business']}
        - هدف: {user_profile.get('goal', 'نامشخص')}
        - مخاطب: {user_profile['audience']}
        - لحن برند: {user_profile['tone']}

        **ایده کاربر:**
        "{user_idea_text}"

        **ساختار پاسخ (فقط فارسی روان):**
        ۱. نقاط قوت ایده
        ۲. نقاط ضعف (آیا قلاب ضعیف است؟ کال‌تواکشن دارد؟)
        ۳. پیشنهاد اصلاحی من (یک نسخه بازنویسی شده و بسیار جذاب‌تر از ایده کاربر)

        **قانون مهم:** از هیچ‌گونه علامت ستاره (*) در پاسخ استفاده نکن.
        """
        response = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
        ai_reply = response.choices[0].message.content.strip()
        if '*' in ai_reply: ai_reply = ai_reply.replace('*', '')

        await wait_msg.edit_text(ai_reply)
        log_event(user_id, 'coach_analyzed_success')
    except Exception as e:
        log_event(user_id, 'coach_error', str(e))
        await wait_msg.edit_text("❌ مشکلی در آنالیز ایده پیش آمد.")
    return ConversationHandler.END

# ---------------------------------------------
# --- 4. مراحل مکالمه تولید محتوا (ایده‌پردازی و سناریو اصلی) ---
IDEAS, EXPAND = range(7, 9)

async def check_profile_before_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_services(update): return ConversationHandler.END
    user_id = str(update.effective_user.id)
    if not await check_daily_limit(update, user_id): return ConversationHandler.END
        
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    
    try:
        response = supabase.table('profiles').select("*").eq('user_id', user_id).execute()
        if not response.data:
            await update.message.reply_text("❌ لطفاً ابتدا از منوی اصلی، پروفایل خود را بسازید.")
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
        **ماموریت:** برای «موضوع» زیر، سه ایده کاملاً متفاوت برای ریلز پیشنهاد بده.
        
        - **کسب‌وکار:** {user_profile['business']}
        - **موضوع:** "{topic}"

        **ساختار خروجی (بسیار مهم):**
        یک آبجکت JSON با کلید "ideas" و مقدار لیست سه ایده.
        مثال:
        {{
            "ideas": [
                {{"title": "ایده ۱: آموزشی", "hook": "آیا می‌دانستید...؟"}},
                {{"title": "ایده ۲: داستانی", "hook": "روزی که فهمیدم..."}},
                {{"title": "ایده ۳: طنز", "hook": "وقتی می‌فهمی..."}}
            ]
        }}
        **قانون:** فقط همین ساختار JSON را خروجی بده.
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
        
        message_text = f"عالی! برای موضوع «{topic}»، سه ایده پیدا کردم:\n\n"
        for i, idea in enumerate(ideas_json):
            message_text += f"ایده {i+1}: {idea['title']}\n- قلاب: «{idea['hook']}»\n\n"
        message_text += "کدام یک را برایت به سناریوی کامل تبدیل کنم؟"
        
        await wait_msg.edit_text(message_text, reply_markup=reply_markup)
        log_event(str(update.effective_user.id), 'ideas_generated', topic)
        return EXPAND

    except Exception as e:
        log_event(str(update.effective_user.id), 'ideation_error', str(e))
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
        اگر موضوع انتخابی هیچ ارتباط منطقی با کسب‌وکار نداشت، فقط بنویس:
        `موضوع با پروفایل شما ارتباطی ندارد.`

        ---
        **ساختار نقشه ساخت (فقط فارسی):**
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


# ---------------------------------------------
if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # --- دستورات اصلی و ادمین ---
    application.add_handler(CommandHandler('start', show_main_menu))
    application.add_handler(CommandHandler('menu', show_main_menu))
    application.add_handler(CommandHandler('admin', admin_start))
    
    # هندلرهای ساده برای دکمه‌های منو که Conversation نیستند
    application.add_handler(CallbackQueryHandler(handle_main_menu_buttons, pattern='^(menu_scenario|menu_quota)$'))
    application.add_handler(CallbackQueryHandler(handle_admin_buttons, pattern='^admin_stats$'))
    
    # --- هندلر ارسال پیام همگانی (ادمین) ---
    admin_broadcast_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern='^admin_broadcast_start$')],
        states={
            A_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_send)],
        },
        fallbacks=[CommandHandler('cancel', cancel_action)],
    )
    
    # --- هندلر ساخت پروفایل ---
    profile_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('profile', profile_start),
            CallbackQueryHandler(profile_start, pattern='^menu_profile$')
        ],
        states={
            P_BUSINESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_business)],
            P_GOAL: [CallbackQueryHandler(get_goal, pattern='^goal_')],
            P_AUDIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_audience)],
            P_TONE: [CallbackQueryHandler(get_tone_and_save, pattern='^tone_')],
        },
        fallbacks=[CommandHandler('cancel', cancel_action), CallbackQueryHandler(cancel_action, pattern='^cancel$')],
    )

    # --- هندلر هشتگ ساز ---
    hashtag_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('hashtags', hashtag_start),
            CallbackQueryHandler(hashtag_start, pattern='^menu_hashtags$')
        ],
        states={
            H_TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, hashtag_generate)],
        },
        fallbacks=[CommandHandler('cancel', cancel_action)],
    )

    # --- هندلر مربی ایده ---
    coach_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('coach', coach_start),
            CallbackQueryHandler(coach_start, pattern='^menu_coach$')
        ],
        states={
            C_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, coach_analyze)],
        },
        fallbacks=[CommandHandler('cancel', cancel_action)],
    )

    # --- هندلر تولید سناریو (باید آخرین هندلر باشد تا پیام‌های متنی عادی را بگیرد) ---
    content_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, check_profile_before_content)],
        states={
            EXPAND: [CallbackQueryHandler(expand_idea, pattern='^expand_')],
        },
        fallbacks=[CommandHandler('cancel', cancel_action), CallbackQueryHandler(cancel_action, pattern='^cancel$')],
    )
    
    # اضافه کردن تمام هندلرها به اپلیکیشن
    application.add_handler(admin_broadcast_handler)
    application.add_handler(profile_conv_handler)
    application.add_handler(hashtag_conv_handler)
    application.add_handler(coach_conv_handler)
    application.add_handler(content_conv_handler)
    
    print("🤖 BOT DEPLOYED WITH ADMIN PANEL, QUOTA & GLASS MENU!")
    application.run_polling()
