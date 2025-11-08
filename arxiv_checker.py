import os
import argparse
import logging
from datetime import datetime, timedelta, time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CallbackQueryHandler, CommandHandler

from arxiv_core import (
    get_rated_papers,
    retrieve_papers_by_tag,
    save_feedback,
    MAX_RESULTS
)

# Telegram Bot Token and Chat ID
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN_NOTIF_BOT"]
TELEGRAM_CHAT_ID = int(os.environ["TELEGRAM_BOT_CHAT_ID"])

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)


async def fetch_and_send_papers(keywords, backdays, context: ContextTypes.DEFAULT_TYPE):
    """Fetch papers and send them via Telegram."""
    papers_to_send = get_rated_papers(keywords, backdays)
    
    if len(papers_to_send) == 0:
        await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="No new papers found.")
        return
    
    for overall_rating, message, entry_id in papers_to_send:
        # Provide 6 levels of rating for the paper.
        keys = ["👎", "2️⃣", "3️⃣", "4️⃣", "👍", "️❤️"]
        keyboard = [
            [
                InlineKeyboardButton(emoji, callback_data=f"rating{idx}_{entry_id}") 
                for idx, emoji in enumerate(keys, 1)
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await context.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID, 
                text=message, 
                parse_mode="Markdown", 
                reply_markup=reply_markup
            )
        except Exception as e:
            logging.error(f"Error sending message: {e}")


async def feedback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle feedback from Telegram users."""
    query = update.callback_query
    await query.answer()
    
    feedback_data = query.data
    feedback_type, entry_id = feedback_data.split('_', 1)
    
    # Save feedback using core module
    save_feedback(feedback_type, entry_id, str(update.effective_user.id))
    
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(f"Thank you for your feedback: {feedback_type}")


async def retrieve_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle paper retrieval requests from Telegram users."""
    if not update.callback_query:
        # Handle command case
        if not context.args:
            await update.message.reply_text("Please provide tags to search for. Usage: /get tag1 tag2 tag3")
            return
            
        tags = context.args
    else:
        # Handle callback query case
        query = update.callback_query
        await query.answer()
        data = query.data
        tags = data.split(' ')
    
    for tag in tags:
        # Retrieve papers using core module
        papers = retrieve_papers_by_tag(tag)
        
        # Return the papers
        if update.callback_query:
            await query.message.reply_text(
                f"For tag {tag}, the papers are the following: \n\n{'\n\n'.join(papers)}", 
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                f"For tag {tag}, the papers are the following: \n\n{'\n\n'.join(papers)}", 
                parse_mode="Markdown"
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--first_backcheck_day', type=int, default=None)
    parser.add_argument("--keywords", type=str, default="reasoning,planning,preference,optimization,symbolic,grokking")
    
    args = parser.parse_args()
    
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    application.add_handler(CallbackQueryHandler(feedback_handler))
    application.add_handler(CommandHandler("get", retrieve_handler))
    application.add_handler(CallbackQueryHandler(retrieve_handler, pattern="^get"))
    
    run_once_fetch_func = lambda context: fetch_and_send_papers(args.keywords, args.first_backcheck_day, context)
    run_daily_fetch_func = lambda context: fetch_and_send_papers(args.keywords, 2, context)
    
    if args.first_backcheck_day is not None:
        application.job_queue.run_once(run_once_fetch_func, when=timedelta(seconds=1))
    application.job_queue.run_daily(run_daily_fetch_func, time(hour=15))
    
    # Run the bot
    application.run_polling()


if __name__ == '__main__':
    main()
