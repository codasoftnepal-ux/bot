import discord
import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
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
        self._inactivity_enabled = config.get('INACTIVITY_AUTO_MESSAGE_ENABLED', False)
        configured_channel_id = config.get('INACTIVITY_AUTO_MESSAGE_CHANNEL_ID')
        self._inactivity_channel_id = int(configured_channel_id) if configured_channel_id is not None else None
        self._inactivity_channel_name = str(config.get('INACTIVITY_AUTO_MESSAGE_CHANNEL_NAME', 'general')).strip().lower()
        self._inactivity_message = str(
            config.get(
                'INACTIVITY_AUTO_MESSAGE_TEXT',
                "It's a little too quiet in here. Someone say something interesting.",
            )
        )
        min_wait = int(config.get('INACTIVITY_AUTO_MESSAGE_MIN_SECONDS', 300))
        max_wait = int(config.get('INACTIVITY_AUTO_MESSAGE_MAX_SECONDS', 360))
        self._inactivity_min_seconds = max(1, min_wait)
        self._inactivity_max_seconds = max(self._inactivity_min_seconds, max_wait)
        sequence_cfg = config.get('INACTIVITY_AUTO_MESSAGE_SEQUENCE_SECONDS')
        if isinstance(sequence_cfg, list):
            parsed_sequence = []
            for item in sequence_cfg:
                try:
                    seconds = int(item)
                except (TypeError, ValueError):
                    continue
                if seconds > 0:
                    parsed_sequence.append(seconds)
            self._inactivity_sequence_seconds = parsed_sequence
        else:
            self._inactivity_sequence_seconds = []
        if not self._inactivity_sequence_seconds:
            self._inactivity_sequence_seconds = [self._inactivity_min_seconds]
        self._inactivity_poll_interval = max(5, int(config.get('INACTIVITY_AUTO_MESSAGE_POLL_SECONDS', 15)))
        self._daily_enabled = bool(config.get('INACTIVITY_DAILY_MESSAGE_ENABLED', False))
        self._daily_message = str(config.get('INACTIVITY_DAILY_MESSAGE_TEXT', 'where are you guys'))
        self._daily_hour = max(0, min(23, int(config.get('INACTIVITY_DAILY_MESSAGE_HOUR', 19))))
        self._daily_minute = max(0, min(59, int(config.get('INACTIVITY_DAILY_MESSAGE_MINUTE', 0))))
        self._daily_required_silence = max(1, int(config.get('INACTIVITY_DAILY_REQUIRED_SILENCE_SECONDS', 900)))
        self._daily_utc_offset_minutes = int(config.get('INACTIVITY_DAILY_UTC_OFFSET_MINUTES', 345))
        self._inactivity_states = {}
        self._inactivity_task = None

    async def cog_load(self):
        if self._inactivity_enabled and self._inactivity_task is None:
            self._inactivity_task = asyncio.create_task(self._inactivity_worker())

    async def cog_unload(self):
        if self._inactivity_task is not None:
            self._inactivity_task.cancel()
            self._inactivity_task = None

    def _reset_inactivity_for_channel(self, channel_id: int):
        now = time.monotonic()
        self._inactivity_states[channel_id] = {
            "started_at": now,
            "next_sequence_index": 0,
            "next_send_at": now + self._inactivity_sequence_seconds[0],
        }

    def _resolve_target_channels(self):
        if self._inactivity_channel_id is not None:
            channel = self.bot.get_channel(self._inactivity_channel_id)
            return [channel] if isinstance(channel, discord.TextChannel) else []

        channels = []
        for guild in self.bot.guilds:
            channel = discord.utils.get(guild.text_channels, name=self._inactivity_channel_name)
            if channel is not None:
                channels.append(channel)
        return channels

    async def _inactivity_worker(self):
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            now = time.monotonic()
            for channel in self._resolve_target_channels():
                if channel is None:
                    continue

                me = channel.guild.me
                if me is None or not channel.permissions_for(me).send_messages:
                    continue

                state = self._inactivity_states.get(channel.id)
                if not state:
                    self._reset_inactivity_for_channel(channel.id)
                    state = self._inactivity_states.get(channel.id)
                    if not state:
                        continue

                if now >= state["next_send_at"]:
                    try:
                        await channel.send(self._inactivity_message)
                        next_index = (state["next_sequence_index"] + 1) % len(self._inactivity_sequence_seconds)
                        state["next_sequence_index"] = next_index
                        state["next_send_at"] = now + self._inactivity_sequence_seconds[next_index]
                    except Exception:
                        continue

                if self._daily_enabled:
                    local_now = datetime.now(timezone.utc) + timedelta(minutes=self._daily_utc_offset_minutes)
                    last_daily_date = state.get("daily_last_sent_date")
                    is_daily_time = local_now.hour == self._daily_hour and local_now.minute == self._daily_minute
                    started_at = state.get("started_at", now)
                    is_silent = (now - started_at) >= self._daily_required_silence
                    if is_daily_time and is_silent and last_daily_date != str(local_now.date()):
                        try:
                            await channel.send(self._daily_message)
                            state["daily_last_sent_date"] = str(local_now.date())
                        except Exception:
                            continue

            await asyncio.sleep(self._inactivity_poll_interval)

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