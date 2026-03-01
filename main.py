"""
Minimal Discord Bot for Bridge System
Bridges Discord messages to external applications (e.g., 妹居物语 game)
"""
import discord
import yaml
import os
import asyncio

# Bridge system
try:
    from bridgeParser import parse_bridge_command
    BRIDGE_AVAILABLE = True
except ImportError:
    BRIDGE_AVAILABLE = False
    print("⚠️  Bridge system not available. Create bridgeParser.py to enable bridge functionality.")

# Load configuration
with open("config.yml", "r") as ymlfile:
    botConfig = yaml.safe_load(ymlfile)

# Override with environment variable if exists
if os.getenv("TOKEN"):
    botConfig["TOKEN"] = os.getenv("TOKEN")

# Initialize Discord bot with only necessary intents
intents = discord.Intents.default()
intents.message_content = True  # Required to read message text (privileged intent)
bot = discord.Client(intents=intents)

# Bridge instances (per channel)
bridge_instances = {}  # channel_id -> BridgedObject
story_listeners = {}  # channel_id -> True/False (active listener)
channel_send_busy = {}  # channel_id -> True/False (waiting for game response)
AUTO_INIT_CHANNEL_ID = "__auto_init__"

BADGE_STORY = "📖 STORY (active)"
BADGE_DIALOGUE = "🗨️ DIALOGUE (auto-continue step)"
BADGE_THINKING = "⏳ THINKING (input blocked)"
BADGE_INPUT = "💬 INPUT (user can send)"
BADGE_ENDED = "✅ ENDED"
STORY_SEND_ACK = "✅ Message sent in story mode. Waiting for story to continue..."
WAIT_FOR_GAME_REPLY = "⏳ **Game is still responding**\nPlease wait for game response and try again."
STORY_INPUT_HELP = (
    "💬 **Game is waiting for your response**\n"
    "You can:\n"
    "• Use `$bridge -m <your response>`\n"
    "• Enable listen mode with `$bridge --listen on` and type directly"
)


async def safe_typing_pulse(channel, seconds: float = 0.8):
    """Best-effort typing indicator compatible with current discord.py channel types."""
    try:
        async with channel.typing():
            await asyncio.sleep(seconds)
    except Exception:
        pass


async def story_mode_listener(channel_id, bridge, timeout_minutes=1, initial_dialogue=""):
    """Monitor story mode and auto-progress dialogue until player input is needed."""
    import time
    
    channel = bot.get_channel(int(channel_id))
    if not channel:
        story_listeners[channel_id] = False
        return
    
    last_dialogue = (initial_dialogue or "").strip()
    pending_auto_continue = bool(last_dialogue)
    story_end_confirm_seconds = 5
    story_ended_time = None  # Track when story mode first became hidden
    last_state = None  # Track last state to avoid duplicate logging
    last_state_badge = None  # Track last badge sent to avoid badge spam
    
    print(f"[Story Listener] Started for channel {channel_id}")
    
    try:
        while story_listeners.get(channel_id) and bridge.connected:
            await asyncio.sleep(1.0)  # Check every second
            
            try:
                is_story, dialogue, has_dialogue, has_input = await bridge.check_story_mode()
                
                # Determine current state
                if has_dialogue:
                    current_state = "dialogue"
                elif has_input:
                    current_state = "input"
                else:
                    current_state = "generating"
                
                # Log state changes only
                if current_state != last_state:
                    if current_state == "dialogue":
                        print(f"[Story Listener] State: Game showing dialogue")
                    elif current_state == "input":
                        print(f"[Story Listener] State: Waiting for user input")
                    elif current_state == "generating":
                        print(f"[Story Listener] State: Yuki generating/thinking")
                    last_state = current_state

                # Send state badge only on state transition
                state_to_badge = {
                    "dialogue": BADGE_DIALOGUE,
                    "input": BADGE_INPUT,
                    "generating": BADGE_THINKING,
                }
                badge = state_to_badge.get(current_state)
                if badge and badge != last_state_badge:
                    await channel.send(badge)
                    last_state_badge = badge
                    if current_state == "generating":
                        await safe_typing_pulse(channel)
                
                # Timeout only applies when dialogue box is visible (same dialogue stuck too long)
                if has_dialogue:
                    current_dialogue = (dialogue or "").strip()

                    if pending_auto_continue and current_dialogue and current_dialogue == last_dialogue:
                        # Listener started with an already-posted dialogue; auto-continue once without reposting
                        continue_result = await bridge.story_continue()
                        if not continue_result.startswith("✅"):
                            print(f"[Story Listener] Auto-continue failed: {continue_result}")
                        pending_auto_continue = False
                        continue

                    # Dialogue box is visible
                    if current_dialogue and current_dialogue == last_dialogue:
                        pass
                    elif current_dialogue and current_dialogue != last_dialogue:
                        # New dialogue appeared - forward, then auto-continue
                        await channel.send(current_dialogue)
                        last_dialogue = current_dialogue
                        pending_auto_continue = False

                        # Auto progress story whenever new text appears
                        continue_result = await bridge.story_continue()
                        if not continue_result.startswith("✅"):
                            print(f"[Story Listener] Auto-continue failed: {continue_result}")

                elif has_input:
                    # Input state is indicated by badge; avoid repeating detailed help text
                    pass
                else:
                    # No dialogue/input visible (generating/transitioning)
                    pass
                
                # Story mode ended (both input and dialogue box not visible)
                if not is_story:
                    if story_ended_time is None:
                        # First time detecting story mode ended
                        story_ended_time = time.time()
                        print(f"[Story Listener] Story mode ended, waiting {story_end_confirm_seconds} seconds to confirm...")
                    elif time.time() - story_ended_time >= story_end_confirm_seconds:
                        # Story mode has been hidden long enough: try ending conversation then stop listener
                        end_result = await bridge.end_conversation()
                        await channel.send(BADGE_ENDED)
                        await channel.send(f"📖 **Story Mode Ended** - Normal chat resumed\n{end_result}")
                        story_listeners[channel_id] = False
                        break
                else:
                    # Story mode is active again, reset end timer
                    if story_ended_time is not None:
                        print(f"[Story Listener] Story mode resumed, canceling end timer")
                        story_ended_time = None
                    
            except Exception as e:
                print(f"[Story Listener] Error for channel {channel_id}: {e}")
                await asyncio.sleep(1)
                
    except Exception as e:
        print(f"[Story Listener] Fatal error: {e}")
    finally:
        story_listeners[channel_id] = False


async def startup_auto_init_loop():
    """Default behavior: try init automatically on startup until success."""
    if not BRIDGE_AVAILABLE:
        return

    await asyncio.sleep(1)
    while True:
        try:
            parse_result = parse_bridge_command("$bridge --init", bridge_instances, AUTO_INIT_CHANNEL_ID)
            if len(parse_result) == 4:
                _, _, bridge, _ = parse_result
            else:
                _, _, bridge = parse_result

            if bridge.connected:
                print("[Auto Init] ✅ Bridge already connected")
                return

            print("[Auto Init] Attempting bridge initialization...")
            success = await bridge.initialize()
            if success:
                print("[Auto Init] ✅ Bridge initialized successfully")
                return

            print("[Auto Init] ❌ Init failed, retrying in 10s...")
        except Exception as e:
            print(f"[Auto Init] Error: {e}")

        await asyncio.sleep(10)


async def execute_bridge_action(message, msgChannel, channel_id, action_type, result_text, bridge, extra=None):
    """Execute one parsed bridge action."""
    # Handle different action types
    if action_type in ['help', 'status', 'listen']:
        print(f"[{msgChannel}] {bot.user.name}: {result_text}")
        await message.channel.send(result_text)

    elif action_type == 'info':
        print(f"[{msgChannel}] {bot.user.name}: {result_text}")
        await message.channel.send(result_text)
        info = await bridge.get_game_info()
        if info:
            print(f"[{msgChannel}] {bot.user.name}: [Game info sent]")
            await message.channel.send(info)
        else:
            error_msg = "❌ Failed to retrieve game info"
            print(f"[{msgChannel}] {bot.user.name}: {error_msg}")
            await message.channel.send(error_msg)

    elif action_type == 'diary':
        print(f"[{msgChannel}] {bot.user.name}: {result_text}")
        await message.channel.send(result_text)
        diary = await bridge.get_diary_entry(extra)  # extra is the index
        if diary:
            print(f"[{msgChannel}] {bot.user.name}: [Diary entry sent]")
            await message.channel.send(diary)
        else:
            error_msg = "❌ Failed to retrieve diary entry"
            print(f"[{msgChannel}] {bot.user.name}: {error_msg}")
            await message.channel.send(error_msg)

    elif action_type == 'init':
        print(f"[{msgChannel}] {bot.user.name}: {result_text}")
        await message.channel.send(result_text)
        success = await bridge.initialize()

        # Send connection status to user
        if hasattr(bridge, 'last_status_message') and bridge.last_status_message:
            print(f"[{msgChannel}] {bot.user.name}: {bridge.last_status_message}")
            await message.channel.send(bridge.last_status_message)

        if success:
            success_msg = "✅ Bridge ready to use!"
            print(f"[{msgChannel}] {bot.user.name}: {success_msg}")
            await message.channel.send(success_msg)

            # Check if game is in story mode
            try:
                is_story, dialogue, has_dialogue, has_input = await bridge.check_story_mode()
                if is_story:
                    await message.channel.send(BADGE_STORY)
                    story_msg = "📖 **Story Mode Detected!**\nAuto-forwarding dialogue...\n\n"
                    if dialogue:
                        story_msg += dialogue
                    elif has_input:
                        story_msg += "💬 Waiting for your response input..."
                    else:
                        story_msg += "⏳ Yuki is thinking..."
                    story_msg += "\n\n" + STORY_INPUT_HELP
                    print(f"[{msgChannel}] {bot.user.name}: [Story mode detected]")
                    await message.channel.send(story_msg)
                    # Start story listener
                    story_listeners[channel_id] = True
                    bot.loop.create_task(story_mode_listener(channel_id, bridge, initial_dialogue=dialogue or ""))
            except Exception as e:
                print(f"[{msgChannel}] Error checking story mode: {e}")
        else:
            error_msg = "❌ Game window not found. Make sure the game is running."
            print(f"[{msgChannel}] {bot.user.name}: {error_msg}")
            await message.channel.send(error_msg)

    elif action_type == 'calibration':
        print(f"[{msgChannel}] {bot.user.name}: {result_text}")
        await message.channel.send(result_text)
        success = await bridge.calibrate()
        if success:
            success_msg = "✅ Calibration complete! Check console for new ratios."
            print(f"[{msgChannel}] {bot.user.name}: {success_msg}")
            await message.channel.send(success_msg)
        else:
            error_msg = "❌ Calibration failed or timed out"
            print(f"[{msgChannel}] {bot.user.name}: {error_msg}")
            await message.channel.send(error_msg)

    elif action_type == 'end-chat':
        print(f"[{msgChannel}] {bot.user.name}: {result_text}")
        await message.channel.send(result_text)
        result = await bridge.end_conversation()
        print(f"[{msgChannel}] {bot.user.name}: {result}")
        await message.channel.send(result)

    elif action_type == 'continue':
        print(f"[{msgChannel}] {bot.user.name}: {result_text}")
        await message.channel.send(result_text)
        result = await bridge.story_continue()
        print(f"[{msgChannel}] {bot.user.name}: {result}")
        await message.channel.send(result)

    elif action_type == 'greet':
        print(f"[{msgChannel}] {bot.user.name}: {result_text}")
        await message.channel.send(result_text)

        if channel_send_busy.get(channel_id, False):
            await message.channel.send(WAIT_FOR_GAME_REPLY)
            return

        # Same availability rule as normal chat send: not allowed during story mode
        try:
            is_story, _, _, _ = await bridge.check_story_mode()
            if is_story:
                blocked_msg = "❌ Not available in story mode. Finish story mode first."
                print(f"[{msgChannel}] {bot.user.name}: {blocked_msg}")
                await message.channel.send(blocked_msg)
                return
        except Exception as e:
            print(f"[{msgChannel}] Error checking story mode for greet: {e}")

        channel_send_busy[channel_id] = True
        try:
            async with message.channel.typing():
                result = await bridge.trigger_greeting()
        finally:
            channel_send_busy[channel_id] = False

        if result:
            print(f"[{msgChannel}] {bot.user.name}: {result}")
            await message.channel.send(result)
        else:
            error_msg = "❌ No reply received"
            print(f"[{msgChannel}] {bot.user.name}: {error_msg}")
            await message.channel.send(error_msg)

    elif action_type == 'disconnect':
        print(f"[{msgChannel}] {bot.user.name}: {result_text}")
        await message.channel.send(result_text)
        success = await bridge.disconnect()
        if success:
            if channel_id in bridge_instances:
                del bridge_instances[channel_id]
            success_msg = "✅ Bridge disconnected"
            print(f"[{msgChannel}] {bot.user.name}: {success_msg}")
            await message.channel.send(success_msg)
        else:
            error_msg = "❌ Failed to disconnect"
            print(f"[{msgChannel}] {bot.user.name}: {error_msg}")
            await message.channel.send(error_msg)

    elif action_type == 'send':
        if channel_send_busy.get(channel_id, False):
            await message.channel.send(WAIT_FOR_GAME_REPLY)
            return

        # Check if in story mode before sending
        try:
            is_story, dialogue, has_dialogue, has_input = await bridge.check_story_mode()
            if is_story and not story_listeners.get(channel_id):
                # Story mode detected, start listener
                await message.channel.send(BADGE_STORY)
                story_msg = "📖 **Story Mode Detected!**\nAuto-forwarding dialogue...\n\n"
                if dialogue:
                    story_msg += dialogue
                elif has_input:
                    story_msg += "💬 Waiting for your response input..."
                else:
                    story_msg += "⏳ Yuki is thinking..."
                story_msg += "\n\n" + STORY_INPUT_HELP
                print(f"[{msgChannel}] {bot.user.name}: [Story mode detected]")
                await message.channel.send(story_msg)
                story_listeners[channel_id] = True
                bot.loop.create_task(story_mode_listener(channel_id, bridge, initial_dialogue=dialogue or ""))
                return

            # Block direct send command while story mode is generating/thinking
            if is_story and (not has_dialogue) and (not has_input):
                await message.channel.send(BADGE_THINKING)
                await message.channel.send("⏳ **Yuki 正在思考中**\n请稍等，暂时不能发送消息。")
                return
        except Exception as e:
            print(f"[{msgChannel}] Error checking story mode: {e}")

        channel_send_busy[channel_id] = True
        try:
            async with message.channel.typing():
                reply = await bridge.send_message(result_text)
        finally:
            channel_send_busy[channel_id] = False

        if reply:
            print(f"[{msgChannel}] {bot.user.name}: {reply}")
            if reply == STORY_SEND_ACK:
                await safe_typing_pulse(message.channel)
            else:
                await message.channel.send(reply)
        else:
            error_msg = "❌ No reply received"
            print(f"[{msgChannel}] {bot.user.name}: {error_msg}")
            await message.channel.send(error_msg)

    else:
        unknown_msg = f"❌ Unsupported action: {action_type}"
        print(f"[{msgChannel}] {bot.user.name}: {unknown_msg}")
        await message.channel.send(unknown_msg)


@bot.event
async def on_ready():
    """Called when bot successfully connects to Discord"""
    print(f'✅ Logged in as {bot.user.name} (ID: {bot.user.id})')
    print(f'🔌 Bridge system: {"Enabled" if BRIDGE_AVAILABLE else "Disabled"}')
    print('Ready to receive commands!')
    if BRIDGE_AVAILABLE:
        bot.loop.create_task(startup_auto_init_loop())


@bot.event
async def on_message(message):
    """Handle incoming Discord messages"""
    # Ignore messages from the bot itself
    if message.author == bot.user:
        return
    
    # Log message for debugging
    msgChannel = "DM" if isinstance(message.channel, discord.DMChannel) else f"{message.guild.name}/{message.channel.name}"
    print(f"[{msgChannel}] {message.author.name}: {message.content}")
    
    # Check bridge listen mode for non-command messages
    if not message.content.startswith('$'):
        if BRIDGE_AVAILABLE:
            channel_id = str(message.channel.id)
            if channel_id in bridge_instances:
                bridge = bridge_instances[channel_id]
                if bridge.is_listening():
                    if channel_send_busy.get(channel_id, False):
                        await message.channel.send(WAIT_FOR_GAME_REPLY)
                        return

                    # Check if in story mode before sending
                    try:
                        is_story, dialogue, has_dialogue, has_input = await bridge.check_story_mode()
                        if is_story and not story_listeners.get(channel_id):
                            # Story mode detected, start listener
                            await message.channel.send(BADGE_STORY)
                            story_msg = "📖 **Story Mode Detected!**\nAuto-forwarding dialogue...\n\n"
                            if dialogue:
                                story_msg += dialogue
                            elif has_input:
                                story_msg += "💬 Waiting for your response input..."
                            else:
                                story_msg += "⏳ Yuki is thinking..."
                            story_msg += "\n\n" + STORY_INPUT_HELP
                            print(f"[{msgChannel}] {bot.user.name}: [Story mode detected]")
                            await message.channel.send(story_msg)
                            story_listeners[channel_id] = True
                            bot.loop.create_task(story_mode_listener(channel_id, bridge, initial_dialogue=dialogue or ""))
                            return

                        # Block direct user messages while story mode is generating/thinking
                        if is_story and (not has_dialogue) and (not has_input):
                            await message.channel.send(BADGE_THINKING)
                            await message.channel.send("⏳ **Yuki 正在思考中**\n请稍等，暂时不能发送消息。")
                            return
                    except Exception as e:
                        print(f"[{msgChannel}] Error checking story mode: {e}")
                    
                    # Auto-forward message to bridge
                    channel_send_busy[channel_id] = True
                    try:
                        async with message.channel.typing():
                            reply = await bridge.send_message(message.content)
                    finally:
                        channel_send_busy[channel_id] = False

                    if reply:
                        print(f"[{msgChannel}] {bot.user.name}: {reply}")
                        if reply == STORY_SEND_ACK:
                            await safe_typing_pulse(message.channel)
                        else:
                            await message.channel.send(reply)
                    return
    
    # Handle bridge commands
    if message.content.startswith('$bridge'):
        if not BRIDGE_AVAILABLE:
            error_msg = "❌ Bridge system not available. Check that bridgeParser.py exists."
            print(f"[{msgChannel}] {bot.user.name}: {error_msg}")
            await message.channel.send(error_msg)
            return
        
        channel_id = str(message.channel.id)
        parse_result = parse_bridge_command(message.content, bridge_instances, channel_id)
        
        # Unpack result (diary command returns 4 items, others return 3)
        if len(parse_result) == 4:
            result_text, action_type, bridge, extra = parse_result
        else:
            result_text, action_type, bridge = parse_result
            extra = None
        
        if action_type == 'multi':
            # extra is list[(result_text, action_type, extra)]
            for sub_result_text, sub_action_type, sub_extra in (extra or []):
                await execute_bridge_action(
                    message=message,
                    msgChannel=msgChannel,
                    channel_id=channel_id,
                    action_type=sub_action_type,
                    result_text=sub_result_text,
                    bridge=bridge,
                    extra=sub_extra,
                )
        else:
            await execute_bridge_action(
                message=message,
                msgChannel=msgChannel,
                channel_id=channel_id,
                action_type=action_type,
                result_text=result_text,
                bridge=bridge,
                extra=extra,
            )
    
    # Help command
    elif message.content.startswith('$help'):
        help_text = """
**Bridge Bot Commands**

`$bridge --help` / `$bridge -h` - Show bridge command usage
`$bridge --init` / `$bridge -i` - Initialize bridge (checks for story mode automatically)
`$bridge --calibration` / `$bridge -c` - Dump DOM to game_dom.html for inspection
`$bridge --status` / `$bridge -s` - Show bridge status
`$bridge -m <message>` / `$bridge --message <message>` - Send a message through the bridge
`$bridge --go-first` / `$bridge --greet` / `$bridge --yuki-first` - Ask Yuki to start first
`$bridge --info` - Get game info (stats, date, coins, etc.)
`$bridge --diary [index]` / `$bridge -y [index]` - Get diary entry (default: 0)
`$bridge --end-chat` / `$bridge -e` - End current conversation (let game proceed)
`$bridge --continue` / `$bridge -n` - Progress story dialogue (when in story mode)
`$bridge --listen [on/off]` / `$bridge -l [on/off]` - Control listen mode (no arg = toggle)
`$bridge --disconnect` / `$bridge -d` - Disconnect and clear cache

**Default behaviors**
- Bot startup auto-tries bridge init in background until success.
- `$bridge --listen` with no mode toggles listen mode.
- `$bridge --diary` with no index defaults to latest (`0`).

**Example Usage:**
```
$bridge -i
$bridge -m Hello!
$bridge --message "Hi Yuki"
$bridge --info
$bridge --diary
$bridge --continue
$bridge --end-chat
$bridge --listen
$bridge -m "test" --calibration
```

**Story Mode:**
- After `--init`, the bot automatically checks if the game is in story mode
- If detected, dialogue is auto-forwarded and auto-progressed
- The bot only asks for your reply when story input is visible
- Story mode is also checked before sending messages

**Note:** 
- The bridge uses Chrome DevTools Protocol to directly interact with the game.
- Supports multiple flags in one command, executed in order.
- Use `-m` / `--message` to send messages, or enable `--listen on` to auto-forward all messages.
- Use `--end-chat` when done chatting to let the game continue.

For detailed setup instructions, see setup_cdp.md
        """
        print(f"[{msgChannel}] {bot.user.name}: [Help text sent]")
        await message.channel.send(help_text)


# Start the bot
if __name__ == "__main__":
    print("Starting Discord Bridge Bot...")
    bot.run(botConfig["TOKEN"])
