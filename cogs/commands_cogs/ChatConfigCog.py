import discord
from discord.ext import commands
from discord import app_commands

from ..common import (
    current_language,
    instructions,
    instruc_config,
    message_history,
    asked_questions,
    asked_questions_order,
)
from bot_utilities.config_loader import load_active_channels
import json

class ChatConfigCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_channels = load_active_channels

    @commands.hybrid_command(name="toggleactive", description=current_language["toggleactive"])
    @discord.app_commands.choices(persona=[
        discord.app_commands.Choice(name=persona.capitalize(), value=persona)
        for persona in instructions
    ])
    @commands.has_permissions(administrator=True)
    async def toggleactive(self, ctx, persona: discord.app_commands.Choice[str] = instructions[instruc_config]):
        channel_id = f"{ctx.channel.id}"
        active_channels = self.active_channels()
        if channel_id in active_channels:
            del active_channels[channel_id]
            with open("channels.json", "w", encoding='utf-8') as f:
                json.dump(active_channels, f, indent=4)
            await ctx.send(f"{ctx.channel.mention} {current_language['toggleactive_msg_1']}", delete_after=3)
        else:
            active_channels[channel_id] = persona.value if persona.value else persona
            with open("channels.json", "w", encoding='utf-8') as f:
                json.dump(active_channels, f, indent=4)
            await ctx.send(f"{ctx.channel.mention} {current_language['toggleactive_msg_2']}", delete_after=3)

    def _clear_channel_state(self, channel_id: int) -> int:
        suffix = f"-{channel_id}"
        keys = [key for key in message_history.keys() if key.endswith(suffix)]

        for key in keys:
            message_history.pop(key, None)
            asked_questions.pop(key, None)
            asked_questions_order.pop(key, None)

        return len(keys)

    @commands.hybrid_command(name="delete", description="Delete all chatbot memory for this channel")
    async def delete(self, ctx):
        cleared_count = self._clear_channel_state(ctx.channel.id)
        if cleared_count == 0:
            await ctx.send("No chatbot history found for this channel.", delete_after=4)
            return
        await ctx.send("Chatbot history deleted for this channel.", delete_after=4)

    @commands.hybrid_command(name="clear", description=current_language["bonk"])
    @commands.has_permissions(manage_messages=True)
    @app_commands.describe(limit="Number of recent messages to scan (max 1000)")
    async def clear(self, ctx, limit: int = 200):
        limit = max(1, min(limit, 1000))
        cleared_count = self._clear_channel_state(ctx.channel.id)

        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.interaction.response.defer(ephemeral=True)

        def should_delete(message: discord.Message) -> bool:
            return message.author == ctx.author or message.author == self.bot.user

        deleted_messages = await ctx.channel.purge(limit=limit, check=should_delete)
        status = (
            f"Cleared {len(deleted_messages)} messages and reset {cleared_count} "
            "chat history entries for this channel."
        )

        if ctx.interaction:
            await ctx.interaction.followup.send(status, ephemeral=True)
        else:
            await ctx.send(status, delete_after=3)

async def setup(bot):
    await bot.add_cog(ChatConfigCog(bot))
