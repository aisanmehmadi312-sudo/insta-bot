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

# --- دریافت توکن‌ها و تنظیمات ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ADMIN_ID = os.environ.get("ADMIN_ID") # آیدی ادمین از متغیر محیطی گرفته می‌شود

DAILY_LIMIT = 5
MAINTENANCE_MODE = False

# --- تنظیمات پرداخت ---
CARD_NUMBER = "6118-2800-5587-6343" 
CARD_NAME = "امیراحمد شاه حسینی"
VIP_PRICE = "۹۹,۰۰۰ تومان" 
SUPPORT_USERNAME = "@Amir_shahosseini" # یوزرنیم برای پشتیبانی

# --- سرور وب ---
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
def is_admin(user_id: int) -> bool: 
    return ADMIN_ID and str(user_id) == str(ADMIN_ID)

async def check_maintenance(update: Update) -> bool:
    if MAINTENANCE_MODE and not is_admin(update.effective_user.id):
        msg = "🛠 ربات در حال بروزرسانی است!\n\nبرای ارتقای کیفیت خدمات، ربات برای دقایقی در حالت تعمیرات قرار دارد. لطفاً کمی بعد دوباره مراجعه کنید. 🙏"
        if update.callback_query:
            await update.callback_query.answer("ربات در حال بروزرسانی است 🛠", show_alert=True)
        else:
            await update.message.reply_text(msg)
        return True 
    return False 

async def check_services(update: Update) -> bool:
    if await check_maintenance(update): return False 
    message_target = update.callback_query.message if update.callback_query else update.message
    if not supabase or not client:
        await message_target.reply_text("❌ سیستم در حال حاضر با مشکل ارتباطی روبروست.")
        return False
    return True

def log_event(user_id: str, event_type: str, content: str = ""):
    if not supabase: return
    try:
        supabase.table('logs').insert({'user_id': str(user_id), 'event_type': event_type, 'content': content}).execute()
    except Exception as e:
        logger.error(f"Supabase log event error: {e}")

async def get_today_usage(user_id: str = None) -> int:
    if not supabase: return 0
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        query = supabase.table('logs').select("id", count="exact").in_('event_type', ['ideas_generated', 'hashtags_generated_success', 'coach_analyzed_success', 'dalle_generated']).gte('created_at', f"{today}T00:00:00Z")
        if user_id: query = query.eq('user_id', user_id)
        response = query.execute()
        return response.count if response.count else 0
    except Exception as e:
        return 0

async def is_user_vip(user_id: str) -> bool:
    if not supabase: return False
    try:
        response = supabase.table('profiles').select('is_vip').eq('user_id', user_id).execute()
        if response.data and 'is_vip' in response.data[0]:
            return bool(response.data[0]['is_vip'])
        return False
    except Exception as e:
        logger.error(f"Error checking VIP status: {e}")
        return False 

async def check_daily_limit(update: Update, user_id: str) -> bool:
    if is_admin(update.effective_user.id) or await is_user_vip(user_id): return True 
    usage_count = await get_today_usage(user_id)
    if usage_count >= DAILY_LIMIT:
        message_target = update.callback_query.message if update.callback_query else update.message
        await message_target.reply_text(
            f"⚠️ محدودیت استفاده روزانه\n\n"
            f"شما امروز به سقف مجاز کاربران عادی ({DAILY_LIMIT} درخواست) رسیده‌اید.\n"
            "برای استفاده نامحدود و دسترسی به امکانات ویژه (مثل تولید کاور با هوش مصنوعی)، می‌توانید حساب خود را به VIP ارتقا دهید. 💎", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 ارتقا به VIP", callback_data='menu_upgrade_vip')]])
        )
        return False
    return True

async def process_voice_to_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    wait_msg = await update.message.reply_text("🎙 در حال تبدیل صدای شما به متن...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        file_path = f"temp_voice_{update.effective_user.id}.ogg"
        await voice_file.download_to_drive(file_path)
        with open(file_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(model="whisper-1", file=audio_file)
        if os.path.exists(file_path): os.remove(file_path)
        await wait_msg.delete()
        return transcription.text
    except Exception as e:
        logger.error(f"Voice processing error: {e}")
        await wait_msg.edit_text("❌ در پردازش صدا مشکلی پیش آمد. لطفاً متن خود را تایپ کنید.")
        if 'file_path' in locals() and os.path.exists(file_path): os.remove(file_path)
        return None

def get_feedback_and_dalle_keyboard(context_name: str):
    keyboard = [
        [
            InlineKeyboardButton("👍 عالی", callback_data=f'feedback_like_{context_name}'),
            InlineKeyboardButton("👎 جالب نبود", callback_data=f'feedback_dislike_{context_name}')
        ]
    ]
    if context_name == 'scenario':
        keyboard.append([InlineKeyboardButton("🎨 تولید تصویر کاور (ویژه VIP 💎)", callback_data='dalle_trigger_request')])
    return InlineKeyboardMarkup(keyboard)

# ---------------------------------------------
# --- 👑 پنل ادمین ---
A_BROADCAST = 10

def get_admin_keyboard():
    global MAINTENANCE_MODE
    m_text = "🟢 روشن" if MAINTENANCE_MODE else "🔴 خاموش"
    keyboard = [
        [InlineKeyboardButton("📊 آمار کلی", callback_data='admin_stats'), InlineKeyboardButton("🕵️‍♂️ مانیتورینگ", callback_data='admin_monitor')],
        [InlineKeyboardButton("👥 ۵ کاربر اخیر", callback_data='admin_recent_users')],
        [InlineKeyboardButton("📢 ارسال پیام همگانی", callback_data='admin_broadcast_start')],
        [InlineKeyboardButton(f"🛠 حالت تعمیرات: {m_text}", callback_data='admin_toggle_maintenance')]
    ]
    return InlineKeyboardMarkup(keyboard)

async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await update.message.reply_text("👑 پنل مدیریت ربات\nلطفاً یک گزینه را انتخاب کنید:", reply_markup=get_admin_keyboard())

async def handle_admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MAINTENANCE_MODE
    query = update.callback_query
    if not is_admin(update.effective_user.id): return await query.answer("عدم دسترسی", show_alert=True)
    
    if query.data == 'admin_toggle_maintenance':
        MAINTENANCE_MODE = not MAINTENANCE_MODE
        await query.answer(f"تعمیرات {'روشن' if MAINTENANCE_MODE else 'خاموش'} شد.")
        await query.edit_message_reply_markup(reply_markup=get_admin_keyboard())
        return

    await query.answer()
    
    if query.data == 'admin_stats':
        try:
            total_users = supabase.table('profiles').select("id", count="exact").execute().count or 0
            vip_users = supabase.table('profiles').select("id", count="exact").eq('is_vip', True).execute().count or 0
            total_usage_today = await get_today_usage()
            await query.message.reply_text(f"📊 آمار:\n👥 کل کاربران: {total_users}\n💎 کاربران ویژه (VIP): {vip_users}\n🔥 درخواست‌های امروز: {total_usage_today}")
        except: await query.message.reply_text("❌ خطا در آمار.")
            
    elif query.data == 'admin_monitor':
        try:
            logs = supabase.table('logs').select("user_id, event_type, content").in_('event_type', ['ideas_generated', 'hashtags_generated_success', 'coach_analyzed_success', 'dalle_generated']).order('created_at', desc=True).limit(5).execute().data
            if not logs: return await query.message.reply_text("📭 خالی.")
            msg = "🕵️‍♂️ ۵ درخواست اخیر:\n\n"
            for idx, log in enumerate(logs):
                event_name = "سناریونویس 🎬" if log['event_type'] == 'ideas_generated' else "هشتگ‌ساز 🏷" if log['event_type'] == 'hashtags_generated_success' else "کاورساز 🎨" if log['event_type'] == 'dalle_generated' else "مربی ایده 🧠"
                msg += f"**{idx+1}. ابزار:** {event_name}\n👤 آیدی: `{log['user_id']}`\n📝 موضوع: {log['content']}\n──────────────\n"
            await query.message.reply_text(msg)
        except: await query.message.reply_text("❌ خطا در مانیتورینگ.")

    elif query.data == 'admin_recent_users':
        try:
            users = supabase.table('profiles').select("*").order('created_at', desc=True).limit(5).execute().data
            if not users: return await query.message.reply_text("📭 خالی.")
            msg = "👥 ۵ کاربر اخیر:\n\n"
            for idx, u in enumerate(users):
                vip_status = "💎 VIP" if u.get('is_vip') else "عادی"
                msg += f"**{idx+1}. آیدی:** `{u['user_id']}`\n💼 کسب‌وکار: {u['business']}\n💳 اکانت: {vip_status}\n──────────────\n"
            await query.message.reply_text(msg)
        except: await query.message.reply_text("❌ خطا.")

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    await query.answer()
    await query.message.reply_text("📢 پیام همگانی را تایپ کنید (لغو: /cancel):")
    return A_BROADCAST

async def admin_broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    msg = update.message.text
    wait_msg = await update.message.reply_text("⏳ در حال ارسال...")
    try:
        users = supabase.table('profiles').select("user_id").execute().data
        success, fail = 0, 0
        for u in users:
            try:
                await context.bot.send_message(chat_id=u['user_id'], text=msg)
                success += 1
                await asyncio.sleep(0.1) 
            except: fail += 1
        await wait_msg.edit_text(f"✅ ارسال شد!\nموفق: {success}\nناموفق: {fail}")
        log_event(str(update.effective_user.id), 'admin_broadcast_sent', f"S: {success}")
    except: await wait_msg.edit_text("❌ خطا در دیتابیس.")
    return ConversationHandler.END

# --- هندلر دریافت عکس (برای رسید پرداخت) ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    if context.user_data.get('awaiting_receipt'):
        if not ADMIN_ID:
            await update.message.reply_text("❌ خطا: آیدی ادمین در سیستم تنظیم نشده است. امکان پردازش خرید وجود ندارد.")
            return
            
        user = update.effective_user
        username_str = f"@{user.username}" if user.username else "ندارد"
        caption = (
            "💰 رسید پرداختی جدید!\n\n"
            f"👤 نام: {user.first_name}\n"
            f"🆔 آیدی تلگرام: {user_id}\n"
            f"🔗 یوزرنیم: {username_str}"
        )
        
        admin_kb = [
            [InlineKeyboardButton("✅ تایید و ارتقا به VIP", callback_data=f'verify_payment_{user_id}')],
            [InlineKeyboardButton("❌ رد رسید", callback_data=f'reject_payment_{user_id}')]
        ]
        
        try:
            await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=update.message.photo[-1].file_id,
                caption=caption,
                reply_markup=InlineKeyboardMarkup(admin_kb)
            )
            context.user_data['awaiting_receipt'] = False
            await update.message.reply_text("⏳ رسید شما دریافت شد و برای مدیریت ارسال گردید. لطفاً منتظر تایید بمانید...")
            log_event(user_id, 'receipt_sent')
            
        except Exception as e:
            logger.error(f"Error sending receipt to admin: {e}")
            await update.message.reply_text(f"❌ متاسفانه در ارسال رسید مشکلی پیش آمد.")
    else:
        await update.message.reply_text("لطفاً برای تولید محتوا، موضوع خود را تایپ یا ویس کنید. من فعلاً قادر به پردازش عکس نیستم! 😅")

# --- هندلر دکمه‌های تایید فیش توسط ادمین ---
async def handle_payment_verification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update.effective_user.id): return await query.answer("شما ادمین نیستید!", show_alert=True)
    
    data = query.data
    parts = data.split('_')
    action = parts[0] 
    target_user_id = parts[2]
    
    if action == 'verify':
        try:
            supabase.table('profiles').update({'is_vip': True}).eq('user_id', target_user_id).execute()
            await query.edit_message_reply_markup(reply_markup=None)
            await query.edit_message_caption(caption=f"{query.message.caption}\n\n✅ تایید و کاربر VIP شد.")
            
            success_msg = "🎉 تبریک! پرداخت شما تایید شد.\n\nحساب شما به VIP 💎 ارتقا یافت. هم‌اکنون محدودیت استفاده روزانه شما برداشته شده و می‌توانید از قابلیت بی‌نظیر تولید کاور با هوش مصنوعی (DALL-E) استفاده کنید!"
            await context.bot.send_message(chat_id=target_user_id, text=success_msg)
            log_event(target_user_id, 'upgraded_to_vip_by_admin')
            
        except Exception as e:
            logger.error(f"Error upgrading user: {e}")
            await query.answer("❌ خطا در آپدیت دیتابیس!", show_alert=True)
            
    elif action == 'reject':
        await query.edit_message_reply_markup(reply_markup=None)
        await query.edit_message_caption(caption=f"{query.message.caption}\n\n❌ این رسید توسط شما رد شد.")
        
        reject_msg = f"❌ کاربر گرامی، متاسفانه رسید ارسالی شما تایید نشد. در صورت بروز اشتباه، لطفاً با پشتیبانی در ارتباط باشید."
        await context.bot.send_message(chat_id=target_user_id, text=reject_msg)

# ---------------------------------------------
# --- منوی اصلی ---
def get_main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("🎬 ایده‌پرداز و سناریو", callback_data='menu_scenario')],
        [InlineKeyboardButton("🏷 هشتگ‌ساز", callback_data='menu_hashtags'), InlineKeyboardButton("🧠 مربی ایده", callback_data='menu_coach')],
        [InlineKeyboardButton("👤 پروفایل", callback_data='menu_profile'), InlineKeyboardButton("💳 اعتبار", callback_data='menu_quota')],
        [InlineKeyboardButton("💎 ارتقا به VIP", callback_data='menu_upgrade_vip')]
    ]
    return InlineKeyboardMarkup(keyboard)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_maintenance(update): return 
    log_event(str(update.effective_user.id), 'opened_main_menu')
    text = "سلام! از منوی زیر انتخاب کنید:\n(می‌تونید ویس هم بفرستید!)"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, reply_markup=get_main_menu_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=get_main_menu_keyboard())

async def handle_main_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_maintenance(update): return 
    query = update.callback_query
    user_id = str(update.effective_user.id)
    await query.answer()
    
    if query.data == 'menu_scenario':
        await query.message.reply_text("🎬 فقط کافیست موضوع را تایپ یا ویس کنید.")
    
    elif query.data == 'menu_quota':
        is_vip = await is_user_vip(user_id)
        if is_vip:
            await query.message.reply_text("💎 وضعیت اکانت شما: VIP\n\nشما هیچ محدودیتی در استفاده از ربات ندارید و دسترسی به تولید کاور DALL-E برایتان فعال است. لذت ببرید! 🚀")
        else:
            usage = await get_today_usage(user_id)
            remaining = max(0, DAILY_LIMIT - usage)
            await query.message.reply_text(
                f"💳 وضعیت اکانت شما: کاربر عادی\n\n"
                f"🔹 سهمیه روزانه: {DAILY_LIMIT}\n"
                f"🔹 استفاده شده امروز: {usage}\n"
                f"✅ اعتبار باقیمانده: {remaining}\n\n"
                "(برای استفاده نامحدود روی دکمه 'ارتقا به VIP' در منو کلیک کنید)"
            )
            
    elif query.data == 'menu_upgrade_vip':
        if await is_user_vip(user_id):
            await query.message.reply_text("شما از قبل کاربر VIP هستید! 💎 نیازی به ارتقا مجدد نیست.")
            return
            
        # در اینجا متغیر parse_mode را کلاً پاک کردم تا ارور تلگرام رخ ندهد
        payment_info = (
            "💎 ارتقا به حساب ویژه (VIP)\n\n"
            "با ارتقای حساب خود از مزایای زیر بهره‌مند می‌شوید:\n"
            "۱. ♾ حذف کامل محدودیت استفاده روزانه\n"
            "۲. 🎨 قابلیت تولید کاور حرفه‌ای ریلز با هوش مصنوعی تصویرساز (DALL-E 3)\n\n"
            f"💳 مبلغ قابل پرداخت: {VIP_PRICE}\n"
            f"شماره کارت: {CARD_NUMBER}\n"
            f"به نام: {CARD_NAME}\n\n"
            "📸 لطفاً پس از واریز، عکس رسید خود را در همینجا برای من ارسال کنید.\n"
            f"پشتیبانی: {SUPPORT_USERNAME}"
        )
        context.user_data['awaiting_receipt'] = True
        await query.message.reply_text(payment_info)

# --- مکالمه پروفایل ---
P_BUSINESS, P_GOAL, P_AUDIENCE, P_TONE = range(4)
async def profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_services(update): return ConversationHandler.END
    context.user_data.clear() 
    msg = "۱/۴ - موضوع اصلی پیج؟"
    if update.callback_query: await update.callback_query.message.reply_text(msg)
    else: await update.message.reply_text(msg)
    return P_BUSINESS

async def get_business(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['business'] = update.message.text
    kb = [[InlineKeyboardButton("فروش", callback_data='goal_sales'), InlineKeyboardButton("آگاهی", callback_data='goal_awareness')],
          [InlineKeyboardButton("آموزش", callback_data='goal_education'), InlineKeyboardButton("سرگرمی", callback_data='goal_community')]]
    await update.message.reply_text("۲/۴ - هدف اصلی؟", reply_markup=InlineKeyboardMarkup(kb))
    return P_GOAL

async def get_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if 'business' not in context.user_data:
        await query.edit_message_text("⚠️ زمان نشست تمام شده. لطفاً دوباره از منو /profile را بزنید.")
        return ConversationHandler.END
    context.user_data['goal'] = next(btn.text for r in query.message.reply_markup.inline_keyboard for btn in r if btn.callback_data == query.data)
    await query.edit_message_text(f"✅ هدف: {context.user_data['goal']}")
    await context.bot.send_message(chat_id=update.effective_chat.id, text="۳/۴ - مخاطب هدف؟")
    return P_AUDIENCE

async def get_audience(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if 'goal' not in context.user_data: return ConversationHandler.END
    context.user_data['audience'] = update.message.text
    kb = [[InlineKeyboardButton("صمیمی", callback_data='tone_friendly'), InlineKeyboardButton("رسمی", callback_data='tone_formal')],
          [InlineKeyboardButton("انرژی‌بخش", callback_data='tone_energetic'), InlineKeyboardButton("طنز", callback_data='tone_humorous')],
          [InlineKeyboardButton("آموزشی", callback_data='tone_educational')]]
    await update.message.reply_text("۴/۴ - لحن برند؟", reply_markup=InlineKeyboardMarkup(kb))
    return P_TONE

async def get_tone_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if 'business' not in context.user_data or 'audience' not in context.user_data:
        await query.edit_message_text("⚠️ خطای حافظه. لطفاً مجدداً پروفایل را بسازید.")
        return ConversationHandler.END
    context.user_data['tone'] = next(btn.text for r in query.message.reply_markup.inline_keyboard for btn in r if btn.callback_data == query.data)
    await query.edit_message_text(f"✅ لحن: {context.user_data['tone']}")
    try:
        supabase.table('profiles').upsert({'user_id': str(update.effective_user.id), **context.user_data}).execute()
        await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ پروفایل ذخیره شد!", reply_markup=get_main_menu_keyboard())
    except: await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ خطا در ذخیره.")
    context.user_data.clear()
    return ConversationHandler.END
async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.callback_query: await update.callback_query.edit_message_text("لغو شد.")
    else: await update.message.reply_text("لغو شد.")
    return ConversationHandler.END

# ---------------------------------------------
# --- هندلر بازخورد کاربر ---
async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data 
    
    if data.startswith('feedback_'):
        await query.answer()
        parts = data.split('_')
        action = parts[1] 
        context_type = parts[2] 
        log_event(str(update.effective_user.id), f"feedback_{action}", context_type)
        
        existing_keyboard = query.message.reply_markup.inline_keyboard
        new_keyboard = [[InlineKeyboardButton("✅ نظر شما ثبت شد. متشکریم!", callback_data='ignore')]]
        
        if len(existing_keyboard) > 1 and 'dalle_trigger' in existing_keyboard[1][0].callback_data:
             new_keyboard.append(existing_keyboard[1])
             
        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(new_keyboard))
        except Exception as e:
            pass

# ---------------------------------------------
# --- 🎨 قابلیت ویژه: تولید تصویر با DALL-E 3 ---
async def handle_dalle_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() 
    user_id = str(update.effective_user.id)
    topic = context.user_data.get('dalle_topic', 'یک صحنه مرتبط با موضوع')
    
    if not await is_user_vip(user_id):
        paywall_msg = (
            "🌟 قابلیت تولید کاور با هوش مصنوعی مخصوص کاربران VIP است.\n\n"
            "با ارتقای حساب خود، می‌توانید برای هر سناریو، یک کاور گرافیکی خیره‌کننده طراحی کنید.\n"
            "برای ارتقا، از منوی اصلی روی دکمه «💎 ارتقا به VIP» کلیک کنید."
        )
        await context.bot.send_message(chat_id=update.effective_chat.id, text=paywall_msg)
        return

    wait_msg = await context.bot.send_message(chat_id=update.effective_chat.id, text="🎨 در حال طراحی و تولید تصویر با کیفیت بالا (DALL-E 3). این فرآیند ممکن است ۲۰ ثانیه طول بکشد...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)

    try:
        prompt_generator = f"""
        Write a highly detailed, cinematic prompt for DALL-E 3 to create an Instagram Reel cover image based on this topic: "{topic}".
        Rules:
        1. The image MUST be 100% free of any text, letters, or words. It should just be the visual background/scene.
        2. Make it eye-catching, vibrant, and suitable for social media.
        3. Vertical aspect ratio style.
        Just output the prompt directly.
        """
        dalle_prompt_response = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt_generator}])
        dalle_prompt = dalle_prompt_response.choices[0].message.content.strip()

        response = client.images.generate(
            model="dall-e-3",
            prompt=dalle_prompt,
            size="1024x1792", 
            quality="hd",
            n=1,
        )
        image_url = response.data[0].url

        await context.bot.send_photo(
            chat_id=update.effective_chat.id, 
            photo=image_url, 
            caption=f"🎨 کاور پیشنهادی شما آماده است!\n\n(این تصویر بدون متن طراحی شده تا بتوانید خودتان در اینستاگرام، متن قلاب را روی آن تایپ کنید)"
        )
        await wait_msg.delete()
        log_event(user_id, 'dalle_generated', topic)

    except Exception as e:
        logger.error(f"DALL-E Error: {e}")
        await wait_msg.edit_text("❌ متاسفانه در تولید تصویر مشکلی پیش آمد.")

# ---------------------------------------------
# --- هشتگ ساز ---
H_TOPIC = 5

async def hashtag_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_services(update): return ConversationHandler.END
    msg = "🏷 هشتگ‌ساز! موضوع را تایپ یا ویس کنید:"
    if update.callback_query: await update.callback_query.message.reply_text(msg)
    else: await update.message.reply_text(msg)
    return H_TOPIC

async def hashtag_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = str(update.effective_user.id)
    if not await check_daily_limit(update, uid): return ConversationHandler.END
    
    if update.message.voice:
        topic = await process_voice_to_text(update, context)
        if not topic: return ConversationHandler.END
        await update.message.reply_text(f"🗣 شما: {topic}")
    else: topic = update.message.text
    
    try:
        prof = supabase.table('profiles').select("*").eq('user_id', uid).execute().data[0]
        wait_msg = await update.message.reply_text("⏳ در حال تولید هشتگ...")
        prompt = f"""
        شخصیت: مدیر استراتژی محتوای سخت‌گیر ایرانی.
        مرحله اول (فیلتر): آیا ({topic}) با کسب‌وکار ({prof['business']}) ارتباط تجاری دارد؟
        مرحله دوم (خروجی JSON):
        فقط یک آبجکت JSON بده. بدون ستاره.
        اگر بی‌ربط بود: {{"is_relevant": false, "rejection_message": "موضوع با کسب‌وکار شما ارتباطی ندارد.", "hashtags_text": ""}}
        اگر مرتبط بود: {{"is_relevant": true, "rejection_message": "", "hashtags_text": "🎯 پربازدید:\\n#هشتگ...\\n\\n🔬 تخصصی:\\n#هشتگ...\\n\\n🤝 کامیونیتی:\\n#هشتگ..."}}
        """
        response = client.chat.completions.create(model="gpt-4o", response_format={"type": "json_object"}, messages=[{"role": "user", "content": prompt}])
        response_data = json.loads(response.choices[0].message.content)
        
        if not response_data.get("is_relevant", True):
            await wait_msg.edit_text(f"⚠️ توجه:\n{response_data.get('rejection_message', 'نامرتبط.')}")
            return ConversationHandler.END

        hashtags_text = response_data.get("hashtags_text", "").replace('*', '')
        await wait_msg.edit_text(hashtags_text, reply_markup=get_feedback_keyboard('hashtag'))
        log_event(uid, 'hashtags_generated_success', topic)
    except: await update.message.reply_text("❌ خطا در تولید هشتگ یا یافتن پروفایل.")
    return ConversationHandler.END

# ---------------------------------------------
# --- مربی ایده ---
C_TEXT = 6

async def coach_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_services(update): return ConversationHandler.END
    msg = "🧠 مربی ایده! ایده خود را بنویسید یا ویس بفرستید:"
    if update.callback_query: await update.callback_query.message.reply_text(msg)
    else: await update.message.reply_text(msg)
    return C_TEXT

async def coach_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = str(update.effective_user.id)
    if not await check_daily_limit(update, uid): return ConversationHandler.END
    
    if update.message.voice:
        idea = await process_voice_to_text(update, context)
        if not idea: return ConversationHandler.END
        await update.message.reply_text(f"🗣 ایده شما: {idea}")
    else: idea = update.message.text

    try:
        prof = supabase.table('profiles').select("*").eq('user_id', uid).execute().data[0]
        wait_msg = await update.message.reply_text("🧐 در حال آنالیز...")
        prompt = f"""
        شخصیت: مربی سخت‌گیر محتوای ایرانی.
        مرحله اول (فیلتر): آیا این ایده ({idea}) با کسب‌وکار ({prof['business']}) بی‌ربط است؟
        مرحله دوم (خروجی JSON):
        فقط یک آبجکت JSON بده. بدون ستاره.
        اگر بی‌ربط بود: {{"is_relevant": false, "rejection_message": "ایده با کسب‌وکار شما ارتباطی ندارد.", "coach_text": ""}}
        اگر مرتبط بود: {{"is_relevant": true, "rejection_message": "", "coach_text": "۱. نقاط قوت...\\n۲. نقاط ضعف...\\n۳. پیشنهاد اصلاحی..."}}
        """
        response = client.chat.completions.create(model="gpt-4o", response_format={"type": "json_object"}, messages=[{"role": "user", "content": prompt}])
        response_data = json.loads(response.choices[0].message.content)
        
        if not response_data.get("is_relevant", True):
            await wait_msg.edit_text(f"⚠️ توجه:\n{response_data.get('rejection_message', 'نامرتبط.')}")
            return ConversationHandler.END

        coach_text = response_data.get("coach_text", "").replace('*', '')
        await wait_msg.edit_text(coach_text, reply_markup=get_feedback_keyboard('coach'))
        log_event(uid, 'coach_analyzed_success', idea)
    except: await update.message.reply_text("❌ خطا در آنالیز.")
    return ConversationHandler.END

# ---------------------------------------------
# --- 🚀 سناریو ساز (۳ مرحله‌ای) ---
C_CLAIM, C_EMOTION, EXPAND = range(7, 10)

async def check_profile_before_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = str(update.effective_user.id)
    if not await check_services(update) or not await check_daily_limit(update, uid): return ConversationHandler.END
    try:
        prof_res = supabase.table('profiles').select("*").eq('user_id', uid).execute()
        if not prof_res.data:
            await update.message.reply_text("❌ لطفاً ابتدا از منوی اصلی، پروفایل خود را بسازید.")
            return ConversationHandler.END
        
        context.user_data['profile'] = prof_res.data[0]
        
        if update.message.voice:
            topic = await process_voice_to_text(update, context)
            if not topic: return ConversationHandler.END
            await update.message.reply_text(f"🗣 موضوع: {topic}")
        else:
            topic = update.message.text
            
        context.user_data['topic'] = topic
        
        await update.message.reply_text(
            "بسیار خب! برای اینکه سناریوی شما کاملاً واقعی باشد، پاسخ دهید:\n\n"
            "۱/۲ - دقیقاً چه حرف، ادعا یا نظر خاصی درباره این موضوع دارید؟\n"
            "(مثال: موافقم چون... / راه حل من این است که...)\n\n"
            "(متن تایپ کنید یا ویس بفرستید)"
        )
        return C_CLAIM
        
    except Exception as e:
        logger.error(f"Error in start content: {e}")
        await update.message.reply_text("❌ خطایی رخ داد.")
        return ConversationHandler.END

async def get_claim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.voice:
        claim = await process_voice_to_text(update, context)
        if not claim: return ConversationHandler.END
    else:
        claim = update.message.text
        
    context.user_data['claim'] = claim
    
    keyboard = [
        [InlineKeyboardButton("امیدوار کننده 🌟", callback_data='emo_hope'), InlineKeyboardButton("تلنگر و هشدار ⚠️", callback_data='emo_warning')],
        [InlineKeyboardButton("طنز و سرگرمی 😂", callback_data='emo_funny'), InlineKeyboardButton("همدلی و درک 🤝", callback_data='emo_empathy')],
        [InlineKeyboardButton("علمی و قاطع 🧠", callback_data='emo_logical')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "۲/۲ - دوست دارید مخاطب بعد از دیدن این ریلز چه حسی داشته باشد؟",
        reply_markup=reply_markup
    )
    return C_EMOTION

async def generate_ideas_after_emotion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    emotion = next(btn.text for row in query.message.reply_markup.inline_keyboard for btn in row if btn.callback_data == query.data)
    context.user_data['emotion'] = emotion
    
    prof = context.user_data['profile']
    topic = context.user_data['topic']
    claim = context.user_data['claim']
    
    await query.edit_message_text(f"حس انتخابی: {emotion}\n\n⏳ در حال ایده‌پردازی دقیق...")
    
    try:
        prompt = f"""
        شخصیت: استراتژیست محتوای اینستاگرام. داستان از خودت نساز.
        مرحله اول (فیلتر): آیا موضوع ({topic}) با کسب‌وکار ({prof['business']}) ارتباط دارد؟
        مرحله دوم (خروجی JSON):
        اگر بی‌ربط بود: {{"is_relevant": false, "rejection_message": "موضوع با کسب‌وکار ارتباطی ندارد.", "ideas": []}}
        اگر مرتبط بود:
        سه ایده جذاب بساز. ادعای کاربر: "{claim}" / احساس: "{emotion}". قلاب‌ها بر این اساس باشد.
        {{
            "is_relevant": true,
            "rejection_message": "",
            "ideas": [{{"title": "...","hook": "..."}}, {{"title": "...","hook": "..."}}, {{"title": "...","hook": "..."}}]
        }}
        """
        res = client.chat.completions.create(model="gpt-4o", response_format={"type": "json_object"}, messages=[{"role": "user", "content": prompt}])
        response_data = json.loads(res.choices[0].message.content)
        
        if not response_data.get("is_relevant", True):
            await query.message.reply_text(f"⚠️ توجه:\n{response_data.get('rejection_message', 'نامرتبط.')}")
            log_event(str(update.effective_user.id), 'topic_rejected_gatekeeper', topic)
            return ConversationHandler.END

        ideas = response_data.get("ideas", [])
        if not ideas: raise ValueError("Empty ideas.")

        context.user_data['ideas'] = ideas
        kb = [[InlineKeyboardButton(f"🎬 ساخت ایده {i+1}", callback_data=f'expand_{i}')] for i in range(len(ideas))]
        msg = f"موضوع: {topic}\nادعای شما: {claim}\n\n" + "\n".join([f"{i+1}. {x['title']}\nقلاب: {x['hook']}\n" for i, x in enumerate(ideas)])
        await query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb))
        log_event(str(update.effective_user.id), 'ideas_generated', topic)
        return EXPAND
        
    except Exception as e:
        logger.error(f"Ideation error: {e}")
        await query.message.reply_text("❌ خطا در ایده‌پردازی.")
        return ConversationHandler.END

async def expand_idea(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    if 'ideas' not in context.user_data or 'profile' not in context.user_data:
        await query.edit_message_text("⚠️ زمان نشست تمام شده. لطفاً دوباره از ابتدا شروع کنید.")
        return ConversationHandler.END
        
    idea = context.user_data['ideas'][int(query.data.split('_')[1])]
    prof = context.user_data['profile']
    claim = context.user_data['claim']
    emotion = context.user_data['emotion']
    
    context.user_data['dalle_topic'] = idea['title']
    
    await query.edit_message_text(f"✅ انتخاب: {idea['title']}\n⏳ در حال نوشتن سناریوی حرفه‌ای...")
    
    try:
        prompt = f"""
        شخصیت تو: کپی‌رایتر حرفه‌ای اینستاگرام ایران. فقط بر اساس واقعیت‌های داده شده بنویس.
        اطلاعات:
        - کسب‌وکار: {prof['business']}
        - هدف: {prof.get('goal', 'نامشخص')}
        - ادعای کاربر: "{claim}"
        - احساس ویدیو: "{emotion}"
        - ایده انتخابی: (عنوان: {idea['title']}, قلاب: {idea['hook']})

        قوانین:
        ۱. دروغ نباف. 
        ۲. بخش "بدنه" توضیح منطقیِ "ادعای کاربر" باشد. 
        ۳. لحن کلمات منعکس‌کننده احساس "{emotion}" باشد.
        ۴. از عبارات کلیشه‌ای استفاده نکن.
        ۵. ستاره (*) نذار.

        ساختار خروجی:
        🎬 نقشه ساخت ریلز: {idea['title']}
        ۱. قلاب (۰-۵ ثانیه):
        تصویر: (مرتبط)
        متن روی صفحه: (کوتاه)
        نریشن: "{idea['hook']}"
        ۲. بدنه (۵-۲۰ ثانیه):
        تصویر: (توضیح)
        نریشن: (باز کردن ادعای کاربر با مکث [...])
        ۳. اقدام (۲۰-۲۵ ثانیه):
        تصویر: (پایانی)
        نریشن: (دعوت به اقدام منطبق با هدف)
        ---
        کپشن پیشنهادی: (۲ خط + سوال)
        """
        
        res = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}]).choices[0].message.content.replace('*', '')
        
        await context.bot.send_message(chat_id=update.effective_chat.id, text=res, reply_markup=get_feedback_and_dalle_keyboard('scenario'))
        log_event(str(update.effective_user.id), 'expansion_success', idea['title'])
    except Exception as e: 
        logger.error(f"Error in expansion: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ خطا در نوشتن سناریو.")
    
    return ConversationHandler.END

# --- اجرای ربات ---
if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler(['start', 'menu'], show_main_menu))
    application.add_handler(CommandHandler('admin', admin_start))
    application.add_handler(CallbackQueryHandler(handle_main_menu_buttons, pattern='^(menu_scenario|menu_quota|menu_upgrade_vip)$'))
    application.add_handler(CallbackQueryHandler(handle_admin_buttons, pattern='^(admin_stats|admin_monitor|admin_recent_users|admin_toggle_maintenance)$'))
    
    application.add_handler(CallbackQueryHandler(handle_feedback, pattern='^feedback_'))
    application.add_handler(CallbackQueryHandler(handle_dalle_trigger, pattern='^dalle_trigger_request$'))
    
    application.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern='^admin_broadcast_start$')],
        states={A_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_send)]},
        fallbacks=[CommandHandler('cancel', cancel_action)]
    ))
    
    application.add_handler(CallbackQueryHandler(handle_payment_verification, pattern='^(verify_payment_|reject_payment_)'))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler('profile', profile_start), CallbackQueryHandler(profile_start, pattern='^menu_profile$')],
        states={
            P_BUSINESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_business)],
            P_GOAL: [CallbackQueryHandler(get_goal, pattern='^goal_')],
            P_AUDIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_audience)],
            P_TONE: [CallbackQueryHandler(get_tone_and_save, pattern='^tone_')]
        },
        fallbacks=[CommandHandler('cancel', cancel_action), CallbackQueryHandler(cancel_action, pattern='^cancel$')]
    ))

    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler('hashtags', hashtag_start), CallbackQueryHandler(hashtag_start, pattern='^menu_hashtags$')],
        states={H_TOPIC: [MessageHandler((filters.TEXT | filters.VOICE) & ~filters.COMMAND, hashtag_generate)]},
        fallbacks=[CommandHandler('cancel', cancel_action)]
    ))

    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler('coach', coach_start), CallbackQueryHandler(coach_start, pattern='^menu_coach$')],
        states={C_TEXT: [MessageHandler((filters.TEXT | filters.VOICE) & ~filters.COMMAND, coach_analyze)]},
        fallbacks=[CommandHandler('cancel', cancel_action)]
    ))

    application.add_handler(ConversationHandler(
        entry_points=[MessageHandler((filters.TEXT | filters.VOICE) & ~filters.COMMAND, check_profile_before_content)],
        states={
            C_CLAIM: [MessageHandler((filters.TEXT | filters.VOICE) & ~filters.COMMAND, get_claim)],
            C_EMOTION: [CallbackQueryHandler(generate_ideas_after_emotion, pattern='^emo_')],
            EXPAND: [CallbackQueryHandler(expand_idea, pattern='^expand_')]
        },
        fallbacks=[CommandHandler('cancel', cancel_action), CallbackQueryHandler(cancel_action, pattern='^cancel$')]
    ))
    
    print("🤖 BOT DEPLOYED: NO-MARKDOWN POLICY APPLIED TO MESSAGES WITH USERNAMES!")
    application.run_polling()
