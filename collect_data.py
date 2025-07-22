import re
import sqlite3
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
import asyncio
import os

from common import *
# Logging with timestamps
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from arxiv_util import *

from dotenv import load_dotenv
load_dotenv()

# Replace these with your own values
api_id = os.getenv('TG_API_ID')
api_hash = os.getenv('TG_API_HASH')

# You can set a session name or use None for an ephemeral session
session_name = "arxiv_retriever"

# The target chat from which to retrieve messages
target_chat = int(os.environ["TELEGRAM_BOT_CHAT_ID"])

# Number of messages to retrieve
limit = 100000  

async def main():
    # Initialize the client
    client = TelegramClient(session_name, api_id, api_hash)

    await client.start()
    logger.info("Client Created")

    # If you have 2FA enabled, you'll need to enter your password
    if not await client.is_user_authorized():
        phone = input("Enter your phone number (with country code): ")
        await client.send_code_request(phone)
        code = input("Enter the code you received: ")
        try:
            await client.sign_in(phone, code)
        except SessionPasswordNeededError:
            password = input("Two-Step Verification enabled. Please enter your password: ")
            await client.sign_in(password=password)

    # Get the target entity
    try:
        entity = await client.get_entity(target_chat)
    except ValueError:
        logger.error(f"Could not find the chat: {target_chat}")
        return

    # Use regular expression to extract the label
    matcher = re.compile(r"Thank you for your feedback: (\w+)")
    label2class = { f"rating{i+1}" : i for i in range(6) }
    label2class.update({
        "not": 0,
        "thumb": 4,
        "love": 5,
    })

    # Load the message from the chat history and save it to a sqlite database
    conn = sqlite3.connect(global_dataset_name)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS infos (id INTEGER PRIMARY KEY, paper_message_id INTEGER, text TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS comments (id INTEGER PRIMARY KEY, message_id INTEGER, paper_message_id INTEGER, comment TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS preferences (id INTEGER PRIMARY KEY, message_id INTEGER, paper_message_id INTEGER, preference INTEGER)')

    logger.info("Loading the database papers and preferences ids")

    # Get all the processed paper ids
    cursor.execute('SELECT paper_message_id FROM infos')
    processed_paper_ids = set([row[0] for row in cursor.fetchall()])

    # Get all the processed message ids from the comments and preferences
    cursor.execute('SELECT message_id FROM comments')
    processed_message_ids = set([row[0] for row in cursor.fetchall()])
    cursor.execute('SELECT message_id FROM preferences')
    processed_message_ids.update([row[0] for row in cursor.fetchall()])

    logger.info("Iterating over the chat history to save most recent messages")

    # Iterate over the chat history from the most recent message of the chat
    async for message in client.iter_messages(entity, limit=limit, reverse=False):
        # Check whether the message has been processed, if so, break the loop since we iterate over the messages from most recent one
        if message.id in processed_message_ids:
            break

        # collect the paper information
        if message.reply_to_msg_id:
            # Get parent message
            paper_msg_id = message.reply_to_msg_id
            # get the content of the parent message
            paper_msg = await client.get_messages(target_chat, ids=paper_msg_id)
            if paper_msg.sender.is_self:
                # If the messsage is about the bot replying to the user request, skip
                # Only bot/user replies to bot are processed
                continue
            
            if paper_msg_id not in processed_paper_ids:
                if paper_msg.text.startswith("//"):
                    paper_msg.text = "\n".join(paper_msg.text.split("\n")[1:])
                cursor.execute('INSERT INTO infos (paper_message_id, text) VALUES (?, ?)', (paper_msg_id, paper_msg.text))

            # Extract label 
            # Example text: Thank you for your feedback: not
            # Use regular expression to extract the label
            m = matcher.search(message.text)
            if m:
                label = label2class.get(m.group(1), None)
                if label is not None:
                    # For parent_msg.txt, if the first line starts with "//", remove it
                    cursor.execute('INSERT INTO preferences (message_id, paper_message_id, preference) VALUES (?, ?, ?)', (message.id, paper_msg_id, label))
            else:
                # Comments
                cursor.execute('INSERT INTO comments (message_id, paper_message_id, comment) VALUES (?, ?, ?)', (message.id, paper_msg_id, message.text))
        
        elif message.sender.is_self and message.text is not None and message.text.startswith("https://arxiv.org"):
            # Put the paper in with default preference 
            # If there is any comments, we put the comments in the comments table
            # If there is any preferences, we put the preferences in the preferences table
            arxiv_link, comments = message.text.split(" ", 1)
            results = get_arxiv_results(arxiv_link, 1)
            cursor.execute('INSERT INTO infos (paper_message_id, text) VALUES (?, ?)', (message.id, get_arxiv_message(results[0]).replace("**", "")))
            cursor.execute('INSERT INTO preferences (message_id, paper_message_id, preference) VALUES (?, ?, ?)', (message.id, message.id, 4))

            comments = comments.strip()
            if comments != "":
                cursor.execute('INSERT INTO comments (message_id, paper_message_id, comment) VALUES (?, ?, ?)', (message.id, message.id, comments))

    # Then insert everything into the database
    conn.commit()

    logger.info("Disconnecting from the client")
    await client.disconnect()

if __name__ == '__main__':
    # Ensure the event loop is properly handled
    asyncio.run(main())
