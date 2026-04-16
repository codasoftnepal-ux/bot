import os
from typing import Any

import discord
from discord.ext import commands
from dotenv import load_dotenv

from cogs import COMMANDS, EVENT_HANDLERS
from bot_utilities.config_loader import config

load_dotenv('.env')


def build_intents() -> discord.Intents:
    """Use non-privileged intents by default so startup works without portal toggles."""
    intents = discord.Intents.default()
    # Optional override for setups that explicitly enable this in the portal.
    intents.message_content = os.getenv('ENABLE_MESSAGE_CONTENT_INTENT', 'false').lower() == 'true'
    return intents

class AIBot(commands.AutoShardedBot):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if config['AUTO_SHARDING']:
            super().__init__(*args, **kwargs)
        else:
            super().__init__(shard_count=1, *args, **kwargs)

    async def setup_hook(self) -> None:
        for cog in COMMANDS:
            cog_name = cog.split('.')[-1]
            discord.client._log.info(f"Loaded Command {cog_name}")
            await self.load_extension(f"{cog}")
        for cog in EVENT_HANDLERS:
            cog_name = cog.split('.')[-1]
            discord.client._log.info(f"Loaded Event Handler {cog_name}")
            await self.load_extension(f"{cog}")
        print('If syncing commands is taking longer than usual you are being ratelimited')
        await self.tree.sync()
        discord.client._log.info(f"Loaded {len(self.commands)} commands")

bot = AIBot(command_prefix=[], intents=build_intents(), help_command=None)

TOKEN = os.getenv('DISCORD_TOKEN')

if TOKEN is None:
    print("\033[31m[ERROR] DISCORD_TOKEN environment variable not set!\033[0m")
    print("\033[33m[SOLUTION] Set DISCORD_TOKEN in Railway variables:\033[0m")
    print("  1. Go to your Railway project")
    print("  2. Click 'Variables' tab")
    print("  3. Add DISCORD_TOKEN=your_token_here")
    print("  4. Redeploy")
    import sys
    sys.exit(1)

bot.run(TOKEN, reconnect=True)
