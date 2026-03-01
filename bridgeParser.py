"""
Bridge command parser for meiju_bridge.py
Handles commands like: $bridge send <text>, $bridge --init, etc.
"""
import shlex

from meiju_bridge import MeijuBridge


def parse_bridge_command(message_content: str, bridge_instances: dict, channel_id: str):
    """
    Parse and execute bridge commands.
    
    Args:
        message_content: Full message text starting with $bridge
        bridge_instances: Dict mapping channel_id -> BridgedObject
        channel_id: Discord channel ID (as string)
        
    Returns:
        Tuple of (response_text, action_type, bridge_object)
        action_type can be: 'send', 'init', 'disconnect', 'status', 'listen', 'help'
    """
    # Remove '$bridge' prefix
    cmd = message_content[7:].strip()  # len('$bridge') = 7
    
    # Get or create bridge instance for this channel.
    # If startup auto-init already established a connection, reuse it to avoid reconnect delay.
    if channel_id not in bridge_instances:
        auto_bridge = bridge_instances.get("__auto_init__")
        if auto_bridge and getattr(auto_bridge, "connected", False):
            bridge_instances[channel_id] = auto_bridge
        else:
            bridge_instances[channel_id] = MeijuBridge(channel_id)
    
    bridge = bridge_instances[channel_id]
    
    usage = (
        "Usage: `$bridge -m <text>` | `$bridge --message <text>` | `$bridge -i` | `$bridge -c` | "
        "`$bridge -s` | `$bridge --info` | `$bridge --diary [index]` | `$bridge --end-chat` | "
        "`$bridge --continue` | `$bridge --go-first` | `$bridge --listen [on/off]` | `$bridge --disconnect`"
    )
    detailed_help = f"""
**Bridge Command Help**

{usage}

**Commands + Aliases**
- `--help`, `-h`: Show this help
- `--init`, `-i`: Initialize bridge and detect story mode
- `--calibration`, `-c`: Dump DOM to `game_dom.html` for selector inspection
- `--status`, `-s`: Show bridge status
- `--message <text>`, `-m <text>`: Send message to Yuki
- `--go-first`, `--greet`, `--yuki-first`, `-g`: Let Yuki speak first
- `--info`: Fetch game info
- `--diary [index]`, `-y [index]`: Fetch diary entry (`0` is latest)
- `--end-chat`, `-e`: End current conversation
- `--continue`, `-n`: Continue story dialogue
- `--listen [on/off]`, `-l [on/off]`: Listen mode control
- `--disconnect`, `-d`: Disconnect bridge

**Defaults**
- `--listen` with no value toggles mode on/off.
- `--diary` with no index defaults to `0`.

**Multi-Flag Support**
- Multiple flags can be combined and run in order.
- Example: `$bridge -m "hello" --calibration`
""".strip()

    # Parse command
    if not cmd:
        return (detailed_help, 'help', bridge)

    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()

    if not tokens:
        return (detailed_help, 'help', bridge)

    aliases = {
        '-h': '--help',
        '--help': '--help',
        '-i': '--init',
        '--init': '--init',
        '-c': '--calibration',
        '--calibration': '--calibration',
        '-s': '--status',
        '--status': '--status',
        '--info': '--info',
        '-d': '--disconnect',
        '--disconnect': '--disconnect',
        '-l': '--listen',
        '--listen': '--listen',
        '--diary': '--diary',
        '-y': '--diary',
        '--end-chat': '--end-chat',
        '-e': '--end-chat',
        '--continue': '--continue',
        '-n': '--continue',
        '--go-first': '--go-first',
        '--greet': '--go-first',
        '--yuki-first': '--go-first',
        '-g': '--go-first',
        '-m': '--message',
        '--message': '--message',
    }

    message_flags = {'--message'}
    known_flags = set(aliases.keys())
    actions = []

    i = 0
    while i < len(tokens):
        token = tokens[i]

        if token not in known_flags:
            return (f"❌ Unknown command: `{token}`\n\n{detailed_help}", 'help', bridge)

        flag = aliases[token]

        if flag == '--help':
            actions.append((detailed_help, 'help', None))

        elif flag == '--init':
            actions.append(("Initializing bridge (checking for game window)...", 'init', None))

        elif flag == '--calibration':
            actions.append(("Starting calibration...\nPlease check console for instructions!", 'calibration', None))

        elif flag == '--status':
            actions.append((bridge.get_status(), 'status', None))

        elif flag == '--info':
            actions.append(("Fetching game info...", 'info', None))

        elif flag == '--diary':
            index = 0
            if i + 1 < len(tokens) and tokens[i + 1] not in known_flags:
                try:
                    index = int(tokens[i + 1])
                    i += 1
                except ValueError:
                    return ("❌ Diary index must be a number. Usage: `$bridge --diary [index]` (default 0)", 'help', bridge)
            actions.append((f"Fetching diary entry {index}...", 'diary', index))

        elif flag == '--end-chat':
            actions.append(("Ending conversation...", 'end-chat', None))

        elif flag == '--continue':
            actions.append(("Continuing story...", 'continue', None))

        elif flag == '--go-first':
            actions.append(("Asking Yuki to start the conversation...", 'greet', None))

        elif flag == '--disconnect':
            actions.append(("Disconnecting bridge...", 'disconnect', None))

        elif flag == '--listen':
            if i + 1 < len(tokens) and tokens[i + 1].lower() in ('on', 'off'):
                mode = tokens[i + 1].lower()
                i += 1
                if mode == 'on':
                    bridge.set_listen_mode(True)
                    actions.append(("✅ Listen mode enabled. All messages will be sent to the game.", 'listen', None))
                else:
                    bridge.set_listen_mode(False)
                    actions.append(("🔴 Listen mode disabled.", 'listen', None))
            else:
                # Default behavior: toggle
                new_mode = not bridge.is_listening()
                bridge.set_listen_mode(new_mode)
                if new_mode:
                    actions.append(("✅ Listen mode enabled (toggled). All messages will be sent to the game.", 'listen', None))
                else:
                    actions.append(("🔴 Listen mode disabled (toggled).", 'listen', None))

        elif flag in message_flags:
            j = i + 1
            message_parts = []
            while j < len(tokens) and tokens[j] not in known_flags:
                message_parts.append(tokens[j])
                j += 1

            text = " ".join(message_parts).strip()
            if not text:
                return ("❌ Usage: `$bridge -m <message>` or `$bridge --message <message>`", 'help', bridge)

            actions.append((text, 'send', None))
            i = j - 1

        i += 1

    if not actions:
        return (detailed_help, 'help', bridge)

    if len(actions) == 1:
        result_text, action_type, extra = actions[0]
        if extra is None:
            return (result_text, action_type, bridge)
        return (result_text, action_type, bridge, extra)

    return ("Executing multiple bridge actions...", 'multi', bridge, actions)


# Export for compatibility
__all__ = ['parse_bridge_command']
