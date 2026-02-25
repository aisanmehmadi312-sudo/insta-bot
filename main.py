import os, logging, threading, json, asyncio, base64, math
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from openai import OpenAI
from supabase import create_client, Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler,
    filters, ConversationHandler, CallbackQueryHandler)

# --- تنظیمات اولیه ---
logging.basicConfig(level=logging.INFO)
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

# --- اتصال به سرویس‌ها ---
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

# --- توابع کمکی و زیرساخت ---
def is_admin(u_id): return ADMIN_ID and str(u_id) == str(ADMIN_ID)

async def is_user_vip(u_id):
    try:
        res = supabase.table('profiles').select('is_vip').eq('user_id', str(u_id)).execute()
        return res.data[0]['is_vip'] if res.data else False
    except: return False

async def get_user_allowance(u_id):
    try:
        ref_count = supabase.table('profiles').select("id", count="exact").eq('referred_by', str(u_id)).execute().count or 0
        return DAILY_LIMIT + (ref_count * REFERRAL_REWARD)
    except: return DAILY_LIMIT

async def check_daily_limit(update, u_id):
    if is_admin(u_id) or await is_user_vip(u_id): return True
    today = datetime.now(timezone.utc).date().isoformat()
    usage = supabase.table('logs').select("id", count="exact").in_('event_type', ['ideas_generated', 'hashtags_generated_success', 'coach_analyzed_success', 'coach_vision_success', 'dalle_generated', 'competitor_analyzed']).gte('created_at', f"{today}T00:00:00Z").eq('user_id', str(u_id)).execute().count or 0
    allowance = await get_user_allowance(u_id)
    if usage >= allowance:
        kb = [[InlineKeyboardButton("🎁 سهمیه رایگان", callback_data='menu_referral')], [InlineKeyboardButton("💎 VIP", callback_data='menu_upgrade_vip')]]
        msg = f"⚠️ سهمیه امروز تمام شد ({usage}/{allowance}). با دعوت از دوستان سهمیه بگیرید!"
        await (update.callback_query.message if update.callback_query else update.message).reply_text(msg, reply_markup=InlineKeyboardMarkup(kb))
        return False
    return True

def log_event(u_id, e_type, content=""):
    try: supabase.table('logs').insert({'user_id': str(u_id), 'event_type': e_type, 'content': content}).execute()
    except: pass

def estimate_duration(text): return math.ceil(len(text.split()) / 2.5)

async def process_voice(update, context):
    file = await context.bot.get_file(update.message.voice.file_id)
    path = f"v_{update.effective_user.id}.ogg"
    await file.download_to_drive(path)
    with open(path, "rb") as f: trans = client.audio.transcriptions.create(model="whisper-1", file=f)
    if os.path.exists(path): os.remove(path)
    return trans.text

# --- مدیریت پروفایل (رفع باگ 409 و نمایش اطلاعات) ---
P_BUSINESS, P_GOAL, P_AUDIENCE, P_TONE = range(4)

async def profile_start(update, context):
    u_id = str(update.effective_user.id)
    res = supabase.table('profiles').select("*").eq('user_id', u_id).execute()
    if res.data:
        p = res.data[0]
        msg = f"👤 **پروفایل شما:**\n🏢 بیزنس: {p['business']}\n🎯 هدف: {p['goal']}\n👥 مخاطب: {p['audience']}\n🗣 لحن: {p['tone']}"
        kb = [[InlineKeyboardButton("📝 ویرایش پروفایل", callback_data='re_edit_profile')], [InlineKeyboardButton("🔙 بازگشت", callback_data='cancel')]]
        await (update.callback_query.message if update.callback_query else update.message).reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        return ConversationHandler.END
    await (update.callback_query.message if update.callback_query else update.message).reply_text("۱/۴ - موضوع اصلی پیج شما چیست؟")
    return P_BUSINESS

async def get_business(update, context):
    context.user_data['business'] = update.message.text
    kb = [[InlineKeyboardButton("فروش 💰", callback_data='goal_sales'), InlineKeyboardButton("برندسازی 📣", callback_data='goal_branding')], [InlineKeyboardButton("آموزش 🎓", callback_data='goal_edu'), InlineKeyboardButton("سرگرمی 🎭", callback_data='goal_fun')]]
    await update.message.reply_text("۲/۴ - هدف اصلی؟", reply_markup=InlineKeyboardMarkup(kb))
    return P_GOAL

async def get_goal(update, context):
    query = update.callback_query
    await query.answer(); context.user_data['goal'] = query.data
    await query.edit_message_text("۳/۴ - مخاطب هدف شما کیست؟")
    return P_AUDIENCE

async def get_audience(update, context):
    context.user_data['audience'] = update.message.text
    kb = [[InlineKeyboardButton("رسمی", callback_data='tone_formal'), InlineKeyboardButton("صمیمی", callback_data='tone_friendly')], [InlineKeyboardButton("طنز", callback_data='tone_funny'), InlineKeyboardButton("تخصصی", callback_data='tone_expert')]]
    await update.message.reply_text("۴/۴ - لحن برند؟", reply_markup=InlineKeyboardMarkup(kb))
    return P_TONE

async def get_tone_and_save(update, context):
    query = update.callback_query
    await query.answer()
    data = {'user_id': str(update.effective_user.id), 'business': context.user_data['business'], 'goal': context.user_data['goal'], 'audience': context.user_data['audience'], 'tone': query.data}
    try:
        supabase.table('profiles').upsert(data, on_conflict='user_id').execute() # رفع خطای 409
        await query.edit_message_text("✅ پروفایل با موفقیت ذخیره شد!")
    except: await query.edit_message_text("❌ خطا در ذخیره دیتابیس.")
    return ConversationHandler.END

# --- مربی ایده و آنالیزور گرافیکی ---
C_TEXT = 6
async def coach_start(update, context):
    await (update.callback_query.message if update.callback_query else update.message).reply_text("🧠 متن خود را بفرستید یا (ویژه VIP) عکس کاور را آپلود کنید:")
    return C_TEXT

async def coach_analyze(update, context):
    u_id = str(update.effective_user.id)
    if update.message.photo:
        if not await is_user_vip(u_id) and not is_admin(u_id):
            await update.message.reply_text("🔒 آنالیز عکس مخصوص VIP است."); return ConversationHandler.END
        file = await context.bot.get_file(update.message.photo[-1].file_id)
        path = f"c_{u_id}.jpg"; await file.download_to_drive(path)
        img = base64.b64encode(open(path, "rb").read()).decode('utf-8')
        res = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": [{"type": "text", "text": "نقد گرافیکی این کاور اینستاگرام (خوانایی و جذابیت)"}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}}]}])
        if os.path.exists(path): os.remove(path)
        await update.message.reply_text(res.choices[0].message.content.replace('*',''))
        return ConversationHandler.END
    # آنالیز متن
    text = await process_voice(update, context) if update.message.voice else update.message.text
    res = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": f"این ایده ریلز را نقد کن: {text}"}])
    await update.message.reply_text(res.choices[0].message.content.replace('*',''))
    return ConversationHandler.END

# --- سناریوساز استراتژیک (Educational, POV, Viral) ---
C_CLAIM, C_EMOTION, EXPAND = range(7, 10)
async def scenario_init(update, context):
    u_id = str(update.effective_user.id)
    if not await check_daily_limit(update, u_id): return ConversationHandler.END
    prof = supabase.table('profiles').select("*").eq('user_id', u_id).execute()
    if not prof.data: await update.message.reply_text("❌ ابتدا پروفایل بسازید /profile"); return ConversationHandler.END
    context.user_data['profile'] = prof.data[0]
    context.user_data['topic'] = await process_voice(update, context) if update.message.voice else update.message.text
    await update.message.reply_text("🎯 ادعای اصلی شما درباره این موضوع چیست؟")
    return C_CLAIM

async def get_claim(update, context):
    context.user_data['claim'] = await process_voice(update, context) if update.message.voice else update.message.text
    kb = [[InlineKeyboardButton("امیدوارکننده", callback_data='emo_hope'), InlineKeyboardButton("جدی/هشدار", callback_data='emo_warn')], [InlineKeyboardButton("طنز", callback_data='emo_fun')]]
    await update.message.reply_text("🎭 حس ویدیو؟", reply_markup=InlineKeyboardMarkup(kb))
    return C_EMOTION

async def gen_ideas(update, context):
    query = update.callback_query; await query.answer()
    context.user_data['emotion'] = query.data
    prompt = f"برای موضوع {context.user_data['topic']} ۳ ایده با سبک های educational، pov و viral بساز. خروجی فقط JSON: {{'ideas': [{{'type': '...', 'title': '...', 'hook': '...'}}]}}"
    res = client.chat.completions.create(model="gpt-4o", response_format={"type": "json_object"}, messages=[{"role": "user", "content": prompt}])
    ideas = json.loads(res.choices[0].message.content)['ideas']
    context.user_data['ideas'] = ideas
    kb = [[InlineKeyboardButton(f"🎬 سبک {id['type'].upper()}", callback_data=f'expand_{i}')] for i, id in enumerate(ideas)]
    await query.message.reply_text("💎 یکی از ۳ استراتژی را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(kb))
    return EXPAND

async def expand_scenario(update, context):
    query = update.callback_query; await query.answer()
    idx = int(query.data.split('_')[1])
    idea = context.user_data['ideas'][idx]
    context.user_data['dalle_topic'], context.user_data['dalle_style'] = idea['title'], idea['type']
    prompt = f"سناریو کامل برای سبک {idea['type']} با موضوع {idea['title']}. بیزنس: {context.user_data['profile']['business']}"
    script = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}]).choices[0].message.content
    dur = estimate_duration(script)
    kb = [[InlineKeyboardButton("👍", callback_data='f_ok'), InlineKeyboardButton("👎", callback_data='f_no')], [InlineKeyboardButton("🎨 تولید کاور (VIP)", callback_data='dalle_trigger_request')]]
    await query.message.reply_text(f"{script}\n\n⏱ زمان: {dur} ثانیه", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

# --- هندلرهای نهایی و اجرای بات ---
async def show_main_menu(update, context):
    kb = [[InlineKeyboardButton("🎬 سناریوساز", callback_data='m_sc'), InlineKeyboardButton("🧠 مربی ایده", callback_data='menu_coach')], [InlineKeyboardButton("👤 پروفایل", callback_data='menu_profile'), InlineKeyboardButton("🎁 هدیه", callback_data='menu_referral')]]
    await (update.message.reply_text("خوش آمدید! انتخاب کنید:" , reply_markup=InlineKeyboardMarkup(kb)) if update.message else update.callback_query.message.reply_text("منوی اصلی:", reply_markup=InlineKeyboardMarkup(kb)))

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler('start', show_main_menu))
    app.add_handler(CallbackQueryHandler(show_main_menu, pattern='^cancel$'))
    
    # ترتیب هندلرها برای حل مشکل تداخل عکس
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('coach', coach_start), CallbackQueryHandler(coach_start, pattern='^menu_coach$')],
        states={C_TEXT: [MessageHandler(filters.PHOTO | filters.TEXT | filters.VOICE, coach_analyze)]},
        fallbacks=[CommandHandler('cancel', show_main_menu)]
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('profile', profile_start), CallbackQueryHandler(profile_start, pattern='^menu_profile$'), CallbackQueryHandler(profile_start, pattern='^re_edit_profile$')],
        states={P_BUSINESS: [MessageHandler(filters.TEXT, get_business)], P_GOAL: [CallbackQueryHandler(get_goal)], P_AUDIENCE: [MessageHandler(filters.TEXT, get_audience)], P_TONE: [CallbackQueryHandler(get_tone_and_save)]},
        fallbacks=[CommandHandler('cancel', show_main_menu)]
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT | filters.VOICE, scenario_init), CallbackQueryHandler(scenario_init, pattern='^m_sc$')],
        states={C_CLAIM: [MessageHandler(filters.TEXT | filters.VOICE, get_claim)], C_EMOTION: [CallbackQueryHandler(gen_ideas)], EXPAND: [CallbackQueryHandler(expand_scenario)]},
        fallbacks=[CommandHandler('cancel', show_main_menu)]
    ))

    print("🚀 ReelsMaster Online!")
    app.run_polling()
