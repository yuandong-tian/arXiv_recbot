import os
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
import joblib
import random
import argparse

import torch
import sqlite3

from dotenv import load_dotenv
load_dotenv()

# Define your keywords
MAX_RESULTS = 100

# Telegram Bot Token and Chat ID (replace with your actual values)
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN_NOTIF_BOT"]  # Set your Telegram bot token as an environment variable
TELEGRAM_CHAT_ID = int(os.environ["TELEGRAM_BOT_CHAT_ID"]) # Replace with your chat ID

import logging
from datetime import datetime, timedelta, time, UTC
from arxiv_util import *
from preference_model import PreferenceModel
from common import *

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CallbackQueryHandler, CommandHandler

if os.path.exists(global_model_name):
    vectorizer = joblib.load(global_vectorizer_name)
    loaded_model = PreferenceModel(vectorizer.get_feature_names_out().shape[0], 6)
    loaded_model.load_state_dict(torch.load(global_model_name))
    loaded_model.eval()
    print(f"Loaded {global_model_name} and {global_vectorizer_name}")
else:
    loaded_model = None

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

application = None  # Will hold the Telegram application instance

# Open the database
conn = sqlite3.connect(global_dataset_name)
cursor = conn.cursor()

async def fetch_and_send_papers(keywords, backdays, context: ContextTypes.DEFAULT_TYPE):
    results = get_arxiv_results(keywords.replace(",", " OR "), MAX_RESULTS)

    now = datetime.now(UTC)
    yesterday = now - timedelta(days=backdays)

    num_sent = 0

    papers_to_send = []

    for result in results:
        submitted_date = result.updated
        # Make the submitted date timezone-aware by assuming UTC
        submitted_date = submitted_date.replace(tzinfo=UTC)
        if submitted_date >= yesterday:
            message = get_arxiv_message(result)

            if loaded_model:
                # Predict the class of the paper
                X = vectorizer.transform([message])
                X_tensor = torch.tensor(X.toarray(), dtype=torch.float32)
                prediction = loaded_model(X_tensor)
                # y_pred = prediction.argmax(dim=1).item()
                # Prepend predicted probabilities of all classes to output text
                y_pred_proba = prediction.softmax(dim=1).detach().cpu()
                y_pred_proba = y_pred_proba[0]

                # Compute an overall rating for the paper. 
                # The rating is a weighted sum of the predicted probabilities of all classes.
                # The weights are [0, 1, 2, ..], i.e. the rating is the sum of the predicted probabilities.
                overall_rating = torch.dot(y_pred_proba, torch.arange(y_pred_proba.shape[0]).float()).item()

                message = f"// {overall_rating} {y_pred_proba}\n{message}"
            else:
                # No model to load yet
                overall_rating = 0
                message = f"// no model yet\n{message}" 

            papers_to_send.append((overall_rating, message, result.entry_id))

    if len(papers_to_send) == 0:
        await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="No new papers found.")
        return

    # Sort papers_to_send by overall_rating in descending order
    papers_to_send.sort(key=lambda x: x[0], reverse=True)
    # Select the top 10 papers
    papers_to_send = papers_to_send[:10]

    for overall_rating, message, entry_id in papers_to_send:
        # Provide 5 level of rating for the paper.
        # Provide emoji for each level of rating.
        keys = ["👎", "2️⃣", "3️⃣", "4️⃣", "👍", "️❤️"]
        keyboard = [
            [
                InlineKeyboardButton(emoji, callback_data=f"rating{idx}_{entry_id}") for idx, emoji in enumerate(keys, 1)
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode="Markdown", reply_markup=reply_markup)
        except Exception as e:
            print(e)

async def feedback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    feedback_data = query.data
    feedback_type, entry_id = feedback_data.split('_', 1)

    # Collect feedback (here we just log it)
    logging.info(f"Received feedback: {feedback_type} for paper {entry_id} from user {update.effective_user.id}")

    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(f"Thank you for your feedback: {feedback_type}")

async def retrieve_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        # Retrieve the paper from the database that contains the tags
        cursor.execute('SELECT paper_message_id FROM comments WHERE comment LIKE ?', ('%' + tag + '%',))
        paper_message_ids = cursor.fetchall()

        # Get all papers that contain the tags and return
        papers = []

        for paper_message_id in paper_message_ids:
            # Retrieve the paper from the database
            cursor.execute('SELECT text FROM infos WHERE paper_message_id = ?', paper_message_id)
            for paper in cursor.fetchall():
                # Convert the paper to a string
                papers.append(str(paper[0]))

        # Return the papers
        if update.callback_query:
            await query.message.reply_text(f"For tag {tag}, the papers are the following: \n\n{'\n\n'.join(papers)}", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"For tag {tag}, the papers are the following: \n\n{'\n\n'.join(papers)}", parse_mode="Markdown")

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
