import asyncio
from itertools import cycle
import discord
from discord.ext import commands

from bot_utilities.config_loader import config
from ..common import presences_disabled, presences

class OnReady(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        presence_items = [p for p in presences if isinstance(p, str) and p.strip()]
        if not presence_items:
            presence_items = [f"Serving {len(self.bot.guilds)} guilds"]
        presences_cycle = cycle(presence_items)
        print(f"{self.bot.user} aka {self.bot.user.name} has connected to Discord!")
        invite_link = discord.utils.oauth_url(
            self.bot.user.id,
            permissions=discord.Permissions(),
            scopes=("bot", "applications.commands")
        )
        print(f"Invite link: {invite_link}")
        if presences_disabled:
            return
        while True:
            presence = next(presences_cycle)
            presence_with_count = presence.replace("{guild_count}", str(len(self.bot.guilds)))
            delay = config['PRESENCES_CHANGE_DELAY']
            await self.bot.change_presence(activity=discord.Game(name=presence_with_count))
            await asyncio.sleep(delay)

async def setup(bot):
    await bot.add_cog(OnReady(bot))
