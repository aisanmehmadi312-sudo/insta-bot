import os
import logging
import threading
import json
import asyncio
import base64
import math
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

# --- متغیرهای محیطی و تنظیمات بیزینسی ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ADMIN_ID = os.environ.get("ADMIN_ID")

DAILY_LIMIT = 5  # سهمیه کاربران عادی
REFERRAL_REWARD = 3  # سهمیه هدیه به ازای هر دعوت
MAINTENANCE_MODE = False

# اطلاعات کارت جهت VIP
CARD_NUMBER = "6118-2800-5587-6343" 
CARD_NAME = "امیراحمد شاه حسینی"
VIP_PRICE = "۹۹,۰۰۰ تومان" 
SUPPORT_USERNAME = "@Amir_shahosseini"

# --- سرور وب جهت فعال نگه داشتن بات (Keep-Alive) ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ReelsMaster Bot is Running...")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_fake_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler).serve_forever()

threading.Thread(target=run_fake_server, daemon=True).start()

# --- اتصال به OpenAI و Supabase ---
client = None
if OPENAI_API_KEY:
    try: client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e: logger.error(f"OpenAI Error: {e}")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try: supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
  except Exception as e: logger.error(f"Supabase Error: {e}")
# --- توابع کمکی امنیتی و بیزینسی ---
def is_admin(user_id: int) -> bool: 
    return ADMIN_ID and str(user_id) == str(ADMIN_ID)

async def check_maintenance(update: Update) -> bool:
    if MAINTENANCE_MODE and not is_admin(update.effective_user.id):
        msg = "🛠 **ربات در حال بروزرسانی است!**\n\nلطفاً دقایقی دیگر دوباره تلاش کنید."
        if update.callback_query: await update.callback_query.answer("در حال بروزرسانی 🛠", show_alert=True)
        else: await update.message.reply_text(msg, parse_mode='Markdown')
        return True 
    return False 

async def check_services(update: Update) -> bool:
    if await check_maintenance(update): return False 
    if not supabase or not client:
        msg = "❌ سیستم در حال حاضر با مشکل ارتباطی روبروست."
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text(msg)
        return False
    return True

# --- مدیریت دیتابیس و لاگ ---
def log_event(user_id: str, event_type: str, content: str = ""):
    if not supabase: return
    try:
        supabase.table('logs').insert({'user_id': str(user_id), 'event_type': event_type, 'content': content}).execute()
    except Exception as e:
        logger.error(f"Log Error: {e}")

async def is_user_vip(user_id: str) -> bool:
    if not supabase: return False
    try:
        res = supabase.table('profiles').select('is_vip').eq('user_id', user_id).execute()
        return bool(res.data[0]['is_vip']) if res.data else False
    except: return False

async def get_user_allowance(user_id: str) -> int:
    """محاسبه سهمیه کاربر: سهمیه پایه + پاداش دعوت از دیگران"""
    if not supabase: return DAILY_LIMIT
    try:
        # شمارش تعداد افرادی که با لینک این کاربر عضو شده‌اند
        ref_count = supabase.table('profiles').select("id", count="exact").eq('referred_by', user_id).execute().count or 0
        return DAILY_LIMIT + (ref_count * REFERRAL_REWARD)
    except:
        return DAILY_LIMIT

async def check_daily_limit(update: Update, user_id: str) -> bool:
    if is_admin(int(user_id)) or await is_user_vip(user_id): return True 
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        # شمارش استفاده‌های امروز
        usage = supabase.table('logs').select("id", count="exact").in_('event_type', ['ideas_generated', 'hashtags_generated_success', 'coach_analyzed_success', 'coach_vision_success', 'dalle_generated', 'competitor_analyzed']).gte('created_at', f"{today}T00:00:00Z").eq('user_id', user_id).execute().count or 0
        
        allowance = await get_user_allowance(user_id)
        
        if usage >= allowance:
            msg = (
                f"⚠️ **سهمیه روزانه شما به پایان رسید!**\n\n"
                f"شما امروز {usage} درخواست از {allowance} سهمیه مجاز خود را استفاده کردید.\n\n"
                f"✅ **راه‌های افزایش سهمیه:**\n"
                f"1️⃣ خرید اشتراک VIP (نامحدود + DALL-E)\n"
                f"2️⃣ دعوت از دوستان (به ازای هر نفر **{REFERRAL_REWARD}+ سهمیه** هدیه)"
            )
            kb = [
                [InlineKeyboardButton("🎁 دریافت لینک دعوت", callback_data='menu_referral')],
                [InlineKeyboardButton("💎 ارتقا به VIP", callback_data='menu_upgrade_vip')]
            ]
            target = update.callback_query.message if update.callback_query else update.message
            await target.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
            return False
        return True
    except: return True

# --- توابع ابزاری ---
def estimate_duration(text: str) -> int:
    """تخمین زمان ویدیو: به طور متوسط هر ۲.۵ کلمه در ثانیه"""
    words = len(text.split())
    return math.ceil(words / 2.5)

async def process_voice_to_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    wait_msg = await update.message.reply_text("🎙 در حال شنیدن صدای شما...")
    try:
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        file_path = f"temp_{update.effective_user.id}.ogg"
        await voice_file.download_to_drive(file_path)
        with open(file_path, "rb") as audio:
            trans = client.audio.transcriptions.create(model="whisper-1", file=audio)
        if os.path.exists(file_path): os.remove(file_path)
        await wait_msg.delete()
        return trans.text
    except Exception as e:
        await wait_msg.edit_text("❌ متوجه صدا نشدم، لطفاً تایپ کنید.")
        return None

def encode_image(image_path):
    with open(image_path, "rb") as f: return base64.b64encode(f.read()).decode('utf-8')

def get_feedback_keyboard(context_name: str):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👍 عالی", callback_data=f'feedback_like_{context_name}'),
        InlineKeyboardButton("👎 ضعیف", callback_data=f'feedback_dislike_{context_name}')
    ]])
# --- 👑 پنل ادمین و مدیریت تراکنش‌ها ---
A_BROADCAST = 10
# مراحل ساخت پروفایل
P_BUSINESS, P_GOAL, P_AUDIENCE, P_TONE = range(4)

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
    await update.message.reply_text("👑 **پنل مدیریت ربات**", reply_markup=get_admin_keyboard(), parse_mode='Markdown')

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
            await query.message.reply_text(f"📊 **آمار:**\n👥 کل کاربران: {total_users}\n💎 کاربران ویژه: {vip_users}", parse_mode='Markdown')
        except: await query.message.reply_text("❌ خطا در دریافت آمار.")

# --- تایید فیش واریزی توسط ادمین ---
async def handle_payment_verification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update.effective_user.id): return
    
    action, _, target_id = query.data.split('_')
    if action == 'verify':
        try:
            supabase.table('profiles').update({'is_vip': True}).eq('user_id', target_id).execute()
            await query.edit_message_caption(caption=f"{query.message.caption}\n\n✅ تایید شد. کاربر VIP شد.")
            await context.bot.send_message(chat_id=target_id, text="🎉 تبریک! حساب شما به **VIP 💎** ارتقا یافت.", parse_mode='Markdown')
        except: await query.answer("خطا در دیتابیس")
    elif action == 'reject':
        await query.edit_message_caption(caption=f"{query.message.caption}\n\n❌ رد شد.")
        await context.bot.send_message(chat_id=target_id, text="❌ رسید شما مورد تایید قرار نگرفت.")

# --- 👤 سیستم ساخت پروفایل کاربر ---
async def profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_services(update): return ConversationHandler.END
    msg = "۱/۴ - موضوع اصلی پیج شما چیست؟ (مثلاً: فروش موبایل، آموزش آشپزی)"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(msg)
    else: await update.message.reply_text(msg)
    return P_BUSINESS

async def get_business(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['business'] = update.message.text
    kb = [[InlineKeyboardButton("فروش مستقیم 💰", callback_data='goal_sales'), InlineKeyboardButton("برندسازی 📣", callback_data='goal_branding')],
          [InlineKeyboardButton("آموزش 🎓", callback_data='goal_edu'), InlineKeyboardButton("سرگرمی 🎭", callback_data='goal_fun')]]
    await update.message.reply_text("۲/۴ - هدف اصلی شما از تولید محتوا چیست؟", reply_markup=InlineKeyboardMarkup(kb))
    return P_GOAL

async def get_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['goal'] = query.data
    await query.edit_message_text(f"✅ هدف ثبت شد.")
    await context.bot.send_message(chat_id=update.effective_chat.id, text="۳/۴ - مخاطب هدف شما چه کسانی هستند؟ (مثلاً: خانم‌های خانه‌دار، نوجوانان علاقه‌مند به تکنولوژی)")
    return P_AUDIENCE

async def get_audience(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['audience'] = update.message.text
    kb = [[InlineKeyboardButton("رسمی و جدی", callback_data='tone_formal'), InlineKeyboardButton("دوستانه و صمیمی", callback_data='tone_friendly')],
          [InlineKeyboardButton("طنز و شوخ", callback_data='tone_funny'), InlineKeyboardButton("آموزشی و تخصصی", callback_data='tone_expert')]]
    await update.message.reply_text("۴/۴ - لحن برند شما چگونه باشد؟", reply_markup=InlineKeyboardMarkup(kb))
    return P_TONE

async def get_tone_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['tone'] = query.data
    user_id = str(update.effective_user.id)
    
    try:
        data = {
            'user_id': user_id,
            'business': context.user_data['business'],
            'goal': context.user_data['goal'],
            'audience': context.user_data['audience'],
            'tone': context.user_data['tone']
        }
        supabase.table('profiles').upsert(data).execute()
        await query.edit_message_text("✅ پروفایل شما با موفقیت ساخته شد! حالا می‌توانید تولید محتوا را شروع کنید.")
    except:
        await query.edit_message_text("❌ خطا در ذخیره پروفایل. لطفاً دوباره تلاش کنید.")
    
    return ConversationHandler.END

async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("عملیات لغو شد.")
    return ConversationHandler.END
# --- 🏷 بخش هشتگ‌ساز هوشمند ---
H_TOPIC = 5

async def hashtag_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_services(update): return ConversationHandler.END
    msg = "🏷 **هشتگ‌ساز هوشمند!**\n\nموضوع پست خود را تایپ یا **ویس** کنید تا بهترین هشتگ‌های مرتبط و طبقه‌بندی شده را برایتان بسازم:"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(msg, parse_mode='Markdown')
    else: 
        await update.message.reply_text(msg, parse_mode='Markdown')
    return H_TOPIC

async def hashtag_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = str(update.effective_user.id)
    if not await check_daily_limit(update, uid): return ConversationHandler.END
    
    # دریافت موضوع (متن یا ویس)
    if update.message.voice:
        topic = await process_voice_to_text(update, context)
        if not topic: return ConversationHandler.END
        await update.message.reply_text(f"🗣 **موضوع شما:** {topic}", parse_mode='Markdown')
    else: topic = update.message.text
    
    wait_msg = await update.message.reply_text("⏳ در حال آنالیز و استخراج هشتگ‌های پربازدید...")
    
    try:
        # دریافت اطلاعات بیزینس برای شخصی‌سازی هشتگ‌ها
        prof = supabase.table('profiles').select("*").eq('user_id', uid).execute().data[0]
        
        prompt = f"""
        شخصیت: متخصص سئو و هشتگ‌گذاری اینستاگرام در ایران.
        موضوع پست: {topic}
        حوزه کاری پیج: {prof['business']}
        
        ماموریت: ۳ دسته هشتگ (مجموعاً ۱۵ تا ۲۰ عدد) به زبان فارسی و انگلیسی (در صورت نیاز) تولید کن.
        فرمت خروجی (JSON):
        {{
            "hashtags": "🎯 پربازدید:\\n#هشتگ1 #هشتگ2...\\n\\n🔬 تخصصی حوزه {prof['business']}:\\n#هشتگ3...\\n\\n🤝 تعاملی:\\n#هشتگ4..."
        }}
        """
        response = client.chat.completions.create(
            model="gpt-4o", 
            response_format={"type": "json_object"}, 
            messages=[{"role": "user", "content": prompt}]
        )
        res_data = json.loads(response.choices[0].message.content)
        
        await wait_msg.edit_text(res_data['hashtags'], reply_markup=get_feedback_keyboard('hashtag'))
        log_event(uid, 'hashtags_generated_success', topic)
    except:
        await wait_msg.edit_text("❌ متاسفانه در تولید هشتگ مشکلی پیش آمد.")
    
    return ConversationHandler.END

# --- 🕵️‍♂️ بخش تحلیل‌گر مهندسی معکوس (Competitor Spy) ---
SPY_TEXT = 11

async def analyze_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_services(update): return ConversationHandler.END
    msg = (
        "🕵️‍♂️ **ابزار مهندسی معکوس محتوا!**\n\n"
        "متنِ یک ریلز موفق یا وایرال شده را اینجا بفرستید (تایپ یا ویس).\n"
        "من فرمولِ پنهانِ آن را کشف می‌کنم و یک ایده مشابه برای کسب‌وکار خودتان می‌سازم!"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(msg, parse_mode='Markdown')
    else:
        await update.message.reply_text(msg, parse_mode='Markdown')
    return SPY_TEXT

async def analyze_competitor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = str(update.effective_user.id)
    if not await check_daily_limit(update, uid): return ConversationHandler.END
    
    if update.message.voice:
        competitor_text = await process_voice_to_text(update, context)
        if not competitor_text: return ConversationHandler.END
    else: 
        competitor_text = update.message.text

    wait_msg = await update.message.reply_text("🕵️‍♂️ در حال کالبدشکافی محتوا...")
    
    try:
        prof = supabase.table('profiles').select("*").eq('user_id', uid).execute().data[0]
        
        prompt = f"""
        شخصیت: هکر رشد (Growth Hacker) اینستاگرام.
        متن رقیب: "{competitor_text}"
        بیزینس کاربر: {prof['business']}
        
        تحلیل کن:
        ۱. روانشناسی قلاب: چرا مخاطب با این شروع متوقف شده؟
        ۲. ساختار بدنه: اطلاعات چطور چیده شده؟
        ۳. بومی‌سازی: حالا دقیقاً همین فرمول را برای بیزینس "{prof['business']}" استفاده کن و یک ایده جدید بده.
        
        بدون ستاره و با زبان فارسی روان پاسخ بده.
        """
        response = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
        ai_reply = response.choices[0].message.content.strip().replace('*', '')
        
        await wait_msg.edit_text(ai_reply, reply_markup=get_feedback_keyboard('analyze'))
        log_event(uid, 'competitor_analyzed', topic[:50] if 'topic' in locals() else "Competitor Analysis")
    except:
        await wait_msg.edit_text("❌ مشکلی در آنالیز محتوا پیش آمد.")
    
    return ConversationHandler.END
# --- 🧠 بخش مربی ایده و آنالیزور گرافیکی (Coach 2.0) ---
C_TEXT = 6

async def coach_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_services(update): return ConversationHandler.END
    msg = (
        "🧠 **مربی ایده و آنالیزگر بصری!**\n\n"
        "۱. ایده یا متن خود را بفرستید (تایپ یا ویس) تا آن را نقد کنم.\n"
        "۲. **(ویژه VIP 💎)** عکس کاور یا پست خود را بفرستید تا از نظر گرافیک و جذابیت اکسپلور آن را تحلیل کنم."
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(msg, parse_mode='Markdown')
    else: await update.message.reply_text(msg, parse_mode='Markdown')
    return C_TEXT

async def coach_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = str(update.effective_user.id)
    
    # الف) پردازش تصویر (Vision AI)
    if update.message.photo:
        if not await is_user_vip(uid) and not is_admin(int(uid)):
            await update.message.reply_text("🔒 **آنالیز بصری مخصوص کاربران VIP است.**", parse_mode='Markdown')
            return ConversationHandler.END
        
        wait_msg = await update.message.reply_text("👁 در حال تحلیل گرافیکی تصویر...")
        try:
            photo_file = await context.bot.get_file(update.message.photo[-1].file_id)
            file_path = f"coach_{uid}.jpg"
            await photo_file.download_to_drive(file_path)
            base64_image = encode_image(file_path)
            
            prompt = "تو یک طراح گرافیک برتر اینستاگرام هستی. این تصویر را از نظر: ۱. خوانایی متن ۲. جذابیت رنگی ۳. چیدمان برای اکسپلور نقد کن و پیشنهاد اصلاحی بده. (بدون ستاره و فارسی)"
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]}]
            )
            if os.path.exists(file_path): os.remove(file_path)
            await wait_msg.edit_text(response.choices[0].message.content.replace('*', ''), reply_markup=get_feedback_keyboard('coach_v'))
            log_event(uid, 'coach_vision_success')
        except: await wait_msg.edit_text("❌ خطا در تحلیل تصویر.")
        return ConversationHandler.END

    # ب) پردازش متن یا ویس
    if not await check_daily_limit(update, uid): return ConversationHandler.END
    idea = await process_voice_to_text(update, context) if update.message.voice else update.message.text
    if not idea: return ConversationHandler.END

    wait_msg = await update.message.reply_text("🧐 در حال نقد ایده شما...")
    try:
        prof = supabase.table('profiles').select("*").eq('user_id', uid).execute().data[0]
        prompt = f"به عنوان یک مربی محتوا، این ایده را برای بیزینس {prof['business']} نقد کن. نقاط قوت، ضعف و یک پیشنهاد طلایی بده. (فارسی و بدون ستاره)"
        response = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
        await wait_msg.edit_text(response.choices[0].message.content.replace('*', ''), reply_markup=get_feedback_keyboard('coach_t'))
        log_event(uid, 'coach_analyzed_success', idea[:50])
    except: await wait_msg.edit_text("❌ خطا در آنالیز ایده.")
    return ConversationHandler.END

# --- 🚀 سناریوساز استراتژیک (۳ مرحله‌ای) ---
C_CLAIM, C_EMOTION, EXPAND = range(7, 10)

async def check_profile_before_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = str(update.effective_user.id)
    if not await check_services(update) or not await check_daily_limit(update, uid): return ConversationHandler.END
    try:
        prof_res = supabase.table('profiles').select("*").eq('user_id', uid).execute()
        if not prof_res.data:
            await update.message.reply_text("❌ ابتدا پروفایل خود را تنظیم کنید /profile")
            return ConversationHandler.END
        context.user_data['profile'] = prof_res.data[0]
        context.user_data['topic'] = await process_voice_to_text(update, context) if update.message.voice else update.message.text
        await update.message.reply_text("🎯 **مرحله ۱ از ۲:** ادعا یا نظر خاص شما درباره این موضوع چیست؟ (مثلاً: راه حل من اینه که...)")
        return C_CLAIM
    except: return ConversationHandler.END

async def get_claim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['claim'] = await process_voice_to_text(update, context) if update.message.voice else update.message.text
    kb = [[InlineKeyboardButton("امیدوارکننده ✨", callback_data='emo_hope'), InlineKeyboardButton("هشداردهنده ⚠️", callback_data='emo_warn')],
          [InlineKeyboardButton("طنز 😂", callback_data='emo_fun'), InlineKeyboardButton("قاطع و علمی 🧠", callback_data='emo_logic')]]
    await update.message.reply_text("🎭 **مرحله ۲ از ۲:** دوست دارید مخاطب چه حسی از ویدیو بگیرد؟", reply_markup=InlineKeyboardMarkup(kb))
    return C_EMOTION

async def generate_ideas_after_emotion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['emotion'] = query.data
    prof, topic, claim = context.user_data['profile'], context.user_data['topic'], context.user_data['claim']
    
    await query.edit_message_text("⏳ در حال طراحی ۳ استراتژی متفاوت (آموزشی، POV، جنجالی)...")
    try:
        prompt = f"برای موضوع {topic} و ادعای {claim}، ۳ ایده با رویکردهای educational، pov و viral بساز. خروجی فقط JSON باشد: {{'ideas': [{{'type': '...', 'title': '...', 'hook': '...'}}]}}"
        res = client.chat.completions.create(model="gpt-4o", response_format={"type": "json_object"}, messages=[{"role": "user", "content": prompt}])
        ideas = json.loads(res.choices[0].message.content)['ideas']
        context.user_data['ideas'] = ideas
        
        kb = [[InlineKeyboardButton(f"🎬 سبک {item['type'].upper()}", callback_data=f'expand_{i}')] for i, item in enumerate(ideas)]
        msg = "\n".join([f"{i+1}. **{item['type']}**: {item['title']}\nقلاب: {item['hook']}" for i, item in enumerate(ideas)])
        await query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        return EXPAND
    except: return ConversationHandler.END

async def expand_idea(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split('_')[1])
    idea = context.user_data['ideas'][idx]
    prof, claim, emotion = context.user_data['profile'], context.user_data['claim'], context.user_data['emotion']
    
    await query.edit_message_text(f"⏳ در حال بسط سناریوی {idea['type']}...")
    try:
        # ذخیره متغیرها برای DALL-E در مرحله بعد
        context.user_data['dalle_topic'] = idea['title']
        context.user_data['dalle_style'] = idea['type']
        
        prompt = f"یک سناریوی کامل اینستاگرام برای سبک {idea['type']} بنویس. شامل قلاب، بدنه و دعوت به اقدام. بیزینس: {prof['business']}. (بدون ستاره)"
        script = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}]).choices[0].message.content
        
        duration = estimate_duration(script)
        res_msg = f"{script}\n\n⏱ **زمان تقریبی:** {duration} ثانیه\n🎵 **پیشنهاد موزیک:** مطابق با سبک {idea['type']}"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=res_msg, reply_markup=get_feedback_and_dalle_keyboard('scenario'))
    except: await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ خطا")
    return ConversationHandler.END
  # --- 🎨 تابع کمکی کیبورد خروجی سناریو ---
def get_feedback_and_dalle_keyboard(context_name: str):
    keyboard = [
        [InlineKeyboardButton("👍 عالی بود", callback_data=f'feedback_like_{context_name}'),
         InlineKeyboardButton("👎 نیاز به اصلاح دارد", callback_data=f'feedback_dislike_{context_name}')],
        [InlineKeyboardButton("🎨 تولید تصویر کاور (ویژه VIP 💎)", callback_data='dalle_trigger_request')]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- 🎁 سیستم زیرمجموعه‌گیری (Referral) ---
async def show_referral_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    
    msg = (
        "🎁 **برنامه دعوت از دوستان!**\n\n"
        f"با دعوت از هر دوست، **{REFERRAL_REWARD} سهمیه رایگان** دریافت کنید.\n\n"
        "🔗 لینک اختصاصی شما:\n"
        f"`{referral_link}`\n\n"
        "این لینک را برای دوستان یا در گروه‌های خود بفرستید."
    )
    if update.callback_query: await update.callback_query.message.reply_text(msg, parse_mode='Markdown')
    else: await update.message.reply_text(msg, parse_mode='Markdown')

# --- 🎨 تولید تصویر با DALL-E 3 (هماهنگ با سبک سناریو) ---
async def handle_dalle_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(update.effective_user.id)
    
    if not await is_user_vip(uid) and not is_admin(int(uid)):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="💎 این قابلیت مخصوص کاربران VIP است.")
        return

    topic = context.user_data.get('dalle_topic', 'Instagram Reel Cover')
    style = context.user_data.get('dalle_style', 'general')
    
    wait_msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"🎨 در حال طراحی کاور سبک {style}...")
    try:
        # تنظیم پرامپت بر اساس سبک
        styles = {
            "educational": "Clean, professional, bright lighting, focus on objects.",
            "pov": "Authentic, candid, mobile photo style, relatable environment.",
            "viral": "Dramatic, high contrast, vibrant, eye-catching composition."
        }
        visual_style = styles.get(style, "High quality cinematic.")
        
        prompt = f"Create a vertical 9:16 Instagram Reel cover about '{topic}'. Style: {visual_style}. NO TEXT, NO LETTERS."
        response = client.images.generate(model="dall-e-3", prompt=prompt, size="1024x1792", quality="hd", n=1)
        
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=response.data[0].url, caption=f"🎨 کاور پیشنهادی ({style})")
        await wait_msg.delete()
        log_event(uid, 'dalle_generated', topic)
    except: await wait_msg.edit_text("❌ خطا در تولید تصویر.")

# --- 📂 بخش تاریخچه و فیش‌های واریزی ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هندلر عمومی عکس برای فیش‌های واریزی (اولویت آخر)"""
    if context.user_data.get('awaiting_receipt'):
        user = update.effective_user
        caption = f"💰 رسید جدید!\n👤 {user.first_name}\n🆔 `{user.id}`"
        kb = [[InlineKeyboardButton("✅ تایید", callback_data=f'verify_pay_{user.id}'),
               InlineKeyboardButton("❌ رد", callback_data=f'reject_pay_{user.id}')]]
        await context.bot.send_photo(chat_id=ADMIN_ID, photo=update.message.photo[-1].file_id, caption=caption, reply_markup=InlineKeyboardMarkup(kb))
        context.user_data['awaiting_receipt'] = False
        await update.message.reply_text("⏳ رسید ارسال شد، منتظر تایید ادمین بمانید.")
    else:
        await update.message.reply_text("لطفاً برای شروع تولید محتوا از منو یا ویس استفاده کنید.")

# ---------------------------------------------
# --- 🚀 بخش اصلی: پیکربندی و اجرای ربات ---
# ---------------------------------------------
if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # ۱. دستورات پایه
    application.add_handler(CommandHandler(['start', 'menu'], show_main_menu))
    application.add_handler(CommandHandler('admin', admin_start))
    
    # ۲. هندلرهای کلیک (Callback)
    application.add_handler(CallbackQueryHandler(handle_main_menu_buttons, pattern='^(menu_scenario|menu_quota|menu_upgrade_vip|menu_referral)$'))
    application.add_handler(CallbackQueryHandler(handle_admin_buttons, pattern='^admin_'))
    application.add_handler(CallbackQueryHandler(handle_payment_verification, pattern='^(verify_pay_|reject_pay_)'))
    application.add_handler(CallbackQueryHandler(handle_dalle_trigger, pattern='^dalle_trigger_request$'))
    application.add_handler(CallbackQueryHandler(handle_feedback, pattern='^feedback_'))

    # ۳. مکالمات (Conversation Handlers)
    # الف) پروفایل
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler('profile', profile_start), CallbackQueryHandler(profile_start, pattern='^menu_profile$')],
        states={P_BUSINESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_business)],
                P_GOAL: [CallbackQueryHandler(get_goal, pattern='^goal_')],
                P_AUDIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_audience)],
                P_TONE: [CallbackQueryHandler(get_tone_and_save, pattern='^tone_')]},
        fallbacks=[CommandHandler('cancel', cancel_action)]
    ))

    # ب) مربی ایده (بسیار مهم: PHOTO در اینجا قبل از هندلر عمومی است)
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler('coach', coach_start), CallbackQueryHandler(coach_start, pattern='^menu_coach$')],
        states={C_TEXT: [MessageHandler((filters.TEXT | filters.VOICE | filters.PHOTO) & ~filters.COMMAND, coach_analyze)]},
        fallbacks=[CommandHandler('cancel', cancel_action)]
    ))

    # ج) سایر ابزارها
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler('hashtags', hashtag_start), CallbackQueryHandler(hashtag_start, pattern='^menu_hashtags$')],
        states={H_TOPIC: [MessageHandler((filters.TEXT | filters.VOICE) & ~filters.COMMAND, hashtag_generate)]},
        fallbacks=[CommandHandler('cancel', cancel_action)]
    ))
    
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler('analyze', analyze_start), CallbackQueryHandler(analyze_start, pattern='^menu_analyze$')],
        states={SPY_TEXT: [MessageHandler((filters.TEXT | filters.VOICE) & ~filters.COMMAND, analyze_competitor)]},
        fallbacks=[CommandHandler('cancel', cancel_action)]
    ))

    # د) سناریوساز اصلی
    application.add_handler(ConversationHandler(
        entry_points=[MessageHandler((filters.TEXT | filters.VOICE) & ~filters.COMMAND, check_profile_before_content)],
        states={C_CLAIM: [MessageHandler((filters.TEXT | filters.VOICE) & ~filters.COMMAND, get_claim)],
                C_EMOTION: [CallbackQueryHandler(generate_ideas_after_emotion, pattern='^emo_')],
                EXPAND: [CallbackQueryHandler(expand_idea, pattern='^expand_')]},
        fallbacks=[CommandHandler('cancel', cancel_action)]
    ))

    # ۴. هندلر عمومی عکس (فقط اگر هیچکدام از بالایی‌ها عکس را نگرفتند)
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    print("🤖 REELS MASTER BOT IS ONLINE AND ARMED!")
    application.run_polling()
    
