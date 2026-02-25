import os, logging, threading, json, asyncio, base64, math
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from openai import OpenAI
from supabase import create_client, Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler,
    filters, ConversationHandler, CallbackQueryHandler)

# --- ۱. تنظیمات و اتصال به سرویس‌ها ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ADMIN_ID = os.environ.get("ADMIN_ID")

DAILY_LIMIT, REFERRAL_REWARD = 5, 3
LOGO_STYLES_PROMPTS = {
    'ls_minimal': 'Minimalist, clean geometric lines, modern, sleek icon. Flat design.',
    'ls_organic': 'Hand-drawn, watercolor texture, organic shapes, friendly and natural feel.',
    'ls_emblem': 'Emblem style badge, bold lines, vintage seal concept, strong and assertive.'
}

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

# --- ۲. وضعیت‌های مکالمه (States) ---
(P_BUSINESS, P_GOAL, P_AUDIENCE, P_TONE, 
 C_TEXT, C_CLAIM, C_EMOTION, EXPAND, 
 H_TOPIC, SPY_TEXT, LOGO_STYLE_SELECT) = range(11)

# --- ۳. توابع کمکی (Helpers) ---
def is_admin(u_id): return ADMIN_ID and str(u_id) == str(ADMIN_ID)

async def is_user_vip(u_id):
    try:
        res = supabase.table('profiles').select('is_vip').eq('user_id', str(u_id)).execute()
        return res.data[0]['is_vip'] if res.data else False
    except: return False

def log_event(u_id, e_type, content=""):
    try: supabase.table('logs').insert({'user_id': str(u_id), 'event_type': e_type, 'content': content}).execute()
    except: pass

async def process_voice(update, context):
    wait = await update.message.reply_text("🎙 در حال پردازش صدا...")
    file = await context.bot.get_file(update.message.voice.file_id)
    path = f"v_{update.effective_user.id}.ogg"
    await file.download_to_drive(path)
    with open(path, "rb") as f: trans = client.audio.transcriptions.create(model="whisper-1", file=f)
    if os.path.exists(path): os.remove(path)
    await wait.delete()
    return trans.text

# --- ۴. مدیریت پروفایل (نمایش، ویرایش و رفع باگ 409) ---
async def profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u_id = str(update.effective_user.id)
    res = supabase.table('profiles').select("*").eq('user_id', u_id).execute()
    if res.data and not context.user_data.get('re_editing'):
        p = res.data[0]
        msg = f"👤 **پروفایل بیزینسی شما:**\n\n🏢 موضوع: {p['business']}\n🎯 هدف: {p['goal']}\n👥 مخاطب: {p['audience']}\n🗣 لحن: {p['tone']}"
        kb = [[InlineKeyboardButton("📝 ویرایش اطلاعات", callback_data='re_edit_profile')], [InlineKeyboardButton("🔙 بازگشت", callback_data='cancel')]]
        await (update.callback_query.message if update.callback_query else update.message).reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        return ConversationHandler.END
    
    await (update.callback_query.message if update.callback_query else update.message).reply_text("۱/۴ - موضوع اصلی پیج شما چیست؟")
    return P_BUSINESS

async def get_business(update, context):
    context.user_data['business'] = update.message.text
    kb = [[InlineKeyboardButton("فروش 💰", callback_data='goal_sales'), InlineKeyboardButton("برندسازی 📣", callback_data='goal_branding')], [InlineKeyboardButton("آموزش 🎓", callback_data='goal_edu'), InlineKeyboardButton("سرگرمی 🎭", callback_data='goal_fun')]]
    await update.message.reply_text("۲/۴ - هدف شما؟", reply_markup=InlineKeyboardMarkup(kb))
    return P_GOAL

async def get_goal(update, context):
    query = update.callback_query; await query.answer()
    context.user_data['goal'] = query.data
    await query.edit_message_text("۳/۴ - مخاطب هدف شما کیست؟")
    return P_AUDIENCE

async def get_audience(update, context):
    context.user_data['audience'] = update.message.text
    kb = [[InlineKeyboardButton("رسمی", callback_data='tone_formal'), InlineKeyboardButton("صمیمی", callback_data='tone_friendly')], [InlineKeyboardButton("تخصصی", callback_data='tone_expert')]]
    await update.message.reply_text("۴/۴ - لحن برند؟", reply_markup=InlineKeyboardMarkup(kb))
    return P_TONE

async def get_tone_and_save(update, context):
    query = update.callback_query; await query.answer()
    u_id = str(update.effective_user.id)
    data = {'user_id': u_id, 'business': context.user_data['business'], 'goal': context.user_data['goal'], 'audience': context.user_data['audience'], 'tone': query.data}
    try:
        supabase.table('profiles').upsert(data, on_conflict='user_id').execute()
        await query.edit_message_text("✅ پروفایل با موفقیت ذخیره شد!")
    except: await query.edit_message_text("❌ خطا در ذخیره دیتابیس.")
    context.user_data.clear()
    return ConversationHandler.END

# --- ۵. استودیو طراحی لوگو (VIP - بدون متن) ---
async def start_logo_design(update, context):
    query = update.callback_query; await query.answer()
    if not await is_user_vip(update.effective_user.id) and not is_admin(update.effective_user.id):
        await query.message.reply_text("💎 طراحی لوگو مخصوص VIP است."); return ConversationHandler.END
    
    res = supabase.table('profiles').select("*").eq('user_id', str(update.effective_user.id)).execute()
    if not res.data: await query.message.reply_text("❌ ابتدا پروفایل بسازید."); return ConversationHandler.END
    context.user_data['profile'] = res.data[0]
    
    kb = [[InlineKeyboardButton("🔹 مینیمال", callback_data='ls_minimal')], [InlineKeyboardButton("🌿 ارگانیک", callback_data='ls_organic')], [InlineKeyboardButton("🛡️ امبلم", callback_data='ls_emblem')]]
    await query.message.reply_text("🎨 سبک لوگو را انتخاب کنید (بدون متن طراحی می‌شود):", reply_markup=InlineKeyboardMarkup(kb))
    return LOGO_STYLE_SELECT

async def generate_logo_final(update, context):
    query = update.callback_query; await query.answer()
    style_prompt = LOGO_STYLES_PROMPTS.get(query.data)
    prof = context.user_data['profile']
    
    wait = await query.message.reply_text("🎨 در حال طراحی لوگو...")
    try:
        gpt_p = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": f"Create a DALL-E 3 prompt for a logo icon for a '{prof['business']}' business. Style: {style_prompt}. NO TEXT, NO LETTERS."}])
        dalle_p = gpt_p.choices[0].message.content
        res = client.images.generate(model="dall-e-3", prompt=dalle_p, size="1024x1024", n=1)
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=res.data[0].url, caption=f"🎨 لوگوی اختصاصی شما آماده شد!")
        await wait.delete()
    except: await wait.edit_text("❌ خطا در تولید تصویر.")
    return ConversationHandler.END

# --- ادامه کد شامل سناریوساز و تولید کاور در پیام بعدی... ---
# --- ۶. هشتگ‌ساز هوشمند و ابزار تحلیل رقبا ---
async def hashtag_start(update, context):
    await (update.callback_query.message if update.callback_query else update.message).reply_text("🏷 موضوع پست خود را بفرستید (تایپ یا ویس) تا هشتگ‌های طبقه‌بندی شده بسازم:")
    return H_TOPIC

async def hashtag_generate(update, context):
    uid = str(update.effective_user.id)
    if not await check_daily_limit(update, uid): return ConversationHandler.END
    topic = await process_voice(update, context) if update.message.voice else update.message.text
    wait = await update.message.reply_text("⏳ در حال استخراج هشتگ‌ها...")
    try:
        res = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": f"برای موضوع {topic} ۲۰ هشتگ فارسی و انگلیسی مرتبط و ترند اینستاگرام تولید کن."}])
        await wait.edit_text(res.choices[0].message.content.replace('*',''))
        log_event(uid, 'hashtags_generated_success', topic)
    except: await wait.edit_text("❌ خطا")
    return ConversationHandler.END

async def analyze_start(update, context):
    await (update.callback_query.message if update.callback_query else update.message).reply_text("🕵️‍♂️ متن یک ریلز موفق را بفرستید تا فرمول وایرال شدنش را کالبدشکافی کنم:")
    return SPY_TEXT

async def analyze_competitor(update, context):
    uid = str(update.effective_user.id)
    text = await process_voice(update, context) if update.message.voice else update.message.text
    wait = await update.message.reply_text("🕵️‍♂️ در حال تحلیل مهندسی معکوس...")
    try:
        res = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": f"این متن ریلز را تحلیل کن و بگو چرا موفق بوده و یک ایده مشابه از آن بساز: {text}"}])
        await wait.edit_text(res.choices[0].message.content.replace('*',''))
        log_event(uid, 'competitor_analyzed', text[:50])
    except: await wait.edit_text("❌ خطا")
    return ConversationHandler.END

# --- ۷. تولید کاور DALL-E (ویژه VIP) ---
async def handle_dalle_trigger(update, context):
    query = update.callback_query; await query.answer()
    uid = str(update.effective_user.id)
    if not await is_user_vip(uid) and not is_admin(uid):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="💎 تولید کاور مخصوص VIP است."); return

    topic = context.user_data.get('dalle_topic', 'Instagram Reel')
    style = context.user_data.get('dalle_style', 'modern')
    wait = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"🎨 در حال طراحی کاور سبک {style}...")
    try:
        prompt = f"An eye-catching Instagram Reel cover for '{topic}'. Style: {style}. High quality, 9:16 aspect ratio. NO TEXT."
        res = client.images.generate(model="dall-e-3", prompt=prompt, size="1024x1792", quality="hd", n=1)
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=res.data[0].url, caption="🎨 کاور پیشنهادی شما آماده شد!")
        await wait.delete()
        log_event(uid, 'dalle_generated', topic)
    except: await wait.edit_text("❌ خطا در تولید تصویر.")

# --- ۸. سیستم زیرمجموعه‌گیری و VIP ---
async def show_referral_menu(update, context):
    u_id = update.effective_user.id
    bot_un = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_un}?start=ref_{u_id}"
    msg = f"🎁 **برنامه دعوت!**\n\nبا دعوت هر دوست، **{REFERRAL_REWARD} سهمیه رایگان** بگیرید.\n\n🔗 لینک شما:\n`{link}`"
    await (update.callback_query.message if update.callback_query else update.message).reply_text(msg, parse_mode='Markdown')

async def upgrade_vip_menu(update, context):
    msg = f"💎 **ارتقا به VIP نامحدود**\n\n💰 قیمت: {VIP_PRICE}\n💳 کارت: `{CARD_NUMBER}`\n👤 بنام: {CARD_NAME}\n\nبعد از واریز، عکس فیش را اینجا بفرستید."
    context.user_data['awaiting_receipt'] = True
    await (update.callback_query.message if update.callback_query else update.message).reply_text(msg, parse_mode='Markdown')

async def handle_photo(update, context):
    """مدیریت عکس‌های فیش واریزی (اولویت آخر)"""
    if context.user_data.get('awaiting_receipt'):
        user = update.effective_user
        kb = [[InlineKeyboardButton("✅ تایید", callback_data=f'v_p_{user.id}'), InlineKeyboardButton("❌ رد", callback_data=f'r_p_{user.id}')]]
        await context.bot.send_photo(chat_id=ADMIN_ID, photo=update.message.photo[-1].file_id, caption=f"💰 رسید از: {user.first_name}\n🆔 `{user.id}`", reply_markup=InlineKeyboardMarkup(kb))
        await update.message.reply_text("⏳ رسید ارسال شد. منتظر تایید ادمین باشید.")
        context.user_data['awaiting_receipt'] = False
    else: await update.message.reply_text("برای شروع، موضوع خود را تایپ یا ویس کنید.")

# --- ۹. هندلرهای ادمین و فیدبک ---
async def handle_feedback(update, context):
    await update.callback_query.answer("سپاس از نظر شما! ❤️")

async def handle_payment_action(update, context):
    query = update.callback_query; action, _, uid = query.data.split('_')
    if not is_admin(update.effective_user.id): return
    if action == 'v':
        supabase.table('profiles').update({'is_vip': True}).eq('user_id', uid).execute()
        await context.bot.send_message(chat_id=uid, text="🎉 حساب شما به VIP ارتقا یافت!")
        await query.edit_message_caption(caption="✅ تایید شد.")
    else:
        await context.bot.send_message(chat_id=uid, text="❌ رسید شما تایید نشد.")
        await query.edit_message_caption(caption="❌ رد شد.")

# --- ۱۰. چیدمان نهایی هندلرها در Main ---
# این بخش باید در انتهای فایل و داخل بلاک if __name__ == '__main__': باشد
def setup_handlers(app):
    app.add_handler(CommandHandler('start', show_main_menu))
    app.add_handler(CallbackQueryHandler(show_main_menu, pattern='^cancel$'))
    app.add_handler(CallbackQueryHandler(handle_dalle_trigger, pattern='^dalle_trigger_request$'))
    app.add_handler(CallbackQueryHandler(handle_feedback, pattern='^f_'))
    app.add_handler(CallbackQueryHandler(upgrade_vip_menu, pattern='^menu_upgrade_vip$'))
    app.add_handler(CallbackQueryHandler(show_referral_menu, pattern='^menu_referral$'))
    app.add_handler(CallbackQueryHandler(handle_payment_action, pattern='^[vr]_p_'))

    # ۱. هندلر طراحی لوگو (VIP)
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(start_logo_design, pattern='^menu_logo_design$')],
        states={LOGO_STYLE_SELECT: [CallbackQueryHandler(generate_logo_final, pattern='^ls_')]},
        fallbacks=[CommandHandler('cancel', show_main_menu)]
    ))

    # ۲. هندلر مربی ایده (با اولویت بالای عکس)
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('coach', coach_start), CallbackQueryHandler(coach_start, pattern='^menu_coach$')],
        states={C_TEXT: [MessageHandler(filters.PHOTO | filters.TEXT | filters.VOICE, coach_analyze)]},
        fallbacks=[CommandHandler('cancel', show_main_menu)]
    ))

    # ۳. هندلر پروفایل (با قابلیت ویرایش و نمایش)
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('profile', profile_start), CallbackQueryHandler(profile_start, pattern='^(menu_profile|re_edit_profile)$')],
        states={P_BUSINESS: [MessageHandler(filters.TEXT, get_business)], P_GOAL: [CallbackQueryHandler(get_goal)], P_AUDIENCE: [MessageHandler(filters.TEXT, get_audience)], P_TONE: [CallbackQueryHandler(get_tone_and_save)]},
        fallbacks=[CommandHandler('cancel', show_main_menu)]
    ))

    # ۴. هشتگ‌ساز و تحلیل رقبا
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(hashtag_start, pattern='^menu_hashtags$'), CallbackQueryHandler(analyze_start, pattern='^menu_analyze$')],
        states={H_TOPIC: [MessageHandler(filters.TEXT | filters.VOICE, hashtag_generate)], SPY_TEXT: [MessageHandler(filters.TEXT | filters.VOICE, analyze_competitor)]},
        fallbacks=[CommandHandler('cancel', show_main_menu)]
    ))

    # ۵. سناریوساز اصلی
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT | filters.VOICE, scenario_init), CallbackQueryHandler(scenario_init, pattern='^m_sc$')],
        states={C_CLAIM: [MessageHandler(filters.TEXT | filters.VOICE, get_claim)], C_EMOTION: [CallbackQueryHandler(gen_ideas)], EXPAND: [CallbackQueryHandler(expand_scenario)]},
        fallbacks=[CommandHandler('cancel', show_main_menu)]
    ))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# انتهای کد:
# setup_handlers(application)
# application.run_polling()
    
