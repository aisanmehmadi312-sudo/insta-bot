import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from openai import OpenAI
from supabase import create_client, Client
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler,
    filters, ConversationHandler
)

# Setup logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Environment Variables ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# --- Keep-Alive Server ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_fake_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler).serve_forever()

threading.Thread(target=run_fake_server, daemon=True).start()

# ---------------------------------------------

# --- Service Connections ---
client = None
if OPENAI_API_KEY:
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        logger.error(f"OpenAI Config Error: {e}")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        logger.error(f"Supabase Config Error: {e}")

# ---------------------------------------------
# --- Analytics Logging Function ---
def log_event(user_id: str, event_type: str, content: str = ""):
    if not supabase:
        return
    try:
        data_to_insert = {
            'user_id': str(user_id),
            'event_type': event_type,
            'content': content
        }
        supabase.table('logs').insert(data_to_insert).execute()
    except Exception as e:
        logger.error(f"Supabase log event error: {e}")

# ---------------------------------------------

# --- Profile Conversation ---
BUSINESS, AUDIENCE, TONE = range(3)

async def profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_event(update.effective_user.id, 'profile_start')
    await update.message.reply_text("خب، بیا پروفایل کسب‌وکارت رو بسازیم.\n\n**موضوع اصلی پیج شما چیست؟**", parse_mode='Markdown')
    return BUSINESS

async def get_business(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['business'] = update.message.text
    await update.message.reply_text("عالی! حالا بگو **مخاطب هدفت چه کسانی هستند؟**", parse_mode='Markdown')
    return AUDIENCE

async def get_audience(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['audience'] = update.message.text
    await update.message.reply_text("و در آخر، **لحن برندت چیست؟** (صمیمی، رسمی، شوخ)", parse_mode='Markdown')
    return TONE

async def get_tone_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['tone'] = update.message.text
    user_id = str(update.effective_user.id)
    
    profile_data = {'user_id': user_id, 'business': context.user_data['business'], 'audience': context.user_data['audience'], 'tone': context.user_data['tone']}
    
    try:
        supabase.table('profiles').upsert(profile_data, on_conflict='user_id').execute()
        log_event(user_id, 'profile_saved')
        await update.message.reply_text("✅ پروفایل شما با موفقیت ذخیره/آپدیت شد!")
    except Exception as e:
        logger.error(f"Supabase upsert Error: {e}")
        await update.message.reply_text(f"❌ خطا در ذخیره پروفایل: {e}")
    return ConversationHandler.END

async def cancel_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_event(update.effective_user.id, 'profile_cancel')
    await update.message.reply_text("عملیات ساخت پروفایل لغو شد.")
    return ConversationHandler.END

# ---------------------------------------------

# --- Main Bot Logic ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_event(update.effective_user.id, 'start_command')
    await update.message.reply_text("سلام! 👋\nبرای ساخت/ویرایش پروفایل، دستور /profile رو بزن.\nبعد از اون، هر موضوعی بفرستی، بر اساس پروفایلت برات سناریو ریلز می‌سازم.")

async def generate_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    try:
        response = supabase.table('profiles').select("*").eq('user_id', user_id).execute()
        if not response.data:
            await update.message.reply_text("❌ اول باید پروفایلت رو بسازی! لطفاً دستور /profile رو بزن.")
            return
        user_profile = response.data[0]
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در خواندن پروفایل از دیتابیس: {e}")
        return

    user_text = update.message.text
    wait_msg = await update.message.reply_text("⏳ در حال بررسی موضوع و طراحی سناریو...")

    try:
        # --- Final Prompt with a Self-Correction Layer ---
        prompt = f"""
        **Your Primary Task:**
        As a viral content strategist, create a professional Instagram Reel blueprint for the user's topic, based on their profile.

        **User's Profile:**
        - **Business:** {user_profile['business']}
        - **Audience:** {user_profile['audience']}
        - **Tone:** {user_profile['tone']}
        - **Today's Topic:** "{user_text}"

        ---
        **Execution Plan:**

        **Step 1: Relevance Check.**
        Use common sense. If the topic is completely irrelevant (e.g., business is "fruit stand", topic is "car engines"), you MUST stop and reply ONLY with this exact Persian sentence:
        `موضوع «{user_text}» با پروفایل کسب‌وکار شما ارتباطی ندارد. لطفاً یک موضوع مرتبط ارائه دهید.`

        **Step 2: Blueprint Creation (if relevant).**
        Create the script using the AIDA model. Structure it like this:
        ### 🎬 Viral Reel Blueprint: [Title]
        **1. ATTENTION (0-3s): Hook** (*Visual:* ..., *On-Screen Text:* ...)
        **2. INTEREST (4-10s): Problem/Value** (*Visual:* ..., *Narration:* ...)
        **3. DESIRE (11-20s): Solution** (*Visual:* ..., *Narration:* ...)
        **4. ACTION (21-30s): CTA** (*Visual:* ..., *On-Screen Text:* ...)
        ---
        ### ✍️ Caption & Hashtags
        **Caption:** ...
        **Hashtags:** ...

        **Step 3: Final Quality Check (CRITICAL).**
        Before you output your final response, you MUST review it for valid Markdown syntax. Ensure every asterisk `*` and every underscore `_` is part of a correctly matched pair. Your final output MUST be 100% syntactically perfect Markdown. This is a non-negotiable technical requirement.
        """
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}]
        )
        ai_reply = response.choices[0].message.content.strip()
        
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
        
        is_rejection = ai_reply.startswith(f"موضوع «{user_text}»")
        
        message_to_send = f"**توجه:**\n{ai_reply}" if is_rejection else ai_reply

        try:
            await update.message.reply_text(message_to_send, parse_mode='Markdown')
            # Log success only if the message is sent without errors
            if not is_rejection:
                log_event(user_id, 'content_generated_success', user_text)
        except BadRequest as e:
            if "Can't parse entities" in str(e):
                log_event(user_id, 'markdown_error', user_text)
                logger.error(f"Markdown parse error despite quality check. Error: {e}")
                fallback_text = "⚠️ هوش مصنوعی یک پاسخ با فرمت نوشتاری اشتباه تولید کرد. این یک باگ است و به زودی رفع می‌شود. متن خام پاسخ:\n\n" + ai_reply
                await update.message.reply_text(fallback_text)
            else:
                # For other bad requests, re-raise the error
                raise e
        
        if is_rejection:
            log_event(user_id, 'topic_rejected', user_text)

    except Exception as e:
        log_event(user_id, 'general_error', str(e))
        logger.error(f"Error in generate_content: {e}")
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
        except Exception as delete_error:
            logger.error(f"Could not delete wait message: {delete_error}")
        
        await update.message.reply_text(f"❌ ببخشید، در پردازش درخواست شما مشکلی پیش آمد: {e}")


if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('profile', profile_start)],
        states={
            BUSINESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_business)],
            AUDIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_audience)],
            TONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_tone_and_save)],
        },
        fallbacks=[CommandHandler('cancel', cancel_profile)],
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), generate_content))
    
    print("🤖 BOT DEPLOYED WITH FINAL PROMPT & QUALITY CONTROL!")
    application.run_polling()
        
