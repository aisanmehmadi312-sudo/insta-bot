import os, logging, threading, json, asyncio, base64, math
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from openai import OpenAI
from supabase import create_client, Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler,
    filters, ConversationHandler, CallbackQueryHandler)

# --- تنظیمات و اتصال به سرویس‌ها ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ADMIN_ID = os.environ.get("ADMIN_ID")

DAILY_LIMIT, REFERRAL_REWARD = 5, 3
CARD_NUMBER, CARD_NAME = "6118-2800-5587-6343", "امیراحمد شاه حسینی"
VIP_PRICE = "۹۹,۰۰۰ تومان"
LOGO_STYLES_PROMPTS = {
    'ls_minimal': 'Minimalist, clean geometric lines, modern, sleek icon. Flat design.',
    'ls_organic': 'Hand-drawn, watercolor texture, organic shapes, friendly and natural feel.',
    'ls_emblem': 'Emblem style badge, bold lines, vintage seal concept, strong and assertive.'
}

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

(P_BUSINESS, P_GOAL, P_AUDIENCE, P_TONE, 
 C_TEXT, C_CLAIM, C_EMOTION, EXPAND, 
 H_TOPIC, SPY_TEXT, LOGO_STYLE_SELECT) = range(11)

# --- سرور فیک برای زنده نگه داشتن Render ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running...")
def run_fake_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler).serve_forever()
threading.Thread(target=run_fake_server, daemon=True).start()

# --- توابع کمکی ---
def is_admin(u_id): return ADMIN_ID and str(u_id) == str(ADMIN_ID)

async def is_user_vip(u_id):
    if not supabase: return False
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
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        usage = supabase.table('logs').select("id", count="exact").in_('event_type', ['ideas_generated', 'hashtags_generated_success', 'coach_analyzed_success', 'dalle_generated']).gte('created_at', f"{today}T00:00:00Z").eq('user_id', str(u_id)).execute().count or 0
        allowance = await get_user_allowance(u_id)
        if usage >= allowance:
            kb = [[InlineKeyboardButton("🎁 دریافت سهمیه", callback_data='menu_referral')], [InlineKeyboardButton("💎 ارتقا به VIP", callback_data='menu_upgrade_vip')]]
            msg = f"⚠️ سهمیه امروز تمام شد ({usage}/{allowance})."
            target = update.callback_query.message if update.callback_query else update.message
            await target.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb))
            return False
        return True
    except: return True

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

def encode_image(image_path):
    with open(image_path, "rb") as image_file: return base64.b64encode(image_file.read()).decode('utf-8')

# --- بخش پروفایل (با حل مشکل دکمه ویرایش) ---
async def profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    u_id = str(update.effective_user.id)
    query = update.callback_query
    
    if query and query.data == 're_edit_profile':
        await query.answer()
        await query.edit_message_text("بسیار خب، اطلاعات جدید را وارد کنید.\n\n۱/۴ - موضوع اصلی پیج شما چیست؟")
        return P_BUSINESS
        
    if query: await query.answer()
    
    res = supabase.table('profiles').select("*").eq('user_id', u_id).execute()
    if res.data:
        p = res.data[0]
        msg = f"👤 **پروفایل فعلی شما:**\n\n🏢 موضوع: {p['business']}\n🎯 هدف: {p['goal']}\n👥 مخاطب: {p['audience']}\n🗣 لحن: {p['tone']}\n\nآیا قصد ویرایش دارید؟"
        kb = [[InlineKeyboardButton("📝 ویرایش پروفایل", callback_data='re_edit_profile')], [InlineKeyboardButton("🔙 بازگشت", callback_data='cancel')]]
        target = query.message if query else update.message
        await target.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        return ConversationHandler.END

    msg = "👋 هنوز پروفایلی نساخته‌اید.\n\n۱/۴ - موضوع اصلی پیج شما چیست؟"
    target = query.message if query else update.message
    await target.reply_text(msg)
    return P_BUSINESS

async def get_business(update, context):
    context.user_data['business'] = update.message.text
    kb = [[InlineKeyboardButton("فروش 💰", callback_data='goal_sales'), InlineKeyboardButton("برندسازی 📣", callback_data='goal_branding')], [InlineKeyboardButton("آموزش 🎓", callback_data='goal_edu'), InlineKeyboardButton("سرگرمی 🎭", callback_data='goal_fun')]]
    await update.message.reply_text("۲/۴ - هدف اصلی؟", reply_markup=InlineKeyboardMarkup(kb))
    return P_GOAL

async def get_goal(update, context):
    query = update.callback_query; await query.answer()
    context.user_data['goal'] = query.data
    await query.edit_message_text("۳/۴ - مخاطب هدف شما کیست؟")
    return P_AUDIENCE

async def get_audience(update, context):
    context.user_data['audience'] = update.message.text
    kb = [[InlineKeyboardButton("رسمی", callback_data='tone_formal'), InlineKeyboardButton("صمیمی", callback_data='tone_friendly')], [InlineKeyboardButton("طنز", callback_data='tone_funny'), InlineKeyboardButton("تخصصی", callback_data='tone_expert')]]
    await update.message.reply_text("۴/۴ - لحن برند؟", reply_markup=InlineKeyboardMarkup(kb))
    return P_TONE

async def get_tone_and_save(update, context):
    query = update.callback_query; await query.answer()
    u_id = str(update.effective_user.id)
    data = {'user_id': u_id, 'business': context.user_data['business'], 'goal': context.user_data['goal'], 'audience': context.user_data['audience'], 'tone': query.data}
    try:
        supabase.table('profiles').upsert(data, on_conflict='user_id').execute()
        await query.edit_message_text("✅ پروفایل با موفقیت ذخیره/بروزرسانی شد! 🚀")
    except Exception as e:
        logger.error(f"Save Error: {e}")
        await query.edit_message_text("❌ خطا در ذخیره.")
    context.user_data.clear()
    return ConversationHandler.END

# --- بخش لوگو VIP ---
async def start_logo_design(update, context):
    query = update.callback_query; await query.answer()
    u_id = str(update.effective_user.id)
    if not await is_user_vip(u_id) and not is_admin(u_id):
        await query.message.reply_text("💎 طراحی لوگو مخصوص کاربران VIP است."); return ConversationHandler.END
    
    prof = supabase.table('profiles').select("*").eq('user_id', u_id).execute()
    if not prof.data: await query.message.reply_text("❌ ابتدا پروفایل بسازید."); return ConversationHandler.END
    context.user_data['profile'] = prof.data[0]
    
    kb = [[InlineKeyboardButton("🔹 مینیمال", callback_data='ls_minimal')], [InlineKeyboardButton("🌿 ارگانیک", callback_data='ls_organic')], [InlineKeyboardButton("🛡️ امبلم", callback_data='ls_emblem')]]
    await query.message.reply_text("🎨 یکی از ۳ سبک زیر را انتخاب کنید (بدون متن طراحی می‌شود):", reply_markup=InlineKeyboardMarkup(kb))
    return LOGO_STYLE_SELECT

async def generate_logo_final(update, context):
    query = update.callback_query; await query.answer()
    style_key = query.data
    prof = context.user_data.get('profile')
    style_prompt = LOGO_STYLES_PROMPTS.get(style_key, LOGO_STYLES_PROMPTS['ls_minimal'])
    
    wait = await query.message.reply_text("🎨 در حال طراحی لوگو...")
    try:
        gpt_prompt = f"Create a DALL-E 3 prompt for a professional logo ICON ONLY. NO TEXT. Topic: {prof['business']}. Audience: {prof['audience']}. Style: {style_prompt}."
        dalle_prompt = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": gpt_prompt}]).choices[0].message.content
        res = client.images.generate(model="dall-e-3", prompt=dalle_prompt, size="1024x1024", n=1)
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=res.data[0].url, caption=f"🎨 لوگوی پیشنهادی آماده شد!")
        await wait.delete()
        log_event(str(update.effective_user.id), 'vip_logo_generated', style_key)
    except Exception as e:
        logger.error(f"Logo Error: {e}")
        await wait.edit_text("❌ خطا در تولید لوگو.")
    return ConversationHandler.END

# --- مربی ایده و Vision AI ---
async def coach_start(update, context):
    msg = "🧠 **مربی هوشمند**\nمتن ایده را بفرستید یا (ویژه VIP 💎) عکس کاور را جهت تحلیل گرافیکی ارسال کنید."
    target = update.callback_query.message if update.callback_query else update.message
    await target.reply_text(msg, parse_mode='Markdown')
    return C_TEXT

async def coach_analyze(update, context):
    u_id = str(update.effective_user.id)
    if update.message.photo:
        if not await is_user_vip(u_id) and not is_admin(u_id):
            await update.message.reply_text("🔒 تحلیل بصری مخصوص VIP است."); return ConversationHandler.END
        wait = await update.message.reply_text("👁 در حال تحلیل گرافیک...")
        try:
            file_path = f"c_{u_id}.jpg"
            file = await context.bot.get_file(update.message.photo[-1].file_id)
            await file.download_to_drive(file_path)
            base64_img = encode_image(file_path)
            res = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": [{"type": "text", "text": "این کاور را از نظر گرافیک اینستاگرام نقد کن (فارسی و بدون ستاره)"}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}}]}])
            if os.path.exists(file_path): os.remove(file_path)
            await wait.edit_text(res.choices[0].message.content.replace('*', ''))
            log_event(u_id, 'coach_vision_success')
        except: await wait.edit_text("❌ خطا در پردازش تصویر.")
        return ConversationHandler.END

    if not await check_daily_limit(update, u_id): return ConversationHandler.END
    content = await process_voice(update, context) if update.message.voice else update.message.text
    wait = await update.message.reply_text("🧐 در حال کالبدشکافی...")
    try:
        res = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": f"به عنوان مربی محتوا این ایده ریلز را نقد کن: {content}"}])
        await wait.edit_text(res.choices[0].message.content.replace('*', ''))
        log_event(u_id, 'coach_analyzed_success', content[:50])
    except: await wait.edit_text("❌ خطا")
    return ConversationHandler.END

# --- سناریوساز اصلی ---
async def scenario_init(update, context):
    u_id = str(update.effective_user.id)
    if not await check_daily_limit(update, u_id): return ConversationHandler.END
    prof = supabase.table('profiles').select("*").eq('user_id', u_id).execute()
    if not prof.data:
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text("❌ ابتدا پروفایل بسازید."); return ConversationHandler.END
    context.user_data['profile'] = prof.data[0]
    context.user_data['topic'] = await process_voice(update, context) if update.message and update.message.voice else (update.message.text if update.message else "موضوع عمومی")
    
    target = update.callback_query.message if update.callback_query else update.message
    await target.reply_text("🎯 ادعای اصلی یا مهم‌ترین نکته شما چیست؟")
    return C_CLAIM

async def get_claim(update, context):
    context.user_data['claim'] = await process_voice(update, context) if update.message.voice else update.message.text
    kb = [[InlineKeyboardButton("امیدوارکننده ✨", callback_data='emo_hope'), InlineKeyboardButton("هشدار ⚠️", callback_data='emo_warn')], [InlineKeyboardButton("طنز 😂", callback_data='emo_fun'), InlineKeyboardButton("تخصصی 🧠", callback_data='emo_expert')]]
    await update.message.reply_text("🎭 حس ویدیو؟", reply_markup=InlineKeyboardMarkup(kb))
    return C_EMOTION

async def gen_ideas(update, context):
    query = update.callback_query; await query.answer()
    context.user_data['emotion'] = query.data
    wait = await query.message.reply_text("🔮 در حال طراحی استراتژی...")
    try:
        t, c, p = context.user_data['topic'], context.user_data['claim'], context.user_data['profile']
        prompt = f"برای موضوع '{t}' با ادعای '{c}' بیزنس '{p['business']}'، ۳ ایده ریلز متفاوت (آموزشی, POV, ویروسی) بساز. خروجی فقط JSON: {{'ideas': [{{'type': '...', 'title': '...', 'hook': '...'}}]}}"
        res = client.chat.completions.create(model="gpt-4o", response_format={"type": "json_object"}, messages=[{"role": "user", "content": prompt}])
        ideas = json.loads(res.choices[0].message.content)['ideas']
        context.user_data['ideas'] = ideas
        kb = [[InlineKeyboardButton(f"🎬 {id['type'].upper()}", callback_data=f'expand_{i}')] for i, id in enumerate(ideas)]
        await wait.edit_text("💎 یکی از سبک‌ها را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(kb))
        return EXPAND
    except: await wait.edit_text("❌ خطا در تولید ایده."); return ConversationHandler.END

async def expand_scenario(update, context):
    query = update.callback_query; await query.answer()
    idea = context.user_data['ideas'][int(query.data.split('_')[1])]
    context.user_data['dalle_topic'], context.user_data['dalle_style'] = idea['title'], idea['type']
    wait = await query.message.reply_text(f"📝 نوشتن سناریو برای سبک {idea['type']}...")
    try:
        script = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": f"سناریو کامل برای موضوع {idea['title']} سبک {idea['type']} با قلاب {idea['hook']}"}]).choices[0].message.content.replace('*', '')
        dur = math.ceil(len(script.split()) / 2.5)
        kb = [[InlineKeyboardButton("🎨 تولید کاور هوشمند (VIP)", callback_data='dalle_trigger_request')], [InlineKeyboardButton("🔙 منو", callback_data='cancel')]]
        await wait.edit_text(f"{script}\n\n⏱ زمان: {dur} ثانیه", reply_markup=InlineKeyboardMarkup(kb))
        log_event(str(update.effective_user.id), 'ideas_generated', idea['type'])
    except: await wait.edit_text("❌ خطا در سناریو.")
    return ConversationHandler.END

async def handle_dalle_trigger(update, context):
    query = update.callback_query; await query.answer()
    uid = str(update.effective_user.id)
    if not await is_user_vip(uid) and not is_admin(uid):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="💎 مخصوص VIP است."); return
    topic, style = context.user_data.get('dalle_topic', 'Reel'), context.user_data.get('dalle_style', 'modern')
    wait = await context.bot.send_message(chat_id=update.effective_chat.id, text="🎨 طراحی کاور...")
    try:
        prompt = f"Instagram Reel cover for '{topic}'. Style: {style}. High quality, 9:16. NO TEXT."
        res = client.images.generate(model="dall-e-3", prompt=prompt, size="1024x1792", quality="hd", n=1)
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=res.data[0].url, caption="🎨 کاور شما!")
        await wait.delete()
        log_event(uid, 'dalle_generated', topic)
    except: await wait.edit_text("❌ خطا.")

# --- هشتگ و آنالیز ---
async def hashtag_start(update, context):
    await (update.callback_query.message if update.callback_query else update.message).reply_text("🏷 موضوع پست؟")
    return H_TOPIC
async def hashtag_generate(update, context):
    uid = str(update.effective_user.id)
    if not await check_daily_limit(update, uid): return ConversationHandler.END
    topic = await process_voice(update, context) if update.message.voice else update.message.text
    wait = await update.message.reply_text("⏳ استخراج...")
    try:
        res = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": f"۲۰ هشتگ برای {topic}"}])
        await wait.edit_text(res.choices[0].message.content.replace('*', ''))
        log_event(uid, 'hashtags_generated_success', topic)
    except: await wait.edit_text("❌ خطا")
    return ConversationHandler.END

async def analyze_start(update, context):
    await (update.callback_query.message if update.callback_query else update.message).reply_text("🕵️‍♂️ متن ریلز موفق را بفرستید:")
    return SPY_TEXT
async def analyze_competitor(update, context):
    uid = str(update.effective_user.id)
    text = await process_voice(update, context) if update.message.voice else update.message.text
    wait = await update.message.reply_text("🕵️‍♂️ تحلیل...")
    try:
        res = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": f"تحلیل این ریلز: {text}"}])
        await wait.edit_text(res.choices[0].message.content.replace('*', ''))
        log_event(uid, 'competitor_analyzed', text[:50])
    except: await wait.edit_text("❌ خطا")
    return ConversationHandler.END

# --- زیرمجموعه‌گیری و VIP ---
async def show_referral_menu(update, context):
    bot_un = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_un}?start=ref_{update.effective_user.id}"
    await (update.callback_query.message if update.callback_query else update.message).reply_text(f"🎁 لینک دعوت شما:\n`{link}`", parse_mode='Markdown')

async def upgrade_vip_menu(update, context):
    query = update.callback_query; await query.answer()
    context.user_data['awaiting_receipt'] = True
    await query.message.reply_text(f"💎 ارتقا VIP\n💳 کارت: `{CARD_NUMBER}`\n👤 بنام: {CARD_NAME}\nبعد از واریز عکس فیش بفرستید.", parse_mode='Markdown')

async def handle_receipt(update, context):
    if context.user_data.get('awaiting_receipt'):
        user = update.effective_user
        kb = [[InlineKeyboardButton("✅ تایید", callback_data=f'v_p_{user.id}')], [InlineKeyboardButton("❌ رد", callback_data=f'r_p_{user.id}')]]
        await context.bot.send_photo(chat_id=ADMIN_ID, photo=update.message.photo[-1].file_id, caption=f"💰 فیش از `{user.id}`", reply_markup=InlineKeyboardMarkup(kb))
        await update.message.reply_text("⏳ ارسال شد.")
        context.user_data['awaiting_receipt'] = False
    else: await update.message.reply_text("ابتدا از منو انتخاب کنید.")

async def handle_admin_payment(update, context):
    query = update.callback_query; await query.answer()
    if not is_admin(update.effective_user.id): return
    action, _, target_uid = query.data.split('_')
    if action == 'v':
        supabase.table('profiles').update({'is_vip': True}).eq('user_id', target_uid).execute()
        await context.bot.send_message(chat_id=target_uid, text="🎉 VIP شدید!")
        await query.edit_message_caption(caption="✅ تایید شد.")
    else:
        await context.bot.send_message(chat_id=target_uid, text="❌ رد شد.")
        await query.edit_message_caption(caption="❌ رد شد.")

# --- منوی اصلی ---
def get_main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 سناریوساز استراتژیک", callback_data='m_sc')],
        [InlineKeyboardButton("🧠 مربی ایده و آنالیزور", callback_data='menu_coach')],
        [InlineKeyboardButton("🎨 طراحی لوگو (VIP)", callback_data='menu_logo_design')],
        [InlineKeyboardButton("🏷 هشتگ‌ساز", callback_data='menu_hashtags'), InlineKeyboardButton("🕵️‍♂️ تحلیل رقبا", callback_data='menu_analyze')],
        [InlineKeyboardButton("👤 پروفایل", callback_data='menu_profile'), InlineKeyboardButton("🎁 هدیه", callback_data='menu_referral')],
        [InlineKeyboardButton("💎 ارتقا VIP", callback_data='menu_upgrade_vip')]
    ])

async def show_main_menu(update, context):
    msg = "🚀 منوی اصلی:"
    if update.message and update.message.text.startswith('/start ref_'):
        referrer = update.message.text.split('ref_')[1]
        u_id = str(update.effective_user.id)
        if referrer != u_id: supabase.table('profiles').upsert({'user_id': u_id, 'referred_by': referrer}, on_conflict='user_id').execute()
    target = update.message if update.message else update.callback_query.message
    await target.reply_text(msg, reply_markup=get_main_menu_keyboard())

# --- اجرای ربات ---
if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler('start', show_main_menu))
    app.add_handler(CallbackQueryHandler(show_main_menu, pattern='^cancel$'))
    app.add_handler(CallbackQueryHandler(upgrade_vip_menu, pattern='^menu_upgrade_vip$'))
    app.add_handler(CallbackQueryHandler(show_referral_menu, pattern='^menu_referral$'))
    app.add_handler(CallbackQueryHandler(handle_admin_payment, pattern='^[vr]_p_'))
    app.add_handler(CallbackQueryHandler(handle_dalle_trigger, pattern='^dalle_trigger_request$'))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(start_logo_design, pattern='^menu_logo_design$')],
        states={LOGO_STYLE_SELECT: [CallbackQueryHandler(generate_logo_final, pattern='^ls_')]},
        fallbacks=[CommandHandler('cancel', show_main_menu)]
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('profile', profile_start), CallbackQueryHandler(profile_start, pattern='^(menu_profile|re_edit_profile)$')],
        states={P_BUSINESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_business)], P_GOAL: [CallbackQueryHandler(get_goal)], P_AUDIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_audience)], P_TONE: [CallbackQueryHandler(get_tone_and_save)]},
        fallbacks=[CommandHandler('cancel', show_main_menu)]
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('coach', coach_start), CallbackQueryHandler(coach_start, pattern='^menu_coach$')],
        states={C_TEXT: [MessageHandler(filters.PHOTO | filters.TEXT | filters.VOICE, coach_analyze)]},
        fallbacks=[CommandHandler('cancel', show_main_menu)]
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(hashtag_start, pattern='^menu_hashtags$'), CallbackQueryHandler(analyze_start, pattern='^menu_analyze$')],
        states={H_TOPIC: [MessageHandler(filters.TEXT | filters.VOICE, hashtag_generate)], SPY_TEXT: [MessageHandler(filters.TEXT | filters.VOICE, analyze_competitor)]},
        fallbacks=[CommandHandler('cancel', show_main_menu)]
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(scenario_init, pattern='^m_sc$'), MessageHandler(filters.TEXT & ~filters.COMMAND, scenario_init)],
        states={C_CLAIM: [MessageHandler(filters.TEXT | filters.VOICE, get_claim)], C_EMOTION: [CallbackQueryHandler(gen_ideas, pattern='^emo_')], EXPAND: [CallbackQueryHandler(expand_scenario, pattern='^expand_')]},
        fallbacks=[CommandHandler('cancel', show_main_menu)]
    ))

    app.add_handler(MessageHandler(filters.PHOTO, handle_receipt))

    print("🚀 Bot is running!")
    app.run_polling()
