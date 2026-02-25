import os, logging, threading, json, asyncio, base64, math
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from openai import OpenAI
from supabase import create_client, Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler,
    filters, ConversationHandler, CallbackQueryHandler)

# --- تنظیمات لاگ و محیطی ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ADMIN_ID = os.environ.get("ADMIN_ID")

DAILY_LIMIT, REFERRAL_REWARD = 5, 3
CARD_NUMBER, CARD_NAME = "6118-2800-5587-6343", "امیراحمد شاه حسینی"
VIP_PRICE, SUPPORT_USERNAME = "۹۹,۰۰۰ تومان", "@Amir_shahosseini"
MAINTENANCE_MODE = False

# --- کلاینت‌های اصلی ---
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

# --- وضعیت‌های مکالمه (States) ---
(P_BUSINESS, P_GOAL, P_AUDIENCE, P_TONE, 
 C_TEXT, C_CLAIM, C_EMOTION, EXPAND, 
 H_TOPIC, SPY_TEXT, LOGO_STYLE_SELECT) = range(11)

# --- سرور Keep-Alive جهت جلوگیری از توقف (Exit Early) ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running...")

def run_fake_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler).serve_forever()

threading.Thread(target=run_fake_server, daemon=True).start()
# --- توابع کمکی امنیتی و بیزینسی ---
def is_admin(u_id): 
    return ADMIN_ID and str(u_id) == str(ADMIN_ID)

async def is_user_vip(u_id):
    if not supabase: return False
    try:
        res = supabase.table('profiles').select('is_vip').eq('user_id', str(u_id)).execute()
        return res.data[0]['is_vip'] if res.data else False
    except: return False

async def get_user_allowance(u_id):
    """محاسبه سهمیه: پایه + پاداش دعوت"""
    try:
        ref_count = supabase.table('profiles').select("id", count="exact").eq('referred_by', str(u_id)).execute().count or 0
        return DAILY_LIMIT + (ref_count * REFERRAL_REWARD)
    except: return DAILY_LIMIT

async def check_daily_limit(update, u_id):
    if is_admin(u_id) or await is_user_vip(u_id): return True
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        usage = supabase.table('logs').select("id", count="exact").in_('event_type', ['ideas_generated', 'hashtags_generated_success', 'coach_analyzed_success', 'dalle_generated']).gte('created_at', f"{today}T00:00:00Z").eq('user_id', str(u_id)).execute().count or 0
        allowance = await get_user_allowance(u_id)
        if usage >= allowance:
            kb = [[InlineKeyboardButton("🎁 سهمیه رایگان", callback_data='menu_referral')], [InlineKeyboardButton("💎 ارتقا به VIP", callback_data='menu_upgrade_vip')]]
            msg = f"⚠️ **سهمیه امروز تمام شد!**\n\nاستفاده امروز: {usage} از {allowance}\nبا دعوت از دوستان سهمیه بگیرید."
            target = update.callback_query.message if update.callback_query else update.message
            await target.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
            return False
        return True
    except: return True

def log_event(u_id, e_type, content=""):
    try: supabase.table('logs').insert({'user_id': str(u_id), 'event_type': e_type, 'content': content}).execute()
    except: pass

# --- مدیریت پروفایل (حل مشکل Conflict 409) ---
async def profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    u_id = str(update.effective_user.id)
    # بررسی وجود پروفایل از قبل
    res = supabase.table('profiles').select("*").eq('user_id', u_id).execute()
    
    if res.data and not context.user_data.get('re_editing'):
        p = res.data[0]
        msg = (f"👤 **پروفایل فعلی شما:**\n\n🏢 موضوع: {p['business']}\n🎯 هدف: {p['goal']}\n"
               f"👥 مخاطب: {p['audience']}\n🗣 لحن: {p['tone']}\n\nآیا قصد ویرایش دارید؟")
        kb = [[InlineKeyboardButton("📝 ویرایش پروفایل", callback_data='re_edit_profile')],
              [InlineKeyboardButton("🔙 بازگشت", callback_data='cancel')]]
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        return ConversationHandler.END

    await (update.callback_query.message if update.callback_query else update.message).reply_text("۱/۴ - موضوع اصلی پیج شما چیست؟")
    return P_BUSINESS

async def get_business(update, context):
    context.user_data['business'] = update.message.text
    kb = [[InlineKeyboardButton("فروش 💰", callback_data='goal_sales'), InlineKeyboardButton("برندسازی 📣", callback_data='goal_branding')],
          [InlineKeyboardButton("آموزش 🎓", callback_data='goal_edu'), InlineKeyboardButton("سرگرمی 🎭", callback_data='goal_fun')]]
    await update.message.reply_text("۲/۴ - هدف اصلی شما؟", reply_markup=InlineKeyboardMarkup(kb))
    return P_GOAL

async def get_goal(update, context):
    query = update.callback_query; await query.answer()
    context.user_data['goal'] = query.data
    await query.edit_message_text("۳/۴ - مخاطب هدف شما کیست؟")
    return P_AUDIENCE

async def get_audience(update, context):
    context.user_data['audience'] = update.message.text
    kb = [[InlineKeyboardButton("رسمی", callback_data='tone_formal'), InlineKeyboardButton("صمیمی", callback_data='tone_friendly')],
          [InlineKeyboardButton("طنز", callback_data='tone_funny'), InlineKeyboardButton("تخصصی", callback_data='tone_expert')]]
    await update.message.reply_text("۴/۴ - لحن برند شما؟", reply_markup=InlineKeyboardMarkup(kb))
    return P_TONE

async def get_tone_and_save(update, context):
    query = update.callback_query; await query.answer()
    u_id = str(update.effective_user.id)
    # آماده‌سازی دیتا برای ذخیره با متد upsert جهت رفع خطای 409
    data = {
        'user_id': u_id,
        'business': context.user_data['business'],
        'goal': context.user_data['goal'],
        'audience': context.user_data['audience'],
        'tone': query.data
    }
    try:
        # استفاده از upsert به جای insert برای جلوگیری از کرش
        supabase.table('profiles').upsert(data, on_conflict='user_id').execute()
        await query.edit_message_text("✅ پروفایل شما با موفقیت ذخیره/بروزرسانی شد! 🚀")
    except Exception as e:
        logger.error(f"Save Error: {e}")
        await query.edit_message_text("❌ خطا در ذخیره‌سازی اطلاعات.") # جلوگیری از نمایش خطای خام به کاربر
    
    context.user_data.clear()
    return ConversationHandler.END
        # --- تابع کمکی تبدیل تصویر به Base64 برای Vision AI ---
def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

# --- 🧠 مربی ایده و آنالیزور گرافیکی (Coach) ---
async def coach_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not supabase or not client:
        await (update.callback_query.message if update.callback_query else update.message).reply_text("❌ سرویس موقتاً در دسترس نیست.")
        return ConversationHandler.END
# --- تابع کمکی تبدیل تصویر به Base64 برای Vision AI ---
def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

# --- 🧠 مربی ایده و آنالیزور گرافیکی (Coach) ---
async def coach_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not supabase or not client:
        await (update.callback_query.message if update.callback_query else update.message).reply_text("❌ سرویس موقتاً در دسترس نیست.")
        return ConversationHandler.END
    
    msg = (
        "🧠 **مربی هوشمند ReelsMaster**\n\n"
        "۱. متن ایده خود را بفرستید (تایپ یا ویس) تا آن را نقد کنم.\n"
        "۲. **(ویژه VIP 💎)** عکس کاور خود را بفرستید تا از نظر گرافیک و جذابیت اکسپلور آن را تحلیل کنم."
    )
    target = update.callback_query.message if update.callback_query else update.message
    await target.reply_text(msg, parse_mode='Markdown')
    return C_TEXT

async def coach_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    u_id = str(update.effective_user.id)
    
    # الف) پردازش تصویر (فقط برای VIP)
    if update.message.photo:
        if not await is_user_vip(u_id) and not is_admin(u_id):
            await update.message.reply_text("🔒 **آنالیز بصری مخصوص کاربران VIP است.**\nبرای ارتقا به بخش 'ارتقا به VIP' بروید.")
            return ConversationHandler.END
        
        wait_msg = await update.message.reply_text("👁 در حال تحلیل گرافیکی تصویر شما...")
        try:
            photo_file = await context.bot.get_file(update.message.photo[-1].file_id)
            file_path = f"coach_{u_id}.jpg"
            await photo_file.download_to_drive(file_path)
            
            base64_image = encode_image(file_path)
            
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "تو یک متخصص ویرال شدن در اینستاگرام هستی. این تصویر کاور را از نظر خوانایی، ترکیب رنگ و پتانسیل جذب کلیک نقد کن. (بدون ستاره و فارسی)"},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                        ],
                    }
                ],
            )
            if os.path.exists(file_path): os.remove(file_path)
            await wait_msg.edit_text(response.choices[0].message.content.replace('*', ''))
            log_event(u_id, 'coach_vision_success')
        except Exception as e:
            logger.error(f"Vision Error: {e}")
            await wait_msg.edit_text("❌ خطا در تحلیل تصویر. مطمئن شوید فایل ارسالی عکس است.")
        return ConversationHandler.END

    # ب) پردازش متن یا ویس
    if not await check_daily_limit(update, u_id): return ConversationHandler.END
    
    content = await process_voice(update, context) if update.message.voice else update.message.text
    if not content: return ConversationHandler.END

    wait_msg = await update.message.reply_text("🧐 در حال کالبدشکافی ایده شما...")
    try:
        # دریافت اطلاعات پروفایل برای نقد دقیق‌تر
        prof_res = supabase.table('profiles').select("*").eq('user_id', u_id).execute()
        if not prof_res.data:
            await wait_msg.edit_text("⚠️ لطفاً ابتدا پروفایل خود را بسازید تا نقد دقیق‌تری دریافت کنید. /profile")
            return ConversationHandler.END
        
        prof = prof_res.data[0]
        prompt = f"به عنوان مربی محتوا برای بیزنس {prof['business']}، این ایده را نقد کن: {content}. (فارسی و بدون ستاره)"
        
        res = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
        await wait_msg.edit_text(res.choices[0].message.content.replace('*', ''))
        log_event(u_id, 'coach_analyzed_success', content[:50])
    except:
        await wait_msg.edit_text("❌ مشکلی در ارتباط با هوش مصنوعی پیش آمد.")
    
    return ConversationHandler.END

# --- 🏷 هشتگ‌ساز و تحلیل‌گر رقبا ---
async def hashtag_start(update, context):
    await (update.callback_query.message if update.callback_query else update.message).reply_text("🏷 موضوع پست خود را بفرستید تا هشتگ‌های هدفمند بسازم:")
    return H_TOPIC

async def hashtag_generate(update, context):
    u_id = str(update.effective_user.id)
    if not await check_daily_limit(update, u_id): return ConversationHandler.END
    
    topic = await process_voice(update, context) if update.message.voice else update.message.text
    wait = await update.message.reply_text("⏳ در حال استخراج بهترین هشتگ‌ها...")
    try:
        res = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": f"برای موضوع '{topic}' ۲۰ هشتگ فارسی و انگلیسی مرتبط و پربازدید بده."}]
        )
        await wait.edit_text(res.choices[0].message.content.replace('*', ''))
        log_event(u_id, 'hashtags_generated_success', topic)
    except: await wait.edit_text("❌ خطا در تولید هشتگ.")
    return ConversationHandler.END
# --- ۸. سناریوساز استراتژیک (Strategic Scenario Builder) ---

async def scenario_init(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    u_id = str(update.effective_user.id)
    if not await check_daily_limit(update, u_id): return ConversationHandler.END
    
    # اطمینان از وجود پروفایل قبل از شروع
    prof = supabase.table('profiles').select("*").eq('user_id', u_id).execute()
    if not prof.data:
        msg = "❌ برای ساخت سناریوی اختصاصی، ابتدا باید پروفایل خود را تکمیل کنید."
        kb = [[InlineKeyboardButton("👤 تکمیل پروفایل", callback_data='menu_profile')]]
        await (update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb)) if update.message else update.callback_query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb)))
        return ConversationHandler.END

    context.user_data['profile'] = prof.data[0]
    context.user_data['topic'] = await process_voice(update, context) if (update.message and update.message.voice) else (update.message.text if update.message else "موضوع عمومی")
    
    await (update.message.reply_text("🎯 **گام اول:** ادعای اصلی یا مهم‌ترین نکته‌ای که می‌خواهید در این ریلز بگویید چیست؟") if update.message else update.callback_query.message.reply_text("🎯 موضوع دریافت شد. ادعای اصلی شما چیست؟"))
    return C_CLAIM

async def get_claim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['claim'] = await process_voice(update, context) if update.message.voice else update.message.text
    
    kb = [
        [InlineKeyboardButton("امیدوارکننده و انگیزشی ✨", callback_data='emo_hope'), InlineKeyboardButton("جدی و هشدار دهنده ⚠️", callback_data='emo_warn')],
        [InlineKeyboardButton("طنز و سرگرمی 😂", callback_data='emo_fun'), InlineKeyboardButton("تخصصی و سنگین 🧠", callback_data='emo_expert')]
    ]
    await update.message.reply_text("🎭 **گام دوم:** دوست دارید حس و حال (Vibe) این ویدیو چطور باشد؟", reply_markup=InlineKeyboardMarkup(kb))
    return C_EMOTION

async def gen_ideas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    context.user_data['emotion'] = query.data
    u_id = str(update.effective_user.id)

    wait_msg = await query.message.reply_text("🔮 در حال طراحی ۳ استراتژی ویروسی برای شما...")
    try:
        topic = context.user_data['topic']
        claim = context.user_data['claim']
        prof = context.user_data['profile']
        
        prompt = (
            f"به عنوان یک استراتژیست اینستاگرام برای بیزنس '{prof['business']}'، "
            f"برای موضوع '{topic}' با ادعای '{claim}'، ۳ ایده ریلز متفاوت بساز.\n"
            f"۱. سبک آموزشی (Educational)\n۲. سبک POV (از زاویه دید مخاطب)\n۳. سبک ویروسی (Viral/Hook-based)\n"
            f"خروجی را فقط به صورت JSON با ساختار زیر بده: "
            f"{{'ideas': [{{'type': '...', 'title': '...', 'hook': '...'}}]}}"
        )

        res = client.chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}]
        )
        ideas = json.loads(res.choices[0].message.content)['ideas']
        context.user_data['ideas'] = ideas

        kb = [[InlineKeyboardButton(f"🎬 {id['type'].upper()}: {id['title']}", callback_data=f'expand_{i}')] for i, id in enumerate(ideas)]
        await wait_msg.edit_text("💎 **یکی از ۳ استراتژی زیر را برای دریافت سناریوی کامل انتخاب کنید:**", reply_markup=InlineKeyboardMarkup(kb))
        return EXPAND
    except Exception as e:
        logger.error(f"Gen Ideas Error: {e}")
        await wait_msg.edit_text("❌ خطایی در تولید ایده‌ها رخ داد. دوباره تلاش کنید.")
        return ConversationHandler.END

async def expand_scenario(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    idx = int(query.data.split('_')[1])
    idea = context.user_data['ideas'][idx]
    
    # ذخیره اطلاعات برای تولید کاور دال-ای در مرحله بعد
    context.user_data['dalle_topic'] = idea['title']
    context.user_data['dalle_style'] = idea['type']

    wait_msg = await query.message.reply_text(f"📝 در حال نوشتن سناریوی کامل برای سبک {idea['type']}...")
    try:
        prof = context.user_data['profile']
        prompt = (
            f"یک سناریوی ریلز کامل بنویس.\nسبک: {idea['type']}\nموضوع: {idea['title']}\n"
            f"قلاب (Hook): {idea['hook']}\nبیزنس: {prof['business']}\n"
            f"شامل: متن روی تصویر، نریشن (گوینده)، و توضیحات تصویر. (فارسی و بدون ستاره)"
        )
        
        script_res = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}])
        script = script_res.choices[0].message.content.replace('*', '')
        
        dur = estimate_duration(script)
        
        kb = [
            [InlineKeyboardButton("🎨 تولید کاور با هوش مصنوعی (VIP)", callback_data='dalle_trigger_request')],
            [InlineKeyboardButton("👍 عالی بود", callback_data='f_ok'), InlineKeyboardButton("👎 نیاز به اصلاح دارد", callback_data='f_no')],
            [InlineKeyboardButton("🔙 بازگشت به منو", callback_data='cancel')]
        ]
        
        await wait_msg.edit_text(f"{script}\n\n⏱ **زمان تخمینی:** {dur} ثانیه", reply_markup=InlineKeyboardMarkup(kb))
        log_event(str(update.effective_user.id), 'ideas_generated', idea['type'])
    except Exception as e:
        logger.error(f"Expand Error: {e}")
        await wait_msg.edit_text("❌ خطا در تولید سناریو.")
        
    return ConversationHandler.END

# --- ۹. تابع کمکی تخمین زمان سناریو ---
def estimate_duration(text):
    # به طور میانگین هر ۱۰۰ کلمه فارسی حدود ۴۰ ثانیه زمان می‌برد
    word_count = len(text.split())
    seconds = math.ceil(word_count / 2.5)
    return seconds
# --- ۱۰. سیستم زیرمجموعه‌گیری و VIP ---

async def show_referral_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u_id = update.effective_user.id
    bot_obj = await context.bot.get_me()
    bot_username = bot_obj.username
    
    # ساخت لینک دعوت اختصاصی
    referral_link = f"https://t.me/{bot_username}?start=ref_{u_id}"
    
    msg = (
        "🎁 **برنامه پاداش ReelsMaster**\n\n"
        f"با دعوت هر دوست به ربات، **{REFERRAL_REWARD} سهمیه رایگان** دریافت کنید.\n\n"
        f"🔗 **لینک اختصاصی شما:**\n`{referral_link}`"
    )
    target = update.callback_query.message if update.callback_query else update.message
    await target.reply_text(msg, parse_mode='Markdown')

async def upgrade_vip_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    
    msg = (
        "💎 **مزایای حساب VIP نامحدود:**\n"
        "✅ دسترسی به تحلیل گرافیکی کاور (Vision AI)\n"
        "✅ تولید کاور هوشمند با DALL-E 3\n"
        "✅ سهمیه روزانه نامحدود\n"
        "✅ حذف تمامی تبلیغات و محدودیت‌ها\n\n"
        f"💰 **هزینه اشتراک:** {VIP_PRICE}\n"
        f"💳 **شماره کارت:** `{CARD_NUMBER}`\n"
        f"👤 **به نام:** {CARD_NAME}\n\n"
        "👇 بعد از واریز، **عکس فیش** خود را همین‌جا بفرستید."
    )
    context.user_data['awaiting_receipt'] = True
    await query.message.reply_text(msg, parse_mode='Markdown')

async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت عکس‌های فیش واریزی ارسال شده توسط کاربر"""
    if context.user_data.get('awaiting_receipt'):
        user = update.effective_user
        # ارسال فیش برای ادمین جهت تایید
        kb = [
            [InlineKeyboardButton("✅ تایید و ارتقا", callback_data=f'v_p_{user.id}')],
            [InlineKeyboardButton("❌ رد رسید", callback_data=f'r_p_{user.id}')]
        ]
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=update.message.photo[-1].file_id,
            caption=f"💰 **رسید جدید پرداخت**\nاز: {user.first_name}\nآیدی: `{user.id}`",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode='Markdown'
        )
        await update.message.reply_text("⏳ رسید شما برای مدیریت ارسال شد. پس از تایید، حساب شما VIP خواهد شد.")
        context.user_data['awaiting_receipt'] = False
    else:
        # اگر عکس فیش نبود و ربات در وضعیت مربی ایده هم نبود
        await update.message.reply_text("برای تحلیل کاور، ابتدا وارد بخش '🧠 مربی ایده' شوید.")

async def handle_admin_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بخش تایید یا رد فیش توسط ادمین"""
    query = update.callback_query; await query.answer()
    if not is_admin(update.effective_user.id): return
    
    data = query.data.split('_') # v_p_123 or r_p_123
    action, _, target_uid = data[0], data[1], data[2]
    
    if action == 'v':
        # ارتقای کاربر به VIP در دیتابیس (رفع خطای احتمالی با استفاده از upsert)
        supabase.table('profiles').update({'is_vip': True}).eq('user_id', target_uid).execute()
        await context.bot.send_message(chat_id=target_uid, text="🎉 **تبریک! حساب شما به VIP ارتقا یافت.**")
        await query.edit_message_caption(caption="✅ رسید تایید شد و کاربر ارتقا یافت.")
    else:
        await context.bot.send_message(chat_id=target_uid, text="❌ رسید پرداخت شما توسط ادمین تایید نشد. در صورت بروز مشکل با پشتیبانی در ارتباط باشید.")
        await query.edit_message_caption(caption="❌ رسید رد شد.")

# --- ۱۱. منوی اصلی و کیبوردها ---

def get_main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 سناریوساز استراتژیک", callback_data='m_sc')],
        [InlineKeyboardButton("🧠 مربی ایده و آنالیزور", callback_data='menu_coach')],
        [InlineKeyboardButton("🏷 هشتگ‌ساز", callback_data='menu_hashtags'), InlineKeyboardButton("🕵️‍♂️ تحلیل رقبا", callback_data='menu_analyze')],
        [InlineKeyboardButton("👤 پروفایل بیزنس", callback_data='menu_profile'), InlineKeyboardButton("🎁 هدیه/دعوت", callback_data='menu_referral')],
        [InlineKeyboardButton("💎 ارتقا به VIP نامحدود", callback_data='menu_upgrade_vip')]
    ])

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "🚀 **به دستیار هوشمند ReelsMaster خوش آمدید!**\nابزار مورد نظر خود را انتخاب کنید:"
    if update.message:
        # بررسی ورود از طریق لینک دعوت
        if update.message.text.startswith('/start ref_'):
            referrer = update.message.text.split('ref_')[1]
            u_id = str(update.effective_user.id)
            if referrer != u_id:
                # ثبت معرف در دیتابیس برای پاداش (Upsert برای جلوگیری از تضاد)
                supabase.table('profiles').upsert({'user_id': u_id, 'referred_by': referrer}, on_conflict='user_id').execute()
        
        await update.message.reply_text(msg, reply_markup=get_main_menu_keyboard(), parse_mode='Markdown')
    elif update.callback_query:
        await update.callback_query.message.reply_text(msg, reply_markup=get_main_menu_keyboard(), parse_mode='Markdown')

# --- ۱۲. اجرای نهایی (Main) ---

if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # تنظیم هندلرهای عمومی
    application.add_handler(CommandHandler('start', show_main_menu))
    application.add_handler(CallbackQueryHandler(show_main_menu, pattern='^cancel$'))
    application.add_handler(CallbackQueryHandler(upgrade_vip_menu, pattern='^menu_upgrade_vip$'))
    application.add_handler(CallbackQueryHandler(show_referral_menu, pattern='^menu_referral$'))
    application.add_handler(CallbackQueryHandler(handle_admin_payment, pattern='^[vr]_p_'))

    # ۱. هندلر پروفایل (با منطق جدید برای رفع باگ 409)
    profile_handler = ConversationHandler(
        entry_points=[
            CommandHandler('profile', profile_start),
            CallbackQueryHandler(profile_start, pattern='^(menu_profile|re_edit_profile)$')
        ],
        states={
            P_BUSINESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_business)],
            P_GOAL: [CallbackQueryHandler(get_goal, pattern='^goal_')],
            P_AUDIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_audience)],
            P_TONE: [CallbackQueryHandler(get_tone_and_save, pattern='^tone_')]
        },
        fallbacks=[CommandHandler('cancel', show_main_menu)]
    )

    # ۲. هندلر مربی ایده (با اولویت بالای عکس)
    coach_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(coach_start, pattern='^menu_coach$')],
        states={C_TEXT: [MessageHandler(filters.PHOTO | filters.TEXT | filters.VOICE, coach_analyze)]},
        fallbacks=[CommandHandler('cancel', show_main_menu)]
    )

    # ۳. هندلر سناریوساز اصلی
    scenario_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(scenario_init, pattern='^m_sc$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, scenario_init)
        ],
        states={
            C_CLAIM: [MessageHandler(filters.TEXT | filters.VOICE, get_claim)],
            C_EMOTION: [CallbackQueryHandler(gen_ideas, pattern='^emo_')],
            EXPAND: [CallbackQueryHandler(expand_scenario, pattern='^expand_')]
        },
        fallbacks=[CommandHandler('cancel', show_main_menu)]
    )

    # افزودن هندلرها به ترتیب اولویت برای جلوگیری از تداخل
    application.add_handler(profile_handler)
    application.add_handler(coach_handler)
    application.add_handler(scenario_handler)
    application.add_handler(CallbackQueryHandler(handle_dalle_trigger, pattern='^dalle_trigger_request$'))
    application.add_handler(MessageHandler(filters.PHOTO, handle_receipt))

    print("🚀 ReelsMaster is running smoothly...")
    application.run_polling()
    
