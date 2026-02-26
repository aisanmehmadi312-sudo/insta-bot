import os, logging, threading, json, asyncio, base64, math
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from openai import OpenAI
from supabase import create_client, Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
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

DAILY_LIMIT, REFERRAL_REWARD = 5, 3 #
CARD_NUMBER, CARD_NAME = "6118-2800-5587-6343", "امیراحمد شاه حسینی"
VIP_PRICE = "۱۹۹,۰۰۰ تومان" #
LOGO_STYLES_PROMPTS = {
    'ls_minimal': 'Minimalist, clean geometric lines, modern, sleek icon. Flat design.',
    'ls_organic': 'Hand-drawn, watercolor texture, organic shapes, friendly and natural feel.',
    'ls_emblem': 'Emblem style badge, bold lines, vintage seal concept, strong and assertive.'
}

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

# وضعیت‌های گفتگو
(P_BUSINESS, P_GOAL, P_AUDIENCE, P_TONE, 
 C_TEXT, C_CLAIM, C_EMOTION, EXPAND, 
 H_TOPIC, SPY_TEXT, 
 LOG_MODE, LOGO_STYLE_SELECT, LOGO_CUSTOM_PROMPT) = range(13)

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
        usage = supabase.table('logs').select("id", count="exact").in_('event_type', ['ideas_generated', 'hashtags_generated_success', 'coach_analyzed_success', 'dalle_generated', 'vip_tts_generated']).gte('created_at', f"{today}T00:00:00Z").eq('user_id', str(u_id)).execute().count or 0
        allowance = await get_user_allowance(u_id)
        if usage >= allowance:
            kb = [[InlineKeyboardButton("🎁 دریافت سهمیه", callback_data='menu_referral')], [InlineKeyboardButton("💎 ارتقا به VIP", callback_data='menu_upgrade_vip')]]
            msg = f"⚠️ سهمیه امروز تمام شد ({usage}/{allowance})."
            target = update.message if update.message else update.callback_query.message
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

# --- بخش پروفایل ---
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
        kb = [[InlineKeyboardButton("📝 ویرایش پروفایل", callback_data='re_edit_profile')], [InlineKeyboardButton("🔙 بازگشت به منو", callback_data='cancel')]]
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
        await show_main_menu(update, context)
    except: await query.edit_message_text("❌ خطا در ذخیره.")
    return ConversationHandler.END

# --- بخش لوگو VIP (نسخه جدید با قابلیت شخصی‌سازی) ---
async def start_logo_design(update, context):
    u_id = str(update.effective_user.id)
    if not await is_user_vip(u_id) and not is_admin(u_id):
        target = update.message if update.message else update.callback_query.message
        await target.reply_text("💎 طراحی لوگو مخصوص کاربران VIP است."); return ConversationHandler.END
    
    kb = [
        [InlineKeyboardButton("🤖 بر اساس پروفایل من", callback_data='logo_mode_auto')],
        [InlineKeyboardButton("✍️ وارد کردن موضوع دلخواه", callback_data='logo_mode_custom')]
    ]
    target = update.message if update.message else update.callback_query.message
    await target.reply_text("🎨 چطور لوگو را طراحی کنم؟", reply_markup=InlineKeyboardMarkup(kb))
    return LOG_MODE

async def logo_mode_handle(update, context):
    query = update.callback_query; await query.answer()
    if query.data == 'logo_mode_custom':
        await query.edit_message_text("📝 لطفاً موضوع یا پرامپت لوگوی خود را بفرستید (انگلیسی یا فارسی):")
        return LOGO_CUSTOM_PROMPT
    else:
        u_id = str(update.effective_user.id)
        prof = supabase.table('profiles').select("*").eq('user_id', u_id).execute()
        if not prof.data:
            await query.edit_message_text("❌ ابتدا پروفایل بسازید."); return ConversationHandler.END
        context.user_data['logo_topic'] = f"Business: {prof.data[0]['business']}, Audience: {prof.data[0]['audience']}"
        kb = [[InlineKeyboardButton("🔹 مینیمال", callback_data='ls_minimal')], [InlineKeyboardButton("🌿 ارگانیک", callback_data='ls_organic')], [InlineKeyboardButton("🛡️ امبلم", callback_data='ls_emblem')]]
        await query.edit_message_text("🎨 سبک لوگو را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(kb))
        return LOGO_STYLE_SELECT

async def get_custom_logo_topic(update, context):
    context.user_data['logo_topic'] = update.message.text
    kb = [[InlineKeyboardButton("🔹 مینیمال", callback_data='ls_minimal')], [InlineKeyboardButton("🌿 ارگانیک", callback_data='ls_organic')], [InlineKeyboardButton("🛡️ امبلم", callback_data='ls_emblem')]]
    await update.message.reply_text("🎨 حالا سبک لوگو را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(kb))
    return LOGO_STYLE_SELECT

async def generate_logo_final(update, context):
    query = update.callback_query; await query.answer()
    style_key = query.data
    topic = context.user_data.get('logo_topic', 'Modern Business')
    style_prompt = LOGO_STYLES_PROMPTS.get(style_key, LOGO_STYLES_PROMPTS['ls_minimal'])
    wait = await query.message.reply_text("🎨 در حال طراحی لوگو...")
    try:
        dalle_prompt = f"Professional logo ICON ONLY. NO TEXT. Subject: {topic}. Style: {style_prompt}. Vector art, solid background."
        res = client.images.generate(model="dall-e-3", prompt=dalle_prompt, size="1024x1024", n=1)
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=res.data[0].url, caption=f"🎨 لوگوی درخواستی آماده شد!")
        await wait.delete()
        log_event(str(update.effective_user.id), 'vip_logo_generated', topic[:50])
    except: await wait.edit_text("❌ خطا در تولید لوگو.")
    return ConversationHandler.END

# --- مربی و آنالیزور ---
async def coach_start(update, context):
    await update.message.reply_text("🧠 متن ایده را بفرستید یا (ویژه VIP 💎) عکس کاور را جهت تحلیل بفرستید.")
    return C_TEXT
async def coach_analyze(update, context):
    u_id = str(update.effective_user.id)
    if update.message.photo:
        if not await is_user_vip(u_id) and not is_admin(u_id):
            await update.message.reply_text("🔒 مخصوص VIP است."); return ConversationHandler.END
        wait = await update.message.reply_text("👁 تحلیل گرافیک...")
        try:
            file_path = f"c_{u_id}.jpg"
            file = await context.bot.get_file(update.message.photo[-1].file_id); await file.download_to_drive(file_path)
            base64_img = encode_image(file_path)
            res = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": [{"type": "text", "text": "نقد گرافیک کاور اینستاگرام (بدون ستاره)"}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}}]}])
            os.remove(file_path); await wait.edit_text(res.choices[0].message.content.replace('*', ''))
            log_event(u_id, 'coach_vision_success')
        except: await wait.edit_text("❌ خطا.")
        return ConversationHandler.END
    if not await check_daily_limit(update, u_id): return ConversationHandler.END
    content = await process_voice(update, context) if update.message.voice else update.message.text
    wait = await update.message.reply_text("🧐 در حال کالبدشکافی...")
    try:
        res = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": f"نقد ایده ریلز: {content}"}])
        await wait.edit_text(res.choices[0].message.content.replace('*', ''))
        log_event(u_id, 'coach_analyzed_success', content[:50])
    except: await wait.edit_text("❌ خطا.")
    return ConversationHandler.END

# --- سناریوساز اصلی ---
async def scenario_init(update, context):
    u_id = str(update.effective_user.id)
    if not await check_daily_limit(update, u_id): return ConversationHandler.END
    prof = supabase.table('profiles').select("*").eq('user_id', u_id).execute()
    if not prof.data:
        await update.message.reply_text("❌ ابتدا پروفایل بسازید."); return ConversationHandler.END
    context.user_data['profile'] = prof.data[0]
    await update.message.reply_text("🎯 موضوع یا ادعای جنجالی خود را بفرستید:")
    return C_CLAIM
async def get_claim(update, context):
    context.user_data['claim'] = await process_voice(update, context) if update.message.voice else update.message.text
    kb = [[InlineKeyboardButton("هشدار دهنده ⚠️", callback_data='emo_warn')], [InlineKeyboardButton("تخصصی 🧠", callback_data='emo_expert')]]
    await update.message.reply_text("🎭 حس ویدیو؟", reply_markup=InlineKeyboardMarkup(kb))
    return C_EMOTION
async def gen_ideas(update, context):
    query = update.callback_query; await query.answer(); context.user_data['emotion'] = query.data
    wait = await query.message.reply_text("🔮 طراحی استراتژی...")
    try:
        p, c = context.user_data['profile'], context.user_data['claim']
        prompt = f"3 Reels ideas for {p['business']} based on '{c}'. Return JSON: {{'ideas': [{{'type': '...', 'title': '...', 'hook': '...'}}]}}"
        res = client.chat.completions.create(model="gpt-4o", response_format={"type": "json_object"}, messages=[{"role": "user", "content": prompt}])
        ideas = json.loads(res.choices[0].message.content)['ideas']; context.user_data['ideas'] = ideas
        kb = [[InlineKeyboardButton(f"🎬 {id['type'].upper()}", callback_data=f'expand_{i}')] for i, id in enumerate(ideas)]
        await wait.edit_text("💎 یک زاویه‌دید انتخاب کنید:", reply_markup=InlineKeyboardMarkup(kb)); return EXPAND
    except: await wait.edit_text("❌ خطا."); return ConversationHandler.END
async def expand_scenario(update, context):
    query = update.callback_query; await query.answer()
    idea = context.user_data['ideas'][int(query.data.split('_')[1])]
    context.user_data['dalle_topic'] = idea['title']
    prof, claim = context.user_data['profile'], context.user_data['claim']
    wait = await query.message.reply_text(f"📝 نگارش سناریو...")
    try:
        prompt = f"Write a 20s Reels script. Topic: {idea['title']}, Claim: {claim}. No 'hello', no 'like/comment'. Focus on hook. Persian language."
        script = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}]).choices[0].message.content.replace('*', '')
        context.user_data['last_script'] = script
        kb = [[InlineKeyboardButton("🎨 تولید کاور (VIP)", callback_data='dalle_trigger')], [InlineKeyboardButton("🎙 دریافت ویس (VIP)", callback_data='tts_generate')], [InlineKeyboardButton("🔙 بازگشت", callback_data='cancel')]]
        await wait.edit_text(script, reply_markup=InlineKeyboardMarkup(kb))
        log_event(str(update.effective_user.id), 'ideas_generated')
    except: await wait.edit_text("❌ خطا."); return ConversationHandler.END

# --- تولید ویس (TTS) ---
async def generate_tts(update, context):
    query = update.callback_query; await query.answer(); uid = str(update.effective_user.id)
    if not await is_user_vip(uid) and not is_admin(uid):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="💎 مخصوص VIP است."); return
    script = context.user_data.get('last_script')
    if not script: return
    wait = await context.bot.send_message(chat_id=update.effective_chat.id, text="🎙 در حال ضبط صدا...")
    try:
        res = client.audio.speech.create(model="tts-1", voice="onyx", input=script[:4000])
        path = f"tts_{uid}.ogg"; res.write_to_file(path)
        with open(path, 'rb') as f: await context.bot.send_voice(chat_id=update.effective_chat.id, voice=f)
        os.remove(path); await wait.delete(); log_event(uid, 'vip_tts_generated')
    except: await wait.edit_text("❌ خطا.")

async def handle_dalle_trigger(update, context):
    query = update.callback_query; await query.answer(); uid = str(update.effective_user.id)
    if not await is_user_vip(uid) and not is_admin(uid): return
    topic = context.user_data.get('dalle_topic', 'Reel')
    wait = await context.bot.send_message(chat_id=update.effective_chat.id, text="🎨 طراحی کاور...")
    try:
        res = client.images.generate(model="dall-e-3", prompt=f"Instagram cover for {topic}, high quality, no text", size="1024x1792", n=1)
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=res.data[0].url); await wait.delete()
        log_event(uid, 'dalle_generated')
    except: await wait.edit_text("❌ خطا.")

# --- هشتگ و آنالیز رقیب ---
async def hashtag_start(update, context):
    await update.message.reply_text("🏷 موضوع پست؟"); return H_TOPIC
async def hashtag_generate(update, context):
    uid = str(update.effective_user.id); topic = update.message.text
    if not await check_daily_limit(update, uid): return ConversationHandler.END
    wait = await update.message.reply_text("⏳ استخراج..."); res = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": f"20 Hashtags for {topic}"}])
    await wait.edit_text(res.choices[0].message.content.replace('*', '')); log_event(uid, 'hashtags_success'); return ConversationHandler.END

async def analyze_start(update, context):
    await update.message.reply_text("🕵️‍♂️ متن ریلز موفق را بفرستید:"); return SPY_TEXT
async def analyze_competitor(update, context):
    uid = str(update.effective_user.id); text = update.message.text
    wait = await update.message.reply_text("🕵️‍♂️ تحلیل..."); res = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": f"Analyze this viral reel script: {text}"}])
    await wait.edit_text(res.choices[0].message.content.replace('*', '')); log_event(uid, 'spy_success'); return ConversationHandler.END

# --- سیستم VIP و مالی ---
async def show_referral(update, context):
    un = (await context.bot.get_me()).username; link = f"https://t.me/{un}?start=ref_{update.effective_user.id}"
    await update.message.reply_text(f"🎁 لینک دعوت شما:\n`{link}`\nهر دعوت = {REFERRAL_REWARD} سهمیه.", parse_mode='Markdown')
async def upgrade_vip(update, context):
    context.user_data['awaiting_receipt'] = True
    msg = f"💎 **اشتراک VIP یک ماهه**\n💳 کارت: `{CARD_NUMBER}`\n👤 بنام: {CARD_NAME}\n💰 مبلغ: {VIP_PRICE}\n📌 عکس فیش را بفرستید."
    await update.message.reply_text(msg, parse_mode='Markdown')
async def handle_receipt(update, context):
    if context.user_data.get('awaiting_receipt'):
        kb = [[InlineKeyboardButton("✅ تایید", callback_data=f'v_p_{update.effective_user.id}')], [InlineKeyboardButton("❌ رد", callback_data=f'r_p_{update.effective_user.id}')]]
        await context.bot.send_photo(chat_id=ADMIN_ID, photo=update.message.photo[-1].file_id, caption=f"فیش از `{update.effective_user.id}`", reply_markup=InlineKeyboardMarkup(kb))
        await update.message.reply_text("⏳ در حال بررسی..."); context.user_data['awaiting_receipt'] = False
async def admin_pay_handle(update, context):
    query = update.callback_query; await query.answer()
    if not is_admin(update.effective_user.id): return
    action, _, target = query.data.split('_')
    if action == 'v':
        supabase.table('profiles').update({'is_vip': True}).eq('user_id', target).execute()
        await context.bot.send_message(chat_id=target, text="🎉 حساب شما VIP شد!")
    await query.edit_message_caption(caption="اعمال شد.")

# --- منو و استارت ---
def main_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🎬 سناریوساز استراتژیک"), KeyboardButton("🧠 مربی ایده و آنالیزور")],
        [KeyboardButton("🎨 طراحی لوگو (VIP)")],
        [KeyboardButton("🏷 هشتگ‌ساز"), KeyboardButton("🕵️‍♂️ تحلیل رقبا")],
        [KeyboardButton("👤 پروفایل"), KeyboardButton("🎁 هدیه")],
        [KeyboardButton("💎 ارتقا VIP")]
    ], resize_keyboard=True)

async def start(update, context):
    if context.args and context.args[0].startswith('ref_'):
        ref = context.args[0].split('_')[1]; uid = str(update.effective_user.id)
        if ref != uid: 
            try: supabase.table('profiles').upsert({'user_id': uid, 'referred_by': ref}, on_conflict='user_id').execute()
            except: pass
    await update.message.reply_text("🚀 خوش آمدید!", reply_markup=main_kb())

# --- اجرای نهایی ---
if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.Regex('^💎 ارتقا VIP$'), upgrade_vip))
    app.add_handler(MessageHandler(filters.Regex('^🎁 هدیه$'), show_referral))
    app.add_handler(CallbackQueryHandler(admin_pay_handle, pattern='^[vr]_p_'))
    app.add_handler(CallbackQueryHandler(handle_dalle_trigger, pattern='^dalle_trigger$'))
    app.add_handler(CallbackQueryHandler(generate_tts, pattern='^tts_generate$'))
    app.add_handler(CallbackQueryHandler(start, pattern='^cancel$'))

    # طراحی لوگو Conversation
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🎨 طراحی لوگو \(VIP\)$'), start_logo_design)],
        states={
            LOG_MODE: [CallbackQueryHandler(logo_mode_handle)],
            LOGO_CUSTOM_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_custom_logo_topic)],
            LOGO_STYLE_SELECT: [CallbackQueryHandler(generate_logo_final, pattern='^ls_')]
        },
        fallbacks=[CommandHandler('cancel', start)]
    ))

    # پروفایل Conversation
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^👤 پروفایل$'), profile_start), CallbackQueryHandler(profile_start, pattern='^re_edit_profile$')],
        states={P_BUSINESS: [MessageHandler(filters.TEXT, get_business)], P_GOAL: [CallbackQueryHandler(get_goal)], P_AUDIENCE: [MessageHandler(filters.TEXT, get_audience)], P_TONE: [CallbackQueryHandler(get_tone_and_save)]},
        fallbacks=[CommandHandler('cancel', start)]
    ))

    # سناریو Conversation
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🎬 سناریوساز استراتژیک$'), scenario_init)],
        states={C_CLAIM: [MessageHandler(filters.TEXT | filters.VOICE, get_claim)], C_EMOTION: [CallbackQueryHandler(gen_ideas)], EXPAND: [CallbackQueryHandler(expand_scenario, pattern='^expand_')]},
        fallbacks=[CommandHandler('cancel', start)]
    ))

    # مربی Conversation
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🧠 مربی ایده و آنالیزور$'), coach_start)],
        states={C_TEXT: [MessageHandler(filters.PHOTO | filters.TEXT | filters.VOICE, coach_analyze)]},
        fallbacks=[CommandHandler('cancel', start)]
    ))

    # هشتگ و آنالیز
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🏷 هشتگ‌ساز$'), hashtag_start), MessageHandler(filters.Regex('^🕵️‍♂️ تحلیل رقبا$'), analyze_start)],
        states={H_TOPIC: [MessageHandler(filters.TEXT, hashtag_generate)], SPY_TEXT: [MessageHandler(filters.TEXT, analyze_competitor)]},
        fallbacks=[CommandHandler('cancel', start)]
    ))

    app.add_handler(MessageHandler(filters.PHOTO, handle_receipt))
    app.run_polling()
