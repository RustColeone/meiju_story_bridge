import asyncio
import json
from typing import Any, Awaitable, Callable, Dict, Optional


class BridgeSessionManager:
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
    AUTO_INIT_CHANNEL_ID = "__auto_init__"

    def __init__(
        self,
        parse_bridge_command: Callable,
        send_text: Callable[[Any, str], Awaitable[None]],
        schedule_task: Callable[[Awaitable[Any]], Any],
        get_target_by_channel_id: Callable[[str], Any],
        get_bot_name: Callable[[], str],
    ):
        self.parse_bridge_command = parse_bridge_command
        self.send_text = send_text
        self.schedule_task = schedule_task
        self.get_target_by_channel_id = get_target_by_channel_id
        self.get_bot_name = get_bot_name

        self.bridge_instances: Dict[str, Any] = {}
        self.story_listeners: Dict[str, bool] = {}
        self.channel_send_busy: Dict[str, bool] = {}
        self.channel_locks: Dict[str, asyncio.Lock] = {}
        self.channel_known_history: Dict[str, set[str]] = {}
        # Set to True by startup_auto_init_loop if story mode was detected at init time.
        # Consumed by the first handle_bridge_command call so the user gets notified.
        self.pending_story_at_init: bool = False
        self._pending_story_dialogue: Optional[str] = None
        self._pending_story_has_input: bool = False

    def _get_channel_lock(self, channel_id: str) -> asyncio.Lock:
        lock = self.channel_locks.get(channel_id)
        if lock is None:
            lock = asyncio.Lock()
            self.channel_locks[channel_id] = lock
        return lock

    async def _stop_story_listener(self, channel_id: str):
        """Signal story listener to stop and give it a short moment to exit loop."""
        if self.story_listeners.get(channel_id):
            self.story_listeners[channel_id] = False
            await asyncio.sleep(0.15)

    @staticmethod
    def _is_yuki_sender(sender: str) -> bool:
        s = (sender or "").strip().lower()
        return s.startswith("yuki")

    @staticmethod
    def _truncate_player_text(text: str, limit: int = 100) -> str:
        t = (text or "").strip()
        if len(t) <= limit:
            return t
        return t[:limit] + "..."

    @staticmethod
    def _history_key(sender: str, content: str) -> str:
        return f"{(sender or '').strip().lower()}::{(content or '').strip()}"

    async def _build_context_sync_block(self, channel_id: str, bridge: Any, current_player_text: str = "") -> str:
        """Build A/B/C sync block when recent context is not yet forwarded to platform."""
        if not hasattr(bridge, "get_recent_conversation"):
            return ""

        try:
            recent = await bridge.get_recent_conversation(limit=4)
        except Exception:
            return ""

        if not recent:
            return ""

        latest_yuki_idx = -1
        for i in range(len(recent) - 1, -1, -1):
            if self._is_yuki_sender(recent[i].get("sender", "")):
                latest_yuki_idx = i
                break

        if latest_yuki_idx <= 0:
            # No Yuki reply or nothing before Yuki reply to sync
            known = self.channel_known_history.setdefault(channel_id, set())
            for item in recent:
                known.add(self._history_key(item.get("sender", ""), item.get("content", "")))
            return ""

        context = recent[max(0, latest_yuki_idx - 3):latest_yuki_idx]
        if not context:
            return ""

        known = self.channel_known_history.setdefault(channel_id, set())
        normalized_current_player = self._truncate_player_text(current_player_text, 100)

        needs_sync = False
        for x in context:
            sender = x.get("sender", "")
            content = x.get("content", "")
            key = self._history_key(sender, content)

            # The immediate current user input (C) is expected to be new and should not
            # by itself trigger context-sync on every turn.
            if (not self._is_yuki_sender(sender)) and normalized_current_player:
                if self._truncate_player_text(content, 100) == normalized_current_player:
                    continue

            if key not in known:
                needs_sync = True
                break

        # Mark recent messages as known for future comparisons
        for item in recent:
            known.add(self._history_key(item.get("sender", ""), item.get("content", "")))

        if not needs_sync:
            return ""

        entries = []
        for item in context:
            sender = item.get("sender", "")
            content = item.get("content", "")
            role = "yuki" if self._is_yuki_sender(sender) else "player"
            rendered = (content or "").strip() if role == "yuki" else self._truncate_player_text(content, 100)
            entries.append({"role": role, "text": rendered})

        lines = []
        for item in entries:
            role = item.get("role", "player")
            text = item.get("text", "")
            lines.append(f'"{role}": {json.dumps(text, ensure_ascii=False)}')

        return "```json\n" + "\n".join(lines) + "\n```"

    async def safe_typing_pulse(self, target: Any, seconds: float = 0.8):
        try:
            async with target.typing():
                await asyncio.sleep(seconds)
        except Exception:
            pass

    async def story_mode_listener(self, channel_id: str, bridge: Any, initial_dialogue: str = ""):
        import time

        target = self.get_target_by_channel_id(channel_id)
        if not target:
            self.story_listeners[channel_id] = False
            return

        last_dialogue = (initial_dialogue or "").strip()
        pending_auto_continue = bool(last_dialogue)
        story_end_confirm_seconds = 5
        story_ended_time = None
        last_state = None
        last_state_badge = None

        print(f"[Story Listener] Started for channel {channel_id}")

        try:
            while self.story_listeners.get(channel_id) and bridge.connected:
                await asyncio.sleep(1.0)

                try:
                    is_story, dialogue, has_dialogue, has_input = await bridge.check_story_mode()

                    if has_dialogue:
                        current_state = "dialogue"
                    elif has_input:
                        current_state = "input"
                    else:
                        current_state = "generating"

                    if current_state != last_state:
                        if current_state == "dialogue":
                            print("[Story Listener] State: Game showing dialogue")
                        elif current_state == "input":
                            print("[Story Listener] State: Waiting for user input")
                        elif current_state == "generating":
                            print("[Story Listener] State: Yuki generating/thinking")
                        last_state = current_state

                    state_to_badge = {
                        "dialogue": self.BADGE_DIALOGUE,
                        "input": self.BADGE_INPUT,
                        "generating": self.BADGE_THINKING,
                    }
                    badge = state_to_badge.get(current_state)
                    if badge and badge != last_state_badge:
                        await self.send_text(target, badge)
                        last_state_badge = badge
                        if current_state == "generating":
                            await self.safe_typing_pulse(target)

                    if has_dialogue:
                        current_dialogue = (dialogue or "").strip()

                        if pending_auto_continue and current_dialogue and current_dialogue == last_dialogue:
                            continue_result = await bridge.story_continue()
                            if not continue_result.startswith("✅"):
                                print(f"[Story Listener] Auto-continue failed: {continue_result}")
                            pending_auto_continue = False
                            continue

                        if current_dialogue and current_dialogue != last_dialogue:
                            await self.send_text(target, current_dialogue)
                            last_dialogue = current_dialogue
                            pending_auto_continue = False

                            continue_result = await bridge.story_continue()
                            if not continue_result.startswith("✅"):
                                print(f"[Story Listener] Auto-continue failed: {continue_result}")

                    if not is_story:
                        if story_ended_time is None:
                            story_ended_time = time.time()
                            print(
                                f"[Story Listener] Story mode ended, waiting {story_end_confirm_seconds} seconds to confirm..."
                            )
                        elif time.time() - story_ended_time >= story_end_confirm_seconds:
                            end_result = await bridge.end_conversation()
                            await self.send_text(target, self.BADGE_ENDED)
                            await self.send_text(target, f"📖 **Story Mode Ended** - Normal chat resumed\n{end_result}")
                            self.story_listeners[channel_id] = False
                            break
                    else:
                        if story_ended_time is not None:
                            print("[Story Listener] Story mode resumed, canceling end timer")
                            story_ended_time = None

                except Exception as e:
                    print(f"[Story Listener] Error for channel {channel_id}: {e}")
                    await asyncio.sleep(1)

        except Exception as e:
            print(f"[Story Listener] Fatal error: {e}")
        finally:
            self.story_listeners[channel_id] = False

    async def startup_auto_init_loop(self):
        await asyncio.sleep(1)
        while True:
            try:
                parse_result = self.parse_bridge_command(
                    "$bridge --init", self.bridge_instances, self.AUTO_INIT_CHANNEL_ID
                )
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
                    # Check if game is already in story mode so the first channel
                    # to interact gets notified immediately.
                    try:
                        is_story, dialogue, _, has_input = await bridge.check_story_mode()
                        if is_story:
                            self.pending_story_at_init = True
                            self._pending_story_dialogue = dialogue
                            self._pending_story_has_input = has_input
                            print("[Auto Init] ⚠️ Game is in story mode — will notify first active channel")
                    except Exception as e:
                        print(f"[Auto Init] Could not check story mode: {e}")
                    return

                print("[Auto Init] ❌ Init failed, retrying in 10s...")
            except Exception as e:
                print(f"[Auto Init] Error: {e}")

            await asyncio.sleep(10)

    async def _process_send_message(self, target: Any, msg_channel: str, channel_id: str, bridge: Any, text: str):
        if self.channel_send_busy.get(channel_id, False):
            await self.send_text(target, self.WAIT_FOR_GAME_REPLY)
            return

        try:
            is_story, dialogue, has_dialogue, has_input = await bridge.check_story_mode()
            if is_story and not self.story_listeners.get(channel_id):
                await self.send_text(target, self.BADGE_STORY)
                story_msg = "📖 **Story Mode Detected!**\nAuto-forwarding dialogue...\n\n"
                if dialogue:
                    story_msg += dialogue
                elif has_input:
                    story_msg += "💬 Waiting for your response input..."
                else:
                    story_msg += "⏳ Yuki is thinking..."
                story_msg += "\n\n" + self.STORY_INPUT_HELP
                print(f"[{msg_channel}] {self.get_bot_name()}: [Story mode detected]")
                await self.send_text(target, story_msg)
                self.story_listeners[channel_id] = True
                self.schedule_task(self.story_mode_listener(channel_id, bridge, initial_dialogue=dialogue or ""))
                return

            if is_story and (not has_dialogue) and (not has_input):
                await self.send_text(target, self.BADGE_THINKING)
                await self.send_text(target, "⏳ **Yuki 正在思考中**\n请稍等，暂时不能发送消息。")
                return
        except Exception as e:
            print(f"[{msg_channel}] Error checking story mode: {e}")

        self.channel_send_busy[channel_id] = True
        try:
            async with target.typing():
                reply = await bridge.send_message(text)
        finally:
            self.channel_send_busy[channel_id] = False

        if reply:
            print(f"[{msg_channel}] {self.get_bot_name()}: {reply}")
            if reply == self.STORY_SEND_ACK:
                await self.safe_typing_pulse(target)
            else:
                sync_block = await self._build_context_sync_block(
                    channel_id,
                    bridge,
                    current_player_text=text,
                )
                if sync_block:
                    await self.send_text(target, f"{sync_block}\n{reply}")
                else:
                    await self.send_text(target, reply)
        else:
            error_msg = "❌ No reply received"
            print(f"[{msg_channel}] {self.get_bot_name()}: {error_msg}")
            await self.send_text(target, error_msg)

    async def handle_listen_mode_message(self, target: Any, msg_channel: str, channel_id: str, content: str):
        async with self._get_channel_lock(channel_id):
            bridge = self.bridge_instances.get(channel_id)
            if not bridge or not bridge.is_listening():
                return False

            await self._process_send_message(target, msg_channel, channel_id, bridge, content)
            return True

    async def execute_bridge_action(
        self,
        target: Any,
        msg_channel: str,
        channel_id: str,
        action_type: str,
        result_text: str,
        bridge: Any,
        extra: Any = None,
    ):
        if action_type in ["help", "status", "listen"]:
            print(f"[{msg_channel}] {self.get_bot_name()}: {result_text}")
            await self.send_text(target, result_text)

        elif action_type == "info":
            print(f"[{msg_channel}] {self.get_bot_name()}: {result_text}")
            await self.send_text(target, result_text)
            info = await bridge.get_game_info()
            if info:
                print(f"[{msg_channel}] {self.get_bot_name()}: [Game info sent]")
                await self.send_text(target, info)
            else:
                error_msg = "❌ Failed to retrieve game info"
                print(f"[{msg_channel}] {self.get_bot_name()}: {error_msg}")
                await self.send_text(target, error_msg)

        elif action_type == "diary":
            await self._stop_story_listener(channel_id)
            print(f"[{msg_channel}] {self.get_bot_name()}: {result_text}")
            await self.send_text(target, result_text)
            diary = await bridge.get_diary_entry(extra)
            if diary:
                print(f"[{msg_channel}] {self.get_bot_name()}: [Diary entry sent]")
                await self.send_text(target, diary)
            else:
                error_msg = "❌ Failed to retrieve diary entry"
                print(f"[{msg_channel}] {self.get_bot_name()}: {error_msg}")
                await self.send_text(target, error_msg)

        elif action_type == "init":
            print(f"[{msg_channel}] {self.get_bot_name()}: {result_text}")
            await self.send_text(target, result_text)
            success = await bridge.initialize()

            if hasattr(bridge, "last_status_message") and bridge.last_status_message:
                print(f"[{msg_channel}] {self.get_bot_name()}: {bridge.last_status_message}")
                await self.send_text(target, bridge.last_status_message)

            if success:
                success_msg = "✅ Bridge ready to use!"
                print(f"[{msg_channel}] {self.get_bot_name()}: {success_msg}")
                await self.send_text(target, success_msg)

                try:
                    is_story, dialogue, has_dialogue, has_input = await bridge.check_story_mode()
                    if is_story:
                        await self.send_text(target, self.BADGE_STORY)
                        story_msg = "📖 **Story Mode Detected!**\nAuto-forwarding dialogue...\n\n"
                        if dialogue:
                            story_msg += dialogue
                        elif has_input:
                            story_msg += "💬 Waiting for your response input..."
                        else:
                            story_msg += "⏳ Yuki is thinking..."
                        story_msg += "\n\n" + self.STORY_INPUT_HELP
                        print(f"[{msg_channel}] {self.get_bot_name()}: [Story mode detected]")
                        await self.send_text(target, story_msg)
                        self.story_listeners[channel_id] = True
                        self.schedule_task(
                            self.story_mode_listener(channel_id, bridge, initial_dialogue=dialogue or "")
                        )
                except Exception as e:
                    print(f"[{msg_channel}] Error checking story mode: {e}")
            else:
                error_msg = "❌ Game window not found. Make sure the game is running."
                print(f"[{msg_channel}] {self.get_bot_name()}: {error_msg}")
                await self.send_text(target, error_msg)

        elif action_type == "calibration":
            print(f"[{msg_channel}] {self.get_bot_name()}: {result_text}")
            await self.send_text(target, result_text)
            success = await bridge.calibrate()
            if success:
                success_msg = "✅ Calibration complete! Check console for new ratios."
                print(f"[{msg_channel}] {self.get_bot_name()}: {success_msg}")
                await self.send_text(target, success_msg)
            else:
                error_msg = "❌ Calibration failed or timed out"
                print(f"[{msg_channel}] {self.get_bot_name()}: {error_msg}")
                await self.send_text(target, error_msg)

        elif action_type == "end-chat":
            await self._stop_story_listener(channel_id)
            print(f"[{msg_channel}] {self.get_bot_name()}: {result_text}")
            await self.send_text(target, result_text)
            result = await bridge.end_conversation()
            print(f"[{msg_channel}] {self.get_bot_name()}: {result}")
            await self.send_text(target, result)

        elif action_type == "continue":
            print(f"[{msg_channel}] {self.get_bot_name()}: {result_text}")
            await self.send_text(target, result_text)

            # Check if game is ALREADY in story mode (e.g. launched into a story scene).
            # In that case, skip calling story_continue() and just start the listener.
            try:
                is_story, dialogue, has_dialogue, has_input = await bridge.check_story_mode()
            except Exception as e:
                print(f"[{msg_channel}] Error checking story mode for continue: {e}")
                is_story, dialogue, has_dialogue, has_input = False, None, False, False

            if is_story and not self.story_listeners.get(channel_id):
                # Game is already mid-story — start listener immediately without clicking
                story_msg = "📖 **Game is already in Story Mode!**\nStarting auto-forwarding...\n\n"
                if dialogue:
                    story_msg += dialogue
                elif has_input:
                    story_msg += "💬 Waiting for your response input..."
                else:
                    story_msg += "⏳ Yuki is thinking..."
                story_msg += "\n\n" + self.STORY_INPUT_HELP
                print(f"[{msg_channel}] Story mode already active — starting listener")
                await self.send_text(target, story_msg)
                self.story_listeners[channel_id] = True
                self.schedule_task(self.story_mode_listener(channel_id, bridge, initial_dialogue=dialogue or ""))
            elif is_story:
                # Listener already running, nothing to do
                await self.send_text(target, "📖 Story listener is already active.")
            else:
                # Not in story mode — execute one continue step
                result = await bridge.story_continue()
                print(f"[{msg_channel}] {self.get_bot_name()}: {result}")
                await self.send_text(target, result)

                # After the continue step, check if we entered story mode
                try:
                    is_story, dialogue, has_dialogue, has_input = await bridge.check_story_mode()
                    if is_story and not self.story_listeners.get(channel_id):
                        self.story_listeners[channel_id] = True
                        self.schedule_task(
                            self.story_mode_listener(channel_id, bridge, initial_dialogue=dialogue or "")
                        )
                except Exception as e:
                    print(f"[{msg_channel}] Error checking story mode after continue: {e}")

        elif action_type == "greet":
            print(f"[{msg_channel}] {self.get_bot_name()}: {result_text}")
            await self.send_text(target, result_text)

            if self.channel_send_busy.get(channel_id, False):
                await self.send_text(target, self.WAIT_FOR_GAME_REPLY)
                return

            try:
                is_story, _, _, _ = await bridge.check_story_mode()
                if is_story:
                    blocked_msg = "❌ Not available in story mode. Finish story mode first."
                    print(f"[{msg_channel}] {self.get_bot_name()}: {blocked_msg}")
                    await self.send_text(target, blocked_msg)
                    return
            except Exception as e:
                print(f"[{msg_channel}] Error checking story mode for greet: {e}")

            self.channel_send_busy[channel_id] = True
            try:
                async with target.typing():
                    result = await bridge.trigger_greeting()
            finally:
                self.channel_send_busy[channel_id] = False

            if result:
                print(f"[{msg_channel}] {self.get_bot_name()}: {result}")
                await self.send_text(target, result)
            else:
                error_msg = "❌ No reply received"
                print(f"[{msg_channel}] {self.get_bot_name()}: {error_msg}")
                await self.send_text(target, error_msg)

        elif action_type == "disconnect":
            print(f"[{msg_channel}] {self.get_bot_name()}: {result_text}")
            await self.send_text(target, result_text)
            success = await bridge.disconnect()
            if success:
                if channel_id in self.bridge_instances:
                    del self.bridge_instances[channel_id]
                success_msg = "✅ Bridge disconnected"
                print(f"[{msg_channel}] {self.get_bot_name()}: {success_msg}")
                await self.send_text(target, success_msg)
            else:
                error_msg = "❌ Failed to disconnect"
                print(f"[{msg_channel}] {self.get_bot_name()}: {error_msg}")
                await self.send_text(target, error_msg)

        elif action_type == "send":
            await self._process_send_message(target, msg_channel, channel_id, bridge, result_text)

        else:
            unknown_msg = f"❌ Unsupported action: {action_type}"
            print(f"[{msg_channel}] {self.get_bot_name()}: {unknown_msg}")
            await self.send_text(target, unknown_msg)

    async def _check_pending_story_at_init(self, target: Any, msg_channel: str, channel_id: str, bridge: Any):
        """If game was in story mode at startup, notify channel and start listener on first command."""
        if not self.pending_story_at_init:
            return
        if self.story_listeners.get(channel_id):
            self.pending_story_at_init = False  # Already handled for another channel
            return
        if not (bridge and getattr(bridge, "connected", False)):
            return

        self.pending_story_at_init = False
        dialogue = self._pending_story_dialogue
        has_input = self._pending_story_has_input

        # Re-check in case state changed since init
        try:
            is_story, dialogue, _, has_input = await bridge.check_story_mode()
        except Exception:
            is_story = True  # Assume still in story mode if check fails

        if not is_story:
            return

        story_msg = (
            "⚠️ **Game was already in Story Mode when the bot started!**\n"
            "Auto-forwarding dialogue...\n\n"
        )
        if dialogue:
            story_msg += dialogue
        elif has_input:
            story_msg += "💬 Waiting for your response input..."
        else:
            story_msg += "⏳ Yuki is thinking..."
        story_msg += "\n\n" + self.STORY_INPUT_HELP

        print(f"[{msg_channel}] Notifying channel: game was in story mode at startup")
        await self.send_text(target, story_msg)
        self.story_listeners[channel_id] = True
        self.schedule_task(self.story_mode_listener(channel_id, bridge, initial_dialogue=dialogue or ""))

    async def handle_bridge_command(self, target: Any, msg_channel: str, channel_id: str, content: str):
        async with self._get_channel_lock(channel_id):
            parse_result = self.parse_bridge_command(content, self.bridge_instances, channel_id)

            if len(parse_result) == 4:
                result_text, action_type, bridge, extra = parse_result
            else:
                result_text, action_type, bridge = parse_result
                extra = None

            # Proactively notify if game was in story mode when the bot first started.
            # Runs on the very first command from this channel.
            await self._check_pending_story_at_init(target, msg_channel, channel_id, bridge)

            if action_type == "multi":
                for sub_result_text, sub_action_type, sub_extra in (extra or []):
                    await self.execute_bridge_action(
                        target=target,
                        msg_channel=msg_channel,
                        channel_id=channel_id,
                        action_type=sub_action_type,
                        result_text=sub_result_text,
                        bridge=bridge,
                        extra=sub_extra,
                    )
            else:
                await self.execute_bridge_action(
                    target=target,
                    msg_channel=msg_channel,
                    channel_id=channel_id,
                    action_type=action_type,
                    result_text=result_text,
                    bridge=bridge,
                    extra=extra,
                )

    def get_help_text(self) -> str:
        return """
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
