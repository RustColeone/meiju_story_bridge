"""
Discord adapter entrypoint.
Keeps platform wiring here and delegates bridge/session orchestration to session_manager.py.
"""
import os

import discord
import yaml

from session_manager import BridgeSessionManager

try:
    from bridgeParser import parse_bridge_command
    BRIDGE_AVAILABLE = True
except ImportError:
    BRIDGE_AVAILABLE = False
    print("⚠️  Bridge system not available. Create bridgeParser.py to enable bridge functionality.")


with open("config.yml", "r") as ymlfile:
    botConfig = yaml.safe_load(ymlfile)

if os.getenv("TOKEN"):
    botConfig["TOKEN"] = os.getenv("TOKEN")

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)


async def _send_text(target, text: str):
    await target.send(text)


def _schedule_task(coro):
    return bot.loop.create_task(coro)


def _get_target_by_channel_id(channel_id: str):
    try:
        return bot.get_channel(int(channel_id))
    except Exception:
        return None


def _get_bot_name() -> str:
    return bot.user.name if bot.user else "Bot"


session_manager = BridgeSessionManager(
    parse_bridge_command=parse_bridge_command if BRIDGE_AVAILABLE else None,
    send_text=_send_text,
    schedule_task=_schedule_task,
    get_target_by_channel_id=_get_target_by_channel_id,
    get_bot_name=_get_bot_name,
)


@bot.event
async def on_ready():
    """Called when bot successfully connects to Discord"""
    print(f"✅ Logged in as {bot.user.name} (ID: {bot.user.id})")
    print(f"🔌 Bridge system: {'Enabled' if BRIDGE_AVAILABLE else 'Disabled'}")
    print("Ready to receive commands!")
    if BRIDGE_AVAILABLE:
        bot.loop.create_task(session_manager.startup_auto_init_loop())


@bot.event
async def on_message(message):
    """Handle incoming Discord messages"""
    if message.author == bot.user:
        return

    msgChannel = (
        "DM"
        if isinstance(message.channel, discord.DMChannel)
        else f"{message.guild.name}/{message.channel.name}"
    )
    print(f"[{msgChannel}] {message.author.name}: {message.content}")

    channel_id = str(message.channel.id)

    if not message.content.startswith("$") and BRIDGE_AVAILABLE:
        handled = await session_manager.handle_listen_mode_message(
            target=message.channel,
            msg_channel=msgChannel,
            channel_id=channel_id,
            content=message.content,
        )
        if handled:
            return

    if message.content.startswith("$bridge"):
        if not BRIDGE_AVAILABLE:
            error_msg = "❌ Bridge system not available. Check that bridgeParser.py exists."
            print(f"[{msgChannel}] {_get_bot_name()}: {error_msg}")
            await message.channel.send(error_msg)
            return

        await session_manager.handle_bridge_command(
            target=message.channel,
            msg_channel=msgChannel,
            channel_id=channel_id,
            content=message.content,
        )

    elif message.content.startswith("$help"):
        print(f"[{msgChannel}] {_get_bot_name()}: [Help text sent]")
        await message.channel.send(session_manager.get_help_text())


def run():
    print("Starting Discord Bridge Bot...")
    bot.run(botConfig["TOKEN"])


if __name__ == "__main__":
    run()
