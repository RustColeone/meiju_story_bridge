# MeijuHub API (Black-Box Reference)

This document describes `MeijuBridge` from `meiju_hub.py` as a **single-file library interface**.
Treat the implementation as a black box: call methods, read returns, and orchestrate platform logic (Discord/Telegram/etc.) around it.

---

## 1) Runtime requirements

- Python 3.10+
- Packages:
  - `pychrome`
  - `requests`
  - `psutil` (optional, used for CDP port auto-discovery)
- Game launched with CDP enabled (recommended):
  - `--remote-debugging-port=9222 --user-data-dir=<custom_folder>`

Environment variables recognized:

- `MEIJU_CDP_PORT`: override CDP port probe target
- `MEIJU_POLL_TIMEOUT`: timeout (seconds) used by reply wait logic

---

## 2) Construction and state

## Constructor

```python
bridge = MeijuBridge(channel_id: str)
```

Input:
- `channel_id: str` — host-platform identifier (can be any string)

Important runtime fields (read-only by convention):
- `bridge.connected: bool`
- `bridge.last_status_message: str`
- `bridge.story_mode: bool` (tracked internally)

---

## 3) Public API: inputs and returns

## `await initialize() -> bool`

Purpose:
- Connect to game tab through Chrome DevTools Protocol.

Returns:
- `True` on successful connection
- `False` on failure

Side effects:
- Updates `connected`
- Updates `last_status_message`

---

## `await disconnect() -> bool`

Purpose:
- Stop tab session and mark bridge disconnected.

Returns:
- Always `True` (best-effort stop)

---

## `get_status() -> str`

Purpose:
- Human-readable status summary.

Returns:
- Markdown-like status string containing:
  - connected state
  - configured CDP default endpoint
  - currently active CDP endpoint (resolved/probed)
  - listen mode state

---

## `await send_message(message: str) -> Optional[str]`

Purpose:
- Send text to game via appropriate input path.

Input:
- `message: str`

Behavior:
- If not connected: auto-calls `initialize()`
- If story mode active: sends via story input (`#story-player-input`)
- Otherwise: sends via normal chat input and waits for Yuki reply

Returns (string contracts):
- Success in story input path:
  - `"✅ Message sent in story mode. Waiting for story to continue..."`
- Success in normal chat path:
  - last parsed Yuki reply text
- Timeout in normal chat path:
  - `"⏱️ No reply received (timeout)"`
- Error cases:
  - prefixed with `"❌ "` (e.g., not connected, input/send errors, exceptions)

Note:
- Method returns `str` in current implementation; annotation is `Optional[str]` for compatibility.

Concurrency note (important for adapters):
- Treat `send_message` as **single-flight** per chat/channel.
- Do not send another user input until current call finishes.
- If your platform receives another user message while waiting, reply with a wait notice, e.g.:
  - `"⏳ Game is still responding\nPlease wait for game response and try again."`

---

## `await trigger_greeting() -> Optional[str]`

Purpose:
- Trigger the persistent greeting button (`#persistent-greeting-btn`) so Yuki speaks first.

Behavior:
- If not connected: auto-calls `initialize()`
- Clicks greeting button and waits for Yuki reply using same wait pipeline as normal chat

Returns:
- Success: parsed Yuki reply text
- Timeout: `"⏱️ No reply received (timeout)"`
- Error: `"❌ ..."`

Usage rule:
- Intended for normal chat mode (not story mode).
- Adapter should block it while story mode is active.

---

## `await check_story_mode() -> tuple[bool, Optional[str], bool, bool]`

Purpose:
- Read story-mode state from DOM.

Returns tuple:
1. `is_story_mode: bool`
2. `dialogue_text: Optional[str]`
3. `has_dialogue: bool`
4. `has_input: bool`

Detection rules:
- Story mode is considered active if **any** is visible:
  - `#dialogue-box`
  - `#story-player-input`
  - `#story-waiting-message` (e.g., "Yuki正在思考中...")

Interpretation:
- `has_dialogue=True`  => game is showing dialogue (auto-continue phase)
- `has_input=True`     => game is waiting for user text
- both false while `is_story_mode=True` => generating/thinking transition

---

## `await story_continue() -> str`

Purpose:
- Click story continue button (`#dialogue-choices button.choice-btn`).

Returns:
- Success: `"✅ Continued story"`
- Error: `"❌ ..."`

---

## `await end_conversation() -> str`

Purpose:
- Click end chat button (`#end-chat-btn`) to close active conversation.

Returns:
- Success: `"✅ Conversation ended. Game can now proceed."`
- Info/no active conversation: `"ℹ️ No active conversation to end."`
- Error: `"❌ ..."`

---

## `await get_game_info() -> Optional[str]`

Purpose:
- Read game info fields (`time/date/city/day/coins`) from DOM.

Returns:
- Formatted info block on success
- `"📋 No info available"` when empty
- Error string prefixed with `"❌ "`

---

## `await get_diary_entry(index: int) -> str`

Purpose:
- Open diary modal, read one entry by index, then close diary.

Input:
- `index: int` (`0` = latest)

Returns:
- Formatted diary content block
- Error string prefixed with `"❌ "`

---

## `await calibrate() -> bool`

Purpose:
- Dump `document.body.innerHTML` to local `game_dom.html` for selector inspection.

Returns:
- `True` on success
- `False` on failure

---

## `await get_recent_conversation(limit: int = 4) -> list[dict[str, str]]`

Purpose:
- Read the most recent messages from the visible chat history DOM.

Input:
- `limit: int` — max number of recent messages to return (default `4`)

Returns:
- List of `{"sender": str, "content": str}` dicts, oldest-first, up to `limit` entries
- Empty list `[]` if not connected, no DOM messages, or on error

Sender values (by convention):
- `"Yuki"` (or variant) for game character dialogue
- Player/user text for human messages

Usage note:
- Intended for platform adapters to detect and sync unseen context before forwarding a new reply.
- Empty entries (blank content) are filtered out automatically.

---

## 4) Suggested host-platform orchestration (Telegram/others)

`MeijuBridge` is pull-based. Host code should implement an adapter loop.

Minimal pattern:

1. `await bridge.initialize()`
2. Poll `check_story_mode()` every ~1s
3. If `has_dialogue` and dialogue changed:
   - forward dialogue to platform
   - call `await bridge.story_continue()`
4. If `has_input`:
   - allow user text -> `await bridge.send_message(user_text)`
5. If `is_story_mode` and not (`has_dialogue` or `has_input`):
   - show "thinking" state and block user input
6. In normal chat mode, enforce one in-flight request per channel:
  - while waiting for `send_message(...)` or `trigger_greeting()` result, block new user input
7. Optional command/button: let Yuki speak first:
  - call `await bridge.trigger_greeting()` only when not in story mode
8. If story inactive for your threshold:
   - `await bridge.end_conversation()` and stop listener

---

## 5) Error-handling contract

For user-facing methods returning `str`, failures are represented as human-readable text, usually prefixed with:

- `❌` hard error
- `ℹ️` informative non-error
- `⏱️` timeout

Recommended adapter behavior:
- Do not parse exception internals
- Route return string directly to platform message/logs
- Treat `startswith("❌")` as failure branch

---

## 6) DOM assumptions used by the black box

Core selectors expected by current implementation:

- Story: `#dialogue-box`, `#dialogue-text`, `#dialogue-choices button.choice-btn`, `#story-player-input`, `#story-waiting-message`
- Chat: `#chat-panel-input`, `#chat-panel-send-btn`, `#persistent-input`, `#persistent-send-btn`, `#chat-history-area`
- End: `#end-chat-btn`
- Diary: `#diary-btn`, `.diary-entry[data-entry-index="..."]`, `#diary-back-btn`
- Info: `#current-time`, `#current-date`, `#current-city`, `#current-day`, `#current-coins`

If game UI updates break behavior, run `await calibrate()` and diff selectors.

---

## 7) Minimal adapter snippet

```python
import asyncio
from meiju_hub import MeijuBridge

async def run_bridge_loop(platform_send, platform_get_user_text):
    bridge = MeijuBridge("telegram-chat-123")
    ok = await bridge.initialize()
    if not ok:
        await platform_send(bridge.last_status_message or "Failed to initialize bridge")
        return

    last_dialogue = None
    busy = False

    while True:
        is_story, dialogue, has_dialogue, has_input = await bridge.check_story_mode()

        if is_story and has_dialogue and dialogue and dialogue != last_dialogue:
            await platform_send(dialogue)
            await bridge.story_continue()
            last_dialogue = dialogue

        elif is_story and has_input:
          if busy:
            await asyncio.sleep(1.0)
            continue
            user_text = await platform_get_user_text(timeout=1.0)
            if user_text:
            busy = True
            result = await bridge.send_message(user_text)
            busy = False
                if result:
                    await platform_send(result)

        elif is_story and (not has_dialogue) and (not has_input):
            # thinking/generating phase
            pass

        else:
          # normal mode example (optional): one-at-a-time send
          if not busy:
            user_text = await platform_get_user_text(timeout=0.2)
            if user_text:
              busy = True
              result = await bridge.send_message(user_text)
              busy = False
              if result:
                await platform_send(result)

        await asyncio.sleep(1.0)
```

---

This file is intentionally focused on **input/output contracts** so platform adapters can be implemented without reading internal bridge logic.

---

## 8) Command-layer behavior (bridgeParser / session_manager / main_discord)

If you use the bundled command layer (`$bridge ...`) on top of `MeijuBridge`, current behavior includes:

- Aliases:
  - `-i` = `--init`
  - `-c` = `--calibration`
  - `-s` = `--status`
  - `-m` = `--message`
  - `-l` = `--listen`
  - `-y` = `--diary`
  - `-e` = `--end-chat`
  - `-n` = `--continue`
  - `-d` = `--disconnect`
  - `-h` = `--help`

- Defaults:
  - `--listen` without `on/off` toggles current listen mode
  - `--diary` without index defaults to `0`
  - Bot startup performs background auto-init attempts until successful

- Multi-flag execution:
  - Multiple flags can be combined in one command and run in order.
  - Example: `$bridge -m "hello" --calibration`

- Story mode at startup:
  - After `--init` succeeds, `check_story_mode()` is called automatically.
  - If the game launched into story mode, a `pending_story_at_init` flag is set.
  - The **first `$bridge` command** from any channel (regardless of type) triggers an
    instant notification to that channel and auto-starts the story listener.
  - This prevents users from accidentally injecting text into the game while it is
    mid-story without any visible warning.

- `--continue` safety:
  - If the game is already in story mode when `--continue` is issued, the story
    listener is started immediately without clicking the continue button
    (avoids double-advancing narrative).
  - If calling `story_continue()` causes a story mode transition, the listener
    is auto-started after the call completes.
