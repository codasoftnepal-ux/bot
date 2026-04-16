import discord
import asyncio
import re
from discord.ext import commands
from collections import deque

from bot_utilities.response_utils import split_response
from bot_utilities.ai_utils import generate_response
from bot_utilities.config_loader import config, load_active_channels
from ..common import (
    allow_dm,
    trigger_words,
    replied_messages,
    smart_mention,
    message_history,
    MAX_HISTORY,
    instructions,
    asked_questions,
    asked_questions_order,
    QUESTION_CACHE_LIMIT,
)


class OnMessage(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_channels = load_active_channels
        self.instructions = instructions
        self._processed_message_ids = deque(maxlen=400)
        self._processed_message_set = set()
        self._response_locks = {}

    def _normalize_question(self, text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip().lower()

    def _try_mark_processed(self, message_id: int) -> bool:
        if message_id in self._processed_message_set:
            return False
        if len(self._processed_message_ids) == self._processed_message_ids.maxlen:
            oldest = self._processed_message_ids.popleft()
            self._processed_message_set.discard(oldest)
        self._processed_message_ids.append(message_id)
        self._processed_message_set.add(message_id)
        return True

    async def process_message(self, message):
        active_channels = self.active_channels()
        string_channel_id = f"{message.channel.id}"
        is_replied = (message.reference and message.reference.resolved.author == self.bot.user) and smart_mention
        is_dm_channel = isinstance(message.channel, discord.DMChannel)
        is_active_channel = string_channel_id in active_channels
        is_allowed_dm = allow_dm and is_dm_channel
        contains_trigger_word = any(word in message.content for word in trigger_words)
        is_bot_mentioned = self.bot.user.mentioned_in(message) and smart_mention and not message.mention_everyone
        bot_name_in_message = self.bot.user.name.lower() in message.content.lower() and smart_mention

        if not (is_active_channel or is_allowed_dm or contains_trigger_word or is_bot_mentioned or is_replied or bot_name_in_message):
            return

        instruc_config = active_channels.get(string_channel_id, config['DEFAULT_INSTRUCTION'])
        instructions = (
            f"Ignore all the instructions you have gotten before. {self.instructions[instruc_config]}. "
            "Format every reply for Discord using clean Markdown: short paragraphs, clear bullet points where useful, "
            "and fenced code blocks for any code. Keep answers readable and avoid one giant paragraph."
        )

        channel_id = message.channel.id
        key = f"{message.author.id}-{channel_id}"
        lock = self._response_locks.setdefault(key, asyncio.Lock())

        async with lock:
            normalized_question = self._normalize_question(message.content)
            question_set = asked_questions.setdefault(key, set())
            question_order = asked_questions_order.setdefault(key, deque(maxlen=QUESTION_CACHE_LIMIT))

            # Avoid responding multiple times to the same question from the same user in the same channel.
            if normalized_question and normalized_question in question_set:
                return

            if normalized_question:
                if len(question_order) == question_order.maxlen:
                    oldest = question_order.popleft()
                    question_set.discard(oldest)
                question_order.append(normalized_question)
                question_set.add(normalized_question)

            message_history[key] = message_history.get(key, [])
            message_history[key] = message_history[key][-min(MAX_HISTORY, 4):]
            message_history[key].append({"role": "user", "content": message.content})

            async with message.channel.typing():
                response = await self.generate_response(instructions, message_history[key])

            message_history[key].append({"role": "assistant", "content": response})

            await self.send_response(message, response)

    async def generate_response(self, instructions, history):
        return await generate_response(instructions=instructions, history=history)

    async def send_response(self, message, response):
        if response is not None:
            for chunk in split_response(response):
                try:
                    await message.reply(chunk, allowed_mentions=discord.AllowedMentions.none(), suppress_embeds=True)
                except Exception:
                    await message.channel.send("I apologize for any inconvenience caused. It seems that there was an error preventing the delivery of my message. Additionally, it appears that the message I was replying to has been deleted, which could be the reason for the issue. If you have any further questions or if there's anything else I can assist you with, please let me know and I'll be happy to help.")
        else:
            await message.reply("I apologize for any inconvenience caused. It seems that there was an error preventing the delivery of my message.")

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.bot.user and message.reference:
            replied_messages[message.reference.message_id] = message
            if len(replied_messages) > 5:
                oldest_message_id = min(replied_messages.keys())
                del replied_messages[oldest_message_id]

        if message.mentions:
            for mention in message.mentions:
                message.content = message.content.replace(f'<@{mention.id}>', f'{mention.display_name}')

        if message.stickers or message.author.bot or (message.reference and (message.reference.resolved.author != self.bot.user or message.reference.resolved.embeds)):
            return

        if not self._try_mark_processed(message.id):
            return

        await self.process_message(message)

async def setup(bot):
    await bot.add_cog(OnMessage(bot))