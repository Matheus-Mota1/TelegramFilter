from bot.settings import Settings
from bot.rate_limit import TokenBucket
from bot.config import ConfigParser
from bpt.FastTelethon import download_file, upload_file
from bot.utils import (
    create_filter_files_regex,
    LinkManager,
    get_file_name,
    get_file_extension,
    empty_queue,
    create_progress_callback,
)
from telethon import TelegramClient
from telethon.tl.types import Message, User, Chat, MessageService
from telethon.errors import (
    FloodWaitError,
    FloodPremiumWaitError,
    FileReferenceExpiredError,
)    
import time
import asyncio
from pathlib import Path
from datetime import datetime
import logging
import os

logging.basicConfig(
    format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s',
    level=logging.WARNING
)

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    print("Using uvloop as the event loop.")
except ImportError:
    print("uvloop not installed, using the default asyncio event loop.")

settings = Settings()

class Bot(TelegramClient):

    def __init__(self):
        self.messages_queue = asyncio.Queue()
        self.download_queue = asyncio.Queue()
        self.finished_queue = False
        self.finished_dequeue = False

        # Usefull in case of file_id expiration or save to continue later
        self.last_processed_msg = 0

        # Since rate limit is the amount of messages sended per minute
        seconds_in_minute = 60
        self.rate_limit = 60
        self.interval = seconds_in_minute / self.rate_limit

        self.bucket = TokenBucket(
            inicial_tokens=1,
            max_tokens=self.rate_limit,
            refill_interval=self.interval
        )
        
        # Define the download directory
        self.download_dir = Path('./downloads')
        self.download_dir.mkdir(exist_ok=True)

        # Get API keys at https://my.telegram.org/auth
        super().__init__(
            session=settings.account_name,
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            flood_sleep_threshold=11
        )
    
    async def get_last_message(self, origin_chat):
        async for message in self.iter_messages(entity=origin_chat, limit=1):
            print("Total messages in the group", message.id)
            return message.id


    async def _get_chat_messages(
        self, 
        origin_chat: Chat,
        offset_id: int = 0,
        limit: int = 100,
        reverse: bool = True,
        offset_date: datetime|None = None,
    ) -> None:
        
        try:
            async for message in self.iter_messages(
                entity=origin_chat,
                limit=limit,
                offset_id=offset_id,
                offset_date=offset_date,
                reverse=reverse
            ):
                      
                print(f"Fetched message ID: {message.id}")
                await self.messages_queue.put(message)

                if message.id == self.last_msg_id:
                    self.finished_queue = True
                    print("All messages fetched")
                    return
              
        except FloodWaitError as e:
            print(f"FloodError detected, it's normal. Waiting {e.value} seconds...")
            await asyncio.sleep(e.seconds)

    async def _queue_downloads(
        self, 
        message: Message,
    ) -> Message|None:
   

        # We first try to get from the telegram atribute
        # I added the message_id, to avoid filename conflicts
        # Since telegram can have two files with the same name in a group
        # If we don't provide a filename it will be random
        filename = get_file_name(message)

        if filename:
            file_path = self.download_dir / f"message_{message.id}_{filename}"
        else:
            file_path = self.download_dir / f"{message.id}_temp"
        
        start_time = time.time()
        await self.download_media(
            message=message, 
            file=str(file_path),
            progress_callback=create_progress_callback(
                start_time, f"Downloading message_id:{message.id}"),
        )
    
        # Sometimes the file in the telegram doesn't have filename
        # Which has the extension that is a requirement to telegram upload
        if not filename:
            extension = get_file_extension(str(file_path))
            new_file_path = file_path.with_suffix(f".{extension}")
            os.rename(file_path, new_file_path)
            file_path = new_file_path
       
        await self.download_queue.put((message, file_path))

    async def _send_copy_message(
        self,
        chat_id: int | str,
        message: Message,
        reply_to_message_id: int | None = None,
        file_path: str | None = None,
        thumb: str | None = None,
        media: bool = False,
    ) -> Message | None:
        

        if message.file is None:
            return await self.send_message(
                entity=chat_id,
                message=message,
                reply_to=reply_to_message_id,
            )
        
        
        # Have to improve it later, did only to avoid
        # Clutter the chat if progress_callback without content

        if message.noforwards:
            start_time = time.time()

            await self.send_file(
                entity=chat_id,
                file=file_path or message.media,
                file_name=message.file.name,
                caption=message.text,
                reply_to=reply_to_message_id,
                progress_callback=create_progress_callback(
                    start_time, f"Uploading   message_id:{message.id}"
                ),
            )

        else:
            await self.send_file(
                entity=chat_id,
                file=file_path or message.media,
                file_name=message.file.name,
                caption=message.text,
                reply_to=reply_to_message_id,
            )


    async def _send_messages(
        self,
        origin_chat: Chat,
        destiny_chat: Chat,
        topic_id: int|None = None,
        rate_limit: int = 20,
        offset_id: int = 0,
        offset_date: datetime | None = None
    ) -> None:
        """
        This function is responsible to take the messages captured and
        give them a proper destiny, depending of group status.

        It send messages if possible, otherwise put them for download

        Forward disable: 
            If downloadable media donwload, and upload after
            If non downloable media or message, send a copy

        Forward enable:
            If message content is protected, same approach as forward disable
            Just mass forward with limit rate normally
        
        Exceptions:
            FileReferenceExpired, when detected we have to clean all our 
            queues and fetch the messages again where the error has occurred

        FloodError
           Telegram is impling limit and tell us to slow down. Just wait X time in seconds
           Usually 15 seconds for upload and 10 seconds for donwload.
        """

        self.last_processed_msg = offset_id
        self.last_msg_id = await self.get_last_message(origin_chat)

        while True:
            
            if self.messages_queue.empty():
                if self.finished_queue:
                    break
                
                print("All messages processed, fetching more...")
                await self._get_chat_messages(
                    origin_chat=origin_chat,
                    offset_id=self.last_processed_msg,
                    offset_date=offset_date,
                ) 

            while not self.bucket.consume():
                print("Preveting flood, waiting seconds:", self.interval)
                await asyncio.sleep(self.interval)

            message: Message = await self.messages_queue.get()
                       
            try:
                await self._messages_trial(
                    destiny_group=destiny_chat, 
                    origin_group=origin_chat,
                    message=message,
                    topic_id=topic_id,
                )
           
            except FileReferenceExpiredError:
                print("File reference expired, refreshing...")
                empty_queue(self.messages_queue)
                empty_queue(self.download_queue)
            
            finally:
                self.last_processed_msg = message.id
                self.messages_queue.task_done()

        print("All messages processed")
        self.finished_dequeue = True

    async def _upload_downloads(
        self, 
        destiny_chat_id: int|str,
        reply_to_message_id: int|None = None
    ) -> Message|None:

        """
        Takes the message information and the file path in the downloads dir
        To upload the message with the same metadata as the original message
        """

        while True:

            if self.download_queue.empty():
                if self.finished_dequeue:
                    break
                await asyncio.sleep(5)
            
            message, file_path = await self.download_queue.get()
           
            try:
                await self._send_copy_message(
                    chat_id=destiny_chat_id,
                    message=message,
                    reply_to_message_id=reply_to_message_id,
                    file_path=file_path,
                )

            except FileReferenceExpiredError:
                print("File reference expired, refreshing...")
                empty_queue(self.messages_queue)
                empty_queue(self.download_queue)

            except Exception as e:
                print("Error in message trial:", e)
        
            finally:
                self.last_processed_msg = message.id
                self.download_queue.task_done()


        print("All medias uploaded")

    async def _messages_trial(
        self, 
        destiny_group: Chat,
        origin_group: Chat,
        message: Message,
        topic_id: int | None = None
    ) -> Message|None:

        # Send copy messages, it's same as a forward but, copying the
        # content, so it works for unrestricted and restricted content
        # Does't work with resctricted media, that we have to download
       
        if isinstance(message, MessageService):
            print(f"Skipping message ID {message.id} as it's a service message.")
            return None


        protected_content_media = (
            (
                origin_group.noforwards or 
                message.noforwards 
            ) and message.media
        )

        if protected_content_media:
            return await self._queue_downloads(message)
        
        else:
            print("Copied message_id", message.id)
            return await self._send_copy_message(
                chat_id=destiny_group.id,
                message=message,
                reply_to_message_id=topic_id,
            )

    async def clone_messages(
        self, 
        origin_group_id: int|str,
        destiny_group_id: int|str,
        new_group_name: str|None = None,
        topic_id: int|None = None,
        offset_id: int = 0,
        offset_date: datetime | None = None
    ) -> None:
        
        try:
            origin_chat = await self.get_entity(origin_group_id)
            print(f"Origin group: {origin_chat.title} is alright")
        except Exception as e:
            raise f"Error with origin chat: {e}"

        try:
            destiny_chat = await self.get_entity(destiny_group_id)
            print(f"Destiny group: {destiny_chat.title} is alright")
        except Exception as e:
            raise f"Error with destiny chat: {e}"

        await asyncio.gather(

            self._send_messages(
                destiny_chat=destiny_chat,
                origin_chat=origin_chat,
                topic_id=topic_id,
                offset_id=offset_id,
                offset_date=offset_date,

            ),

            self._upload_downloads(
                destiny_chat_id=destiny_chat.id,
                reply_to_message_id=topic_id,
            )
        )


async def main():
    bot = Bot()
    await bot.start(
        phone=settings.phone_number,
        password=settings.password
    )

    # Chat to catch from
    origin_group = -1001889466238

    # Chat to send to
    destiny_group = -1002070526963

    # Topic that you want to send
    topic_id = 7342

    
    # The last message it stoped
    offset_id = 849 

    print("\n>>> Cloner up and running.\n")
    await bot.clone_messages(
        origin_group_id=origin_group,
        destiny_group_id=destiny_group,
        topic_id=topic_id,
        offset_id=offset_id
    )

    await bot.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
