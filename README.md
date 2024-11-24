# Telegram Message Filter & Forwarder

This Python script automates the filtering and forwarding of messages in Telegram groups using the [Pyrogram](https://docs.pyrogram.org/) library. The bot filters messages based on custom criteria (such as file names or extensions) and automatically forwards them to another group.

## Features

- Filters messages based on file names and extensions.
- Forwards filtered messages to a destination group.
- Runs continuously and automatically processes messages.

## Requirements

- Poetry
- Python 3.7+
- Pyrogram and TgCrypto
- Pydantic Settings

Install dependencies:

```bash

# Installing Poetry
pipx install poetry

# Installing project dependencies
poetry shell
poetry install
```
## Setup

1. **Create a Telegram Application**: Go to [my.telegram.org](https://my.telegram.org/) to get your `API_ID` and `API_HASH`.
2. **Configure Settings**: Update `.env` with your credentials.
3. **Run the Script**:

## Configuration

Create a file called `.env`, define your API credentials, phone number, group IDs, and filter criteria:

```python
ACCOUNT_NAME = '' # JUST TO IDENTIFY SESSION AND 
API_ID = your_api_id # INTEGER
API_HASH = 'your_api_hash' # STRING
PHONE_NUMBER = 'your_phone_number' # STRING INCLUDE INTERNATIONAL CODE
PASSWORD = 'your_password' # STRING OPTIONAL IN CASE YOU HAVE TWO STEPS VERFICATION'
```

## Running the code
```bash
python main.py
```

## Notes

- The script will automatically filter and forward messages from the origin group to the destination group.
- Make sure the bot has permission to send messages to both groups.

For troubleshooting, check the Pyrogram documentation or open an issue.
---


