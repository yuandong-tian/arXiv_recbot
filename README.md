# Introduction
Here is a simple Telegram bot to recommend arXiv papers daily, obtain your preference ratings and update the recommender models given the preference ratings. 

# Install
## Get Bot token
Create a telegram bot following the instruction [here](https://core.telegram.org/bots/tutorial). Then you get a bot token and store it in `TELEGRAM_BOT_TOKEN_NOTIF_BOT` as an environment variable.

## Get Chat ID
Then you can get your chat id. First randomly chat with the bot you just created in telegram, then run
```
curl https://api.telegram.org/bot{your bot token}/getUpdates
```
and look for chat ids. Save the chat id into `TELEGRAM_BOT_CHAT_ID` as another environment variable. 

## Get Telegram API KEY and PASS
Please follow the instruction [here](https://core.telegram.org/api/obtaining_api_id) to get the telegram API ID and PASS, and store them into `TG_API_ID` and `TG_API_HASH` as environment variables. 

## Install dependency  
```
pip install -r requirement.txt
```

## Run the bot
```
python arxiv_checker.py --first_backcheck_day 3 --keywords llm,search,reasoning,planning,optimization
```
The bot will send you arXiv papers related to your interest every day, to the chat window.  
+ `keywords` specifies the keywords of the paper the bot uses to search, separated by comma. No space needed. 
+ `first_backcheck_day` is to specify how many days to look back to get arXiv papers, when the bot runs at the first time. 

For each paper the bot sends, in the chat window there will be possible ratings ( :thumbsdown: = 1, :thumbsup: = 5 and :heart: = 6 ) for the user to rate. User can press the rating and the bot will receive it (as one reply message from the bot). Users can also reply to the bot messages to put comments and tags associated with the paper. 

User can also suggest papers by send its arXiv link in the chat window. Such papers will automatically be ranked as :thumbsup: = 5. 

User can also retrieve papers by sending `/get tag1 tag2 tag3` in the chat window, and the bot will retrieve all papers that contain the tags. 

## Update the model. 
Once the model collects enough ranking instances (e.g. > 100), user can update the preference model by running the following:
```
python collect_data.py
python preference_model.py
```
It will save the trained model to `pytorch_preference_model.pt` and `tfidf_vectorizer.joblib` (as a TF-IDF vectorizer, name specified in `common.py`). Then you restart `arxiv_checker.py` to load the updated models and continue the recommendation. 

# LICENSE

MIT License