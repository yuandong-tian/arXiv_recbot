import os
import logging
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk.errors import SlackApiError

from arxiv_core import (
    get_rated_papers,
    retrieve_papers_by_tag,
    save_feedback
)

# Tokens from your Slack app config
bot_token = os.environ["SLACK_BOT_TOKEN"]      # xoxb-***
app_token = os.environ["SLACK_APP_TOKEN"]      # xapp-***

app = App(token=bot_token)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def send_papers_to_slack(papers_to_send, say):
    """
    Send papers to Slack channel using say function.
    
    Args:
        papers_to_send: List of tuples (overall_rating, message, entry_id)
        say: Slack say function to send messages
    """
    if len(papers_to_send) == 0:
        say("No new papers found.")
        return
    
    for overall_rating, message, entry_id in papers_to_send:
        # Create interactive buttons for Slack
        # Slack allows max 5 buttons per action block, so we split into two blocks
        keys = ["👎", "2️⃣", "3️⃣", "4️⃣", "👍", "️❤️"]
        buttons1 = [
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": emoji
                },
                "value": f"rating{idx}_{entry_id}",
                "action_id": f"rating_{idx}"
            }
            for idx, emoji in enumerate(keys[:3], 1)
        ]
        buttons2 = [
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": emoji
                },
                "value": f"rating{idx}_{entry_id}",
                "action_id": f"rating_{idx}"
            }
            for idx, emoji in enumerate(keys[3:], 4)
        ]
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": message
                }
            },
            {
                "type": "actions",
                "elements": buttons1
            },
            {
                "type": "actions",
                "elements": buttons2
            }
        ]
        
        try:
            say(blocks=blocks, text=message)
        except SlackApiError as e:
            logger.error(f"Error sending message: {e}")


def handle_feedback(ack, body, logger):
    """Handle feedback from Slack users."""
    ack()
    
    action = body["actions"][0]
    value = action["value"]
    feedback_type, entry_id = value.split('_', 1)
    user_id = body["user"]["id"]
    
    # Save feedback using core module
    save_feedback(feedback_type, entry_id, user_id)
    
    # Update the message to remove buttons
    try:
        app.client.chat_update(
            channel=body["channel"]["id"],
            ts=body["message"]["ts"],
            text=body["message"]["text"],
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": body["message"]["text"]
                    }
                }
            ]
        )
        
        # Send acknowledgment
        app.client.chat_postEphemeral(
            channel=body["channel"]["id"],
            user=user_id,
            text=f"Thank you for your feedback: {feedback_type}"
        )
    except SlackApiError as e:
        logger.error(f"Error handling feedback: {e}")


# Register action handlers for all rating buttons
for i in range(1, 7):
    app.action(f"rating_{i}")(handle_feedback)


@app.command("/get")
def handle_get_command(ack, command, respond, logger):
    """Handle /get command to retrieve papers by tag."""
    ack()
    
    tags = command.get("text", "").strip().split()
    
    if not tags:
        respond("Please provide tags to search for. Usage: /get tag1 tag2 tag3")
        return
    
    for tag in tags:
        # Retrieve papers using core module
        papers = retrieve_papers_by_tag(tag)
        
        if papers:
            papers_text = '\n\n'.join(papers)
            respond(f"For tag `{tag}`, the papers are the following:\n\n{papers_text}")
        else:
            respond(f"No papers found for tag `{tag}`")


@app.command("/fetch")
def handle_fetch_command(ack, command, respond, say, logger):
    """Handle /fetch command to fetch and send papers."""
    if ack is not None:
       ack()
    
    # Get keywords from command text, or use default
    keywords = command.get("text", "").strip()
    if not keywords:
        keywords = "reasoning,planning,preference,optimization,symbolic,grokking"
    
    # Get backdays from command text, default to 7
    backdays = 7
    try:
        parts = keywords.split()
        if len(parts) > 1:
            backdays = int(parts[-1])
            keywords = ' '.join(parts[:-1])
    except ValueError:
        pass
    
    respond(f"Fetching papers for keywords: {keywords}, looking back {backdays} days...")
    
    # Fetch papers using core module
    papers_to_send = get_rated_papers(keywords, backdays)

    import pdb
    pdb.set_trace()
    
    # Send papers to Slack using say
    send_papers_to_slack(papers_to_send, say)


# Reply when someone mentions the bot
@app.event("app_mention")
def handle_app_mention(event, say, logger):
    # Call the /fetch command to fetch papers and send them to Slack
    # Extract the keywords and backdays from the mention
    keywords = event.get("text", "")
    handle_fetch_command(ack=None, command={"text": keywords}, respond=say, say=say, logger=logger)

if __name__ == "__main__":
    SocketModeHandler(app, app_token).start()
