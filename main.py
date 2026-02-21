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
ADMIN_ID = os.environ.get("ADMIN_ID")

DAILY_LIMIT = 5

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
async def check_services(update: Update) -> bool:
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
        query = supabase.table('logs').select("id", count="exact").in_('event_type', ['ideas_generated', 'hashtags_generated_success', 'coach_analyzed_success']).gte('created_at', f"{today}T00:00:00Z")
        if user_id: query = query.eq('user_id', user_id)
        response = query.execute()
        return response.count if response.count else 0
    except Exception as e:
        return 0

async def check_daily_limit(update: Update, user_id: str) -> bool:
    usage_count = await get_today_usage(user_id)
    if usage_count >= DAILY_LIMIT:
        message_target = update.callback_query.message if update.callback_query else update.message
        await message_target.reply_text(f"⚠️ **محدودیت استفاده روزانه**\n\nشما امروز به سقف مجاز خود ({DAILY_LIMIT} درخواست) رسیده‌اید. لطفاً فردا دوباره مراجعه کنید.", parse_mode='Markdown')
        return False
    return True

# --- تابع پردازش صدا ---
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

# --- 👑 پنل ادمین (Admin Panel) ---
A_BROADCAST = 10

def is_admin(user_id: int) -> bool: 
    return ADMIN_ID and str(user_id) == str(ADMIN_ID)

async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    keyboard = [
        [InlineKeyboardButton("📊 آمار کلی ربات", callback_data='admin_stats')],
        [InlineKeyboardButton("🕵️‍♂️ ۵ درخواست اخیر کاربران", callback_data='admin_monitor')],
        [InlineKeyboardButton("📢 ارسال پیام همگانی", callback_data='admin_broadcast_start')]
    ]
    await update.message.reply_text("👑 **پنل مدیریت ربات**\nلطفاً یک گزینه را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def handle_admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update.effective_user.id): return await query.answer("عدم دسترسی", show_alert=True)
    await query.answer()
    
    if query.data == 'admin_stats':
        try:
            total_users = supabase.table('profiles').select("id", count="exact").execute().count or 0
            total_usage_today = await get_today_usage()
            await query.message.reply_text(f"📊 **آمار زنده:**\n\n👥 کل کاربران ثبت‌نامی: {total_users} نفر\n🔥 کل درخواست‌های امروز: {total_usage_today} بار", parse_mode='Markdown')
        except Exception as e:
            await query.message.reply_text(f"❌ خطا: {e}")
            
    elif query.data == 'admin_monitor':
        try:
            # دریافت ۵ لاگ آخر مربوط به تولید محتوا
            response = supabase.table('logs').select("user_id, event_type, content, created_at")\
                .in_('event_type', ['ideas_generated', 'hashtags_generated_success', 'coach_analyzed_success'])\
                .order('created_at', desc=True).limit(5).execute()
            
            logs = response.data
            if not logs:
                await query.message.reply_text("📭 هنوز هیچ درخواستی ثبت نشده است.")
                return
                
            msg = "🕵️‍♂️ **۵ درخواست اخیر کاربران:**\n\n"
            for idx, log in enumerate(logs):
                # تبدیل اسم رویداد به زبان ساده
                event_name = "سناریونویس 🎬"
                if log['event_type'] == 'hashtags_generated_success': event_name = "هشتگ‌ساز 🏷"
                elif log['event_type'] == 'coach_analyzed_success': event_name = "مربی ایده 🧠"
                
                msg += f"**{idx+1}. ابزار:** {event_name}\n"
                msg += f"👤 **آیدی کاربر:** `{log['user_id']}`\n"
                msg += f"📝 **موضوع درخواست:** {log['content']}\n"
                msg += "──────────────\n"
                
            await query.message.reply_text(msg, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Admin monitor error: {e}")
            await query.message.reply_text("❌ خطا در دریافت اطلاعات مانیتورینگ.")

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    await query.answer()
    await query.message.reply_text("📢 **ارسال پیام همگانی:**\nلطفاً پیامی که می‌خواهید برای همه ارسال شود را تایپ کنید.\n(برای لغو /cancel را بزنید)")
    return A_BROADCAST

async def admin_broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    msg = update.message.text
    wait_msg = await update.message.reply_text("⏳ در حال استخراج کاربران و ارسال...")
    try:
        users = supabase.table('profiles').select("user_id").execute().data
        success, fail = 0, 0
        for u in users:
            try:
                await context.bot.send_message(chat_id=u['user_id'], text=msg)
                success += 1
                await asyncio.sleep(0.1) # برای جلوگیری از اسپم شدن ربات توسط تلگرام
            except: fail += 1
        await wait_msg.edit_text(f"✅ **پایان ارسال!**\n\n📬 موفق: {success} نفر\n🚫 ناموفق/بلاک کرده: {fail} نفر", parse_mode='Markdown')
        log_event(str(update.effective_user.id), 'admin_broadcast_sent', f"S: {success}, F: {fail}")
    except: await wait_msg.edit_text("❌ خطا در ارتباط با دیتابیس.")
    return ConversationHandler.END

# --- منوی اصلی ---
def get_main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("🎬 ایده‌پرداز و سناریو", callback_data='menu_scenario')],
        [InlineKeyboardButton("🏷 هشتگ‌ساز", callback_data='menu_hashtags'), InlineKeyboardButton("🧠 مربی ایده", callback_data='menu_coach')],
        [InlineKeyboardButton("👤 پروفایل", callback_data='menu_profile'), InlineKeyboardButton("💳 اعتبار", callback_data='menu_quota')]
    ]
    return InlineKeyboardMarkup(keyboard)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_event(str(update.effective_user.id), 'opened_main_menu')
    text = "سلام! از منوی زیر انتخاب کنید:\n*(می‌تونید ویس هم بفرستید!)*"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, reply_markup=get_main_menu_keyboard(), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=get_main_menu_keyboard(), parse_mode='Markdown')

async def handle_main_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'menu_scenario':
        await query.message.reply_text("🎬 فقط کافیست موضوع را تایپ یا **ویس** کنید.")
    elif query.data == 'menu_quota':
        usage = await get_today_usage(str(update.effective_user.id))
        await query.message.reply_text(f"💳 مصرف امروز: {usage}/{DAILY_LIMIT}")

# --- مکالمه پروفایل ---
P_BUSINESS, P_GOAL, P_AUDIENCE, P_TONE = range(4)
async def profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_services(update): return ConversationHandler.END
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
    context.user_data['goal'] = next(btn.text for r in query.message.reply_markup.inline_keyboard for btn in r if btn.callback_data == query.data)
    await query.edit_message_text(f"✅ هدف: {context.user_data['goal']}")
    await context.bot.send_message(chat_id=update.effective_chat.id, text="۳/۴ - مخاطب هدف؟")
    return P_AUDIENCE
async def get_audience(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['audience'] = update.message.text
    kb = [[InlineKeyboardButton("صمیمی", callback_data='tone_friendly'), InlineKeyboardButton("رسمی", callback_data='tone_formal')],
          [InlineKeyboardButton("انرژی‌بخش", callback_data='tone_energetic'), InlineKeyboardButton("طنز", callback_data='tone_humorous')],
          [InlineKeyboardButton("آموزشی", callback_data='tone_educational')]]
    await update.message.reply_text("۴/۴ - لحن برند؟", reply_markup=InlineKeyboardMarkup(kb))
    return P_TONE
async def get_tone_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
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

# --- هشتگ ساز ---
H_TOPIC = 5
async def hashtag_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_services(update): return ConversationHandler.END
    msg = "🏷 **هشتگ‌ساز!** موضوع را تایپ یا ویس کنید:"
    if update.callback_query: await update.callback_query.message.reply_text(msg, parse_mode='Markdown')
    else: await update.message.reply_text(msg, parse_mode='Markdown')
    return H_TOPIC
async def hashtag_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = str(update.effective_user.id)
    if not await check_daily_limit(update, uid): return ConversationHandler.END
    
    if update.message.voice:
        topic = await process_voice_to_text(update, context)
        if not topic: return ConversationHandler.END
        await update.message.reply_text(f"🗣 **شما:** {topic}", parse_mode='Markdown')
    else: topic = update.message.text
    
    try:
        prof = supabase.table('profiles').select("*").eq('user_id', uid).execute().data[0]
        wait_msg = await update.message.reply_text("⏳ در حال تولید هشتگ...")
        prompt = f"بر اساس کسب‌وکار ({prof['business']}) و موضوع ({topic}) سه دسته هشتگ فارسی بده: پربازدید، تخصصی، کامیونیتی."
        res = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}]).choices[0].message.content.replace('*', '')
        await wait_msg.edit_text(res)
        log_event(uid, 'hashtags_generated_success', topic)
    except: await update.message.reply_text("❌ خطا در تولید هشتگ یا یافتن پروفایل.")
    return ConversationHandler.END

# --- مربی ایده ---
C_TEXT = 6
async def coach_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_services(update): return ConversationHandler.END
    msg = "🧠 **مربی ایده!** ایده خود را بنویسید یا ویس بفرستید:"
    if update.callback_query: await update.callback_query.message.reply_text(msg, parse_mode='Markdown')
    else: await update.message.reply_text(msg, parse_mode='Markdown')
    return C_TEXT
async def coach_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = str(update.effective_user.id)
    if not await check_daily_limit(update, uid): return ConversationHandler.END
    
    if update.message.voice:
        idea = await process_voice_to_text(update, context)
        if not idea: return ConversationHandler.END
        await update.message.reply_text(f"🗣 **ایده شما:** {idea}", parse_mode='Markdown')
    else: idea = update.message.text

    try:
        prof = supabase.table('profiles').select("*").eq('user_id', uid).execute().data[0]
        wait_msg = await update.message.reply_text("🧐 در حال آنالیز...")
        prompt = f"به عنوان مربی، این ایده ({idea}) را برای کسب‌وکار ({prof['business']}) بررسی کن. نقاط قوت، ضعف، و نسخه اصلاح شده بده. بدون کاراکتر ستاره."
        res = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}]).choices[0].message.content.replace('*', '')
        await wait_msg.edit_text(res)
        log_event(uid, 'coach_analyzed_success', idea)
    except: await update.message.reply_text("❌ خطا در آنالیز یا یافتن پروفایل.")
    return ConversationHandler.END

# --- سناریو ساز ---
IDEAS, EXPAND = range(7, 9)
async def check_profile_before_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = str(update.effective_user.id)
    if not await check_services(update) or not await check_daily_limit(update, uid): return ConversationHandler.END
    try:
        context.user_data['profile'] = supabase.table('profiles').select("*").eq('user_id', uid).execute().data[0]
        if update.message.voice:
            topic = await process_voice_to_text(update, context)
            if not topic: return ConversationHandler.END
            await update.message.reply_text(f"🗣 **موضوع:** {topic}", parse_mode='Markdown')
            context.user_data['topic'] = topic
        else: context.user_data['topic'] = update.message.text
        return await generate_ideas(update, context)
    except:
        await update.message.reply_text("❌ پروفایل یافت نشد.")
        return ConversationHandler.END
        
async def generate_ideas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    prof, topic = context.user_data['profile'], context.user_data['topic']
    wait_msg = await update.message.reply_text("⏳ در حال ایده‌پردازی...")
    try:
        prompt = f"برای موضوع ({topic}) و کسب‌وکار ({prof['business']})، سه ایده ریلز با ساختار JSON (کلید ideas، لیست شامل title و hook) بده."
        res = client.chat.completions.create(model="gpt-4o", response_format={"type": "json_object"}, messages=[{"role": "user", "content": prompt}])
        ideas = json.loads(res.choices[0].message.content).get("ideas", [])
        context.user_data['ideas'] = ideas
        kb = [[InlineKeyboardButton(f"🎬 ساخت ایده {i+1}", callback_data=f'expand_{i}')] for i in range(len(ideas))]
        msg = f"موضوع: {topic}\n\n" + "\n".join([f"{i+1}. {x['title']}\nقلاب: {x['hook']}\n" for i, x in enumerate(ideas)])
        await wait_msg.edit_text(msg, reply_markup=InlineKeyboardMarkup(kb))
        log_event(str(update.effective_user.id), 'ideas_generated', topic)
        return EXPAND
    except:
        await wait_msg.edit_text("❌ خطا در ایده‌پردازی.")
        return ConversationHandler.END

async def expand_idea(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idea = context.user_data['ideas'][int(query.data.split('_')[1])]
    prof = context.user_data['profile']
    await query.edit_message_text(f"✅ انتخاب: {idea['title']}\n⏳ در حال نوشتن سناریو...")
    try:
        prompt = f"بر اساس ایده ({idea['title']}, {idea['hook']}) و پروفایل ({prof['business']}) سناریو کامل فارسی بده. اگر کاملا نامربوط بود بگو نامرتبط. ستاره نذار."
        res = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": prompt}]).choices[0].message.content.replace('*', '')
        await context.bot.send_message(chat_id=update.effective_chat.id, text=res)
        log_event(str(update.effective_user.id), 'expansion_success', idea['title'])
    except: await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ خطا در سناریو.")
    context.user_data.clear()
    return ConversationHandler.END

# --- اجرای ربات ---
if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler(['start', 'menu'], show_main_menu))
    application.add_handler(CommandHandler('admin', admin_start))
    application.add_handler(CallbackQueryHandler(handle_main_menu_buttons, pattern='^(menu_scenario|menu_quota)$'))
    
    # اضافه شدن هندلر دکمه‌های ادمین
    application.add_handler(CallbackQueryHandler(handle_admin_buttons, pattern='^(admin_stats|admin_monitor)$'))
    
    application.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern='^admin_broadcast_start$')],
        states={A_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_send)]},
        fallbacks=[CommandHandler('cancel', cancel_action)]
    ))
    
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
        states={EXPAND: [CallbackQueryHandler(expand_idea, pattern='^expand_')]},
        fallbacks=[CommandHandler('cancel', cancel_action), CallbackQueryHandler(cancel_action, pattern='^cancel$')]
    ))
    
    print("🤖 BOT DEPLOYED: LIVE MONITORING ADMIN PANEL!")
    application.run_polling()
