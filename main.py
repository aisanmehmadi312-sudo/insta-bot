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

# ... (بخش‌های اولیه کد بدون تغییر باقی می‌مانند) ...
# --- اتصال به سرویس‌ها ---
# ... (بدون تغییر) ...

# ---------------------------------------------
# --- تابع جدید برای ثبت آمار ---
def log_event(user_id, event_type, content=""):
    """یک رخداد را در جدول logs در Supabase ثبت می‌کند."""
    if not supabase:
        return
    try:
        supabase.table('logs').insert({
            'user_id': str(user_id),
            'event_type': event_type,
            'content': content
        }).execute()
    except Exception as e:
        logger.error(f"Supabase log error: {e}")

# ---------------------------------------------

# --- مراحل مکالمه برای ساخت پروفایل ---
async def profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_event(update.effective_user.id, 'profile_start') # ثبت آمار
    await update.message.reply_text("خب، بیا پروفایل کسب‌وکارت رو بسازیم.\n\n**موضوع اصلی پیج شما چیست؟**", parse_mode='Markdown')
    return BUSINESS

# ... (بقیه توابع پروفایل بدون تغییر) ...

async def get_tone_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['tone'] = update.message.text
    user_id = str(update.effective_user.id)
    
    profile_data = {'user_id': user_id, 'business': context.user_data['business'], 'audience': context.user_data['audience'], 'tone': context.user_data['tone']}
    
    try:
        supabase.table('profiles').upsert(profile_data, on_conflict='user_id').execute()
        log_event(user_id, 'profile_saved') # ثبت آمار
        await update.message.reply_text("✅ پروفایل شما با موفقیت ذخیره/آپدیت شد!")
    except Exception as e:
        logger.error(f"Supabase upsert Error: {e}")
        await update.message.reply_text(f"❌ خطا در ذخیره پروفایل: {e}")
    return ConversationHandler.END

# ... (تابع cancel_profile بدون تغییر) ...

# ---------------------------------------------

# --- دستورات اصلی ربات ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_event(update.effective_user.id, 'start_command') # ثبت آمار
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
        prompt = f"""
        **Your Primary Task:**
        You are a viral content strategist. Your job is to create a professional Instagram Reel blueprint for the user's topic, based on their profile.

        **User's Profile:**
        - **Business:** {user_profile['business']}
        - **Audience:** {user_profile['audience']}
        - **Tone:** {user_profile['tone']}
        - **Today's Topic:** "{user_text}"

        ---
        **CRITICAL RULES:**
        1.  **Relevance First:** Use common sense. If and ONLY IF the topic is completely irrelevant to the business (e.g., business is "fruit stand", topic is "car engines"), then you MUST abandon the blueprint and reply ONLY with this exact Persian sentence:
            `موضوع «{user_text}» با پروفایل کسب‌وکار شما ارتباطی ندارد. لطفاً یک موضوع مرتبط ارائه دهید.`
        2.  **Markdown Quality Control:** You MUST be extremely careful with your Markdown syntax. Every `*` or `_` used for formatting must be correctly opened and closed. Double-check your response to ensure it's syntactically perfect before outputting. This is a strict technical requirement.

        ---
        **Blueprint Structure (if relevant):**
        ### 🎬 Viral Reel Blueprint: [Engaging Title]
        **1. ATTENTION (0-3s): Hook**
        *   **Visual:** [Describe the first shot]
        *   **On-Screen Text:** [A powerful sentence]
        **2. INTEREST (4-10s): Problem/Value**
        *   **Visual:** [Describe the shots]
        *   **Narration:** [Explain the core idea]
        **3. DESIRE (11-20s): Solution**
        *   **Visual:** [Show the "aha!" moment]
        *   **Narration:** [Explain the benefit]
        **4. ACTION (21-30s): CTA**
        *   **Visual:** [Final satisfying shot]
        *   **On-Screen Text:** [e.g., "Save for later!"]
        ---
        ### ✍️ Caption & Hashtags
        **Caption:** [Write an engaging caption]
        **Hashtags:** [Provide 5-7 hashtags]
        """
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}]
        )
        ai_reply = response.choices[0].message.content.strip()
        
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
        
        is_rejection = ai_reply.startswith(f"موضوع «{user_text}»")
        
        message_to_send = ""
        if is_rejection:
            log_event(user_id, 'topic_rejected', user_text) # ثبت آمار
            message_to_send = f"**توجه:**\n{ai_reply}"
        else:
            log_event(user_id, 'content_generated', user_text) # ثبت آمار
            message_to_send = ai_reply

        try:
            await update.message.reply_text(message_to_send, parse_mode='Markdown')
        except BadRequest as e:
            if "Can't parse entities" in str(e):
                log_event(user_id, 'markdown_error', user_text) # ثبت آمار
                logger.warning(f"Markdown parse error. Sending as plain text. Error: {e}")
                fallback_text = "⚠️ هوش مصنوعی یک پاسخ با فرمت نوشتاری اشتباه تولید کرد. متن خام پاسخ:\n\n" + ai_reply
                await update.message.reply_text(fallback_text)
            else:
                raise e

    except Exception as e:
        log_event(user_id, 'general_error', str(e)) # ثبت آمار
        logger.error(f"Error in generate_content: {e}")
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=wait_msg.message_id)
        except Exception as delete_error:
            logger.error(f"Could not delete wait message: {delete_error}")
        
        await update.message.reply_text(f"❌ ببخشید، در پردازش درخواست شما مشکلی پیش آمد.\n\nجزئیات فنی: {e}")


if __name__ == '__main__':
    # ... (کد اصلی اجرای ربات بدون تغییر)
    
