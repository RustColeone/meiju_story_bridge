"""
Meiju Bridge - CDP Version
Uses Chrome DevTools Protocol to interact with Electron-based game
No GUI automation needed - directly manipulates DOM
"""
import time
import asyncio
from typing import Optional
import re
import requests
import json
import os

try:
    import pychrome
except ImportError:
    print("⚠️ pychrome not installed. Run: pip install pychrome")
    pychrome = None

from bridgeBase import BridgedObject

try:
    import psutil
except ImportError:
    psutil = None


class MeijuBridge(BridgedObject):
    """
    Bridge for 妹居物语 using Chrome DevTools Protocol.
    
    Connects to the Electron app via CDP and manipulates the DOM directly.
    Much more reliable than GUI automation.
    
    Prerequisites:
    - Game must be launched with: --remote-debugging-port=9222
    """
    
    # ====== Configuration ======
    CDP_HOST = "localhost"
    CDP_PORT = 9222
    POLL_INTERVAL = 0.5
    POLL_TIMEOUT = 45
    STABLE_ROUNDS = 3
    # ===========================
    
    def __init__(self, channel_id: str):
        super().__init__(channel_id)
        self.browser = None
        self.tab = None
        self.connected = False
        self.active_cdp_port: Optional[int] = None
        self.last_status_message = ""  # Store status messages for user
        self.story_mode = False  # Track if currently in story mode
        self.last_story_check = 0  # Timestamp of last story mode check
        self.poll_timeout = float(os.getenv("MEIJU_POLL_TIMEOUT", str(self.POLL_TIMEOUT)))
        
        if pychrome is None:
            print("[MeijuBridge] ERROR: pychrome not installed")

    def _probe_cdp_version(self, port: int, timeout: float = 1.0) -> Optional[dict]:
        """Return /json/version payload if a valid CDP endpoint exists on this port."""
        try:
            url = f"http://{self.CDP_HOST}:{port}/json/version"
            response = requests.get(url, timeout=timeout)
            if response.status_code != 200:
                return None
            data = response.json()
            if isinstance(data, dict) and data.get('webSocketDebuggerUrl'):
                return data
            return None
        except Exception:
            return None

    def _discover_cdp_port(self) -> Optional[int]:
        """
        Discover CDP port when fixed port (9222) is unavailable.

        Newer Electron/Chromium builds may ignore/fallback from a fixed port and
        bind CDP to a random localhost port.
        """
        if psutil is None:
            return None

        try:
            # Find candidate game processes
            pids = []
            for proc in psutil.process_iter(attrs=['pid', 'name', 'cmdline']):
                try:
                    name = (proc.info.get('name') or '').lower()
                    cmdline = ' '.join(proc.info.get('cmdline') or []).lower()
                    if (
                        '妹居物语' in name
                        or 'meijustory' in name
                        or 'urban-friendship-story' in cmdline
                        or 'meijustory' in cmdline
                    ):
                        pids.append(proc.info['pid'])
                except Exception:
                    continue

            if not pids:
                return None

            # Probe listening ports owned by candidate process(es)
            checked_ports = set()
            for pid in pids:
                try:
                    proc = psutil.Process(pid)
                    for conn in proc.connections(kind='inet'):
                        if conn.status != psutil.CONN_LISTEN or not conn.laddr:
                            continue

                        host = (conn.laddr.ip or '').strip()
                        port = conn.laddr.port

                        if host not in ('127.0.0.1', '::1', '0.0.0.0', '::'):
                            continue
                        if port in checked_ports:
                            continue

                        checked_ports.add(port)
                        payload = self._probe_cdp_version(port, timeout=0.7)
                        if payload:
                            ua = (payload.get('User-Agent') or '').lower()
                            browser = (payload.get('Browser') or '').lower()
                            if 'electron' in ua or 'chrome' in browser:
                                return port
                except Exception:
                    continue

            return None
        except Exception:
            return None

    def _resolve_cdp_port(self) -> Optional[int]:
        """Resolve usable CDP port with fallback discovery."""
        # 1) Environment override
        env_port = os.getenv('MEIJU_CDP_PORT')
        if env_port and env_port.isdigit():
            port = int(env_port)
            if self._probe_cdp_version(port):
                return port

        # 2) Configured default
        if self._probe_cdp_version(self.CDP_PORT):
            return self.CDP_PORT

        # 3) Auto-discovery (random ports after updates)
        discovered = self._discover_cdp_port()
        if discovered:
            return discovered

        return None
    
    
    async def initialize(self) -> bool:
        """
        Initialize CDP connection to the game.
        
        Returns:
            True if connected successfully
        """
        try:
            resolved_port = self._resolve_cdp_port()
            if not resolved_port:
                print("[MeijuBridge] ❌ CDP endpoint not found")
                self.active_cdp_port = None
                self.last_status_message = (
                    "❌ CDP endpoint not found.\n"
                    "Try launching game with:\n"
                    "`--remote-debugging-port=9222 --user-data-dir=<custom_folder>`"
                )
                return False

            self.active_cdp_port = resolved_port

            if resolved_port != self.CDP_PORT:
                print(f"[MeijuBridge] ℹ️ Using discovered CDP port: {resolved_port}")

            # Get tab list directly via HTTP (avoids GenericAttr issues)
            url = f"http://{self.CDP_HOST}:{resolved_port}/json"
            print(f"[MeijuBridge] Fetching tab list from {url}...")
            
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            tabs_data = response.json()
            
            if not tabs_data:
                print("[MeijuBridge] ❌ No tabs found. Is the game running with --remote-debugging-port=9222?")
                self.last_status_message = "❌ No tabs found"
                return False
            
            print(f"[MeijuBridge] Found {len(tabs_data)} tab(s)")
            
            # Find the game tab (first non-DevTools tab)
            game_tab_data = None
            for tab_info in tabs_data:
                title = tab_info.get('title', '')
                tab_url = tab_info.get('url', '')
                print(f"[MeijuBridge]   - Tab: {title} ({tab_url})")
                
                # Skip DevTools tabs
                if 'devtools' not in tab_url.lower():
                    game_tab_data = tab_info
                    break
            
            if not game_tab_data:
                game_tab_data = tabs_data[0]
            
            # Get WebSocket URL
            ws_url = game_tab_data.get('webSocketDebuggerUrl')
            if not ws_url:
                print("[MeijuBridge] ❌ No WebSocket URL available")
                self.last_status_message = "❌ No WebSocket URL available"
                return False
            
            title = game_tab_data.get('title', 'Unknown')
            print(f"[MeijuBridge] Connecting to: {title}")
            self.last_status_message = f"🔗 Connecting to: **{title}** (port {resolved_port})"
            
            # Create tab object from existing game tab data
            self.tab = pychrome.Tab(**game_tab_data)
            
            # Start the tab connection
            self.tab.start()
            
            self.connected = True
            print("[MeijuBridge] ✅ Connected!")
            self.last_status_message = f"✅ Connected to **{title}** on port **{resolved_port}**!"
            return True
            
        except Exception as e:
            import traceback
            print(f"[MeijuBridge] ❌ Connection failed: {e}")
            print(traceback.format_exc())
            print("[MeijuBridge] Make sure game is running with: --remote-debugging-port=9222 --user-data-dir=<custom_folder>")
            self.active_cdp_port = None
            return False
    
    async def calibrate(self) -> bool:
        """
        CDP version doesn't need calibration.
        This method inspects the DOM to help you find selectors.
        """
        if not self.connected:
            if not await self.initialize():
                return False
        
        print("[MeijuBridge] 🔍 Inspecting DOM structure...")
        
        try:
            # Get page HTML
            result = self.tab.Runtime.evaluate(expression="document.body.innerHTML")
            html = result.get('result', {}).get('value', '')
            
            # Save to file for inspection
            with open('game_dom.html', 'w', encoding='utf-8') as f:
                f.write(html)
            
            print("[MeijuBridge] ✅ DOM saved to game_dom.html")
            print("[MeijuBridge] Inspect the file to find CSS selectors for:")
            print("  - Chat history container")
            print("  - Input box")
            print("  - Send button")
            return True
            
        except Exception as e:
            print(f"[MeijuBridge] ❌ Failed to inspect DOM: {e}")
            return False
    
    async def _dismiss_modals(self) -> None:
        """
        Dismiss any popup modals (like diary notifications).
        Simulates human clicking "确定" button.
        Only clicks if the modal is actually visible.
        """
        try:
            js_code = """
            (function() {
                // Check for event modal close button
                let closeBtn = document.querySelector('#event-close-btn');
                if (!closeBtn) return 'NONE';
                
                // Check if button is visible
                const style = window.getComputedStyle(closeBtn);
                if (style.display === 'none' || 
                    style.visibility === 'hidden' ||
                    closeBtn.offsetWidth === 0 ||
                    closeBtn.offsetHeight === 0) {
                    return 'HIDDEN';
                }
                
                closeBtn.click();
                return 'DISMISSED';
            })();
            """
            result = self.tab.Runtime.evaluate(expression=js_code)
            status = result.get('result', {}).get('value', '')
            if status == 'DISMISSED':
                print("[MeijuBridge] 📋 Auto-dismissed modal popup")
                await asyncio.sleep(0.3)  # Brief delay after dismissal
        except Exception as e:
            pass  # Silent fail - modal might not be present
    
    async def end_conversation(self) -> str:
        """
        End the current conversation by clicking the end chat button.
        This allows the game to proceed.
        
        Returns:
            Status message
        """
        if not self.connected:
            if not await self.initialize():
                return "❌ Not connected to game"
        
        try:
            js_code = """
            (function() {
                let endBtn = document.querySelector('#end-chat-btn');
                if (!endBtn) return 'ERROR: End chat button not found';
                
                // Check if button is visible
                const style = window.getComputedStyle(endBtn);
                if (style.display === 'none' || 
                    style.visibility === 'hidden' ||
                    endBtn.offsetWidth === 0 ||
                    endBtn.offsetHeight === 0) {
                    return 'INFO: No active conversation';
                }
                
                endBtn.click();
                return 'OK';
            })();
            """
            
            result = self.tab.Runtime.evaluate(expression=js_code)
            status = result.get('result', {}).get('value', '')
            
            if status == 'OK':
                print("[MeijuBridge] ✅ Conversation ended")
                return "✅ Conversation ended. Game can now proceed."
            elif status.startswith('INFO:'):
                return "ℹ️ No active conversation to end."
            else:
                return f"❌ {status}"
        
        except Exception as e:
            return f"❌ Failed to end conversation: {e}"
    
    async def check_story_mode(self) -> tuple[bool, Optional[str], bool, bool]:
        """
        Check if story mode is active and get current dialogue text.
        
        Returns:
            Tuple of (is_story_mode, dialogue_text, has_dialogue, has_input)
            - is_story_mode: True if story mode is active
            - dialogue_text: Current dialogue text or None
            - has_dialogue: True if dialogue box is visible (game showing dialogue)
            - has_input: True if input box is visible (game waiting for user)

        Note:
            Story mode also stays active while #story-waiting-message is visible
            (e.g., "Yuki正在思考中...").
        """
        if not self.connected:
            return (False, None, False, False)
        
        try:
            js_code = """
            (function() {
                function isVisible(el) {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && 
                           style.visibility !== 'hidden' && 
                           el.offsetWidth > 0 && 
                           el.offsetHeight > 0;
                }
                
                let dialogueBox = document.querySelector('#dialogue-box');
                let storyInput = document.querySelector('#story-player-input');
                let storyWaiting = document.querySelector('#story-waiting-message');
                
                // Story mode active if dialogue, input, or waiting overlay is visible
                let isStoryMode = isVisible(dialogueBox) || isVisible(storyInput) || isVisible(storyWaiting);
                let dialogueText = '';
                
                if (isVisible(dialogueBox)) {
                    let textEl = document.querySelector('#dialogue-text');
                    dialogueText = textEl ? textEl.innerText : '';
                }
                
                return JSON.stringify({
                    storyMode: isStoryMode,
                    dialogueText: dialogueText,
                    hasDialogue: isVisible(dialogueBox),
                    hasInput: isVisible(storyInput)
                });
            })();
            """
            
            result = self.tab.Runtime.evaluate(expression=js_code)
            data = json.loads(result.get('result', {}).get('value', '{}'))
            
            is_story = data.get('storyMode', False)
            dialogue = data.get('dialogueText', '')
            has_dialogue = data.get('hasDialogue', False)
            has_input = data.get('hasInput', False)
            
            # Track story mode state changes
            if is_story != self.story_mode:
                self.story_mode = is_story
                self.last_story_check = time.time()
                if is_story:
                    print("[MeijuBridge] 📖 Story mode ENABLED")
                else:
                    print("[MeijuBridge] 📖 Story mode DISABLED")
            
            return (is_story, dialogue if dialogue else None, has_dialogue, has_input)
        
        except Exception as e:
            return (False, None, False, False)
    
    async def story_continue(self) -> str:
        """
        Click the continue button in story mode.
        
        Returns:
            Status message
        """
        if not self.connected:
            return "❌ Not connected to game"
        
        try:
            js_code = """
            (function() {
                // Find continue button in dialogue choices
                let continueBtn = document.querySelector('#dialogue-choices button.choice-btn');
                if (!continueBtn) return 'ERROR: Continue button not found';
                
                // Check if visible
                const style = window.getComputedStyle(continueBtn);
                if (style.display === 'none' || 
                    style.visibility === 'hidden' ||
                    continueBtn.offsetWidth === 0) {
                    return 'ERROR: No dialogue to continue';
                }
                
                continueBtn.click();
                return 'OK';
            })();
            """
            
            result = self.tab.Runtime.evaluate(expression=js_code)
            status = result.get('result', {}).get('value', '')
            
            if status == 'OK':
                print("[MeijuBridge] ✅ Clicked continue")
                return "✅ Continued story"
            else:
                return f"❌ {status}"
        
        except Exception as e:
            return f"❌ Failed to continue: {e}"
    
    async def disconnect(self) -> bool:
        """Disconnect from CDP"""
        if self.tab:
            try:
                self.tab.stop()
            except:
                pass
        self.connected = False
        self.active_cdp_port = None
        print("[MeijuBridge] Disconnected from CDP")
        return True
    
    def get_status(self) -> str:
        """Get current bridge status"""
        status = "**妹居物语 Bridge Status (CDP):**\n"
        status += f"Connected: {'✅ Yes' if self.connected else '❌ No (use --init)'}\n"
        status += f"CDP Default: {self.CDP_HOST}:{self.CDP_PORT}\n"
        if self.connected and self.active_cdp_port:
            status += f"CDP Active: {self.CDP_HOST}:{self.active_cdp_port}\n"
        else:
            status += "CDP Active: N/A (not connected)\n"
        status += f"Listen mode: {'🟢 ON' if self.listen_mode else '🔴 OFF'}\n"
        return status
    
    async def get_game_info(self) -> Optional[str]:
        """
        Get game information by reading the DOM.
        """
        if not self.connected:
            if not await self.initialize():
                return "❌ Not connected to game"
        
        try:
            # Execute JavaScript to extract game info from specific IDs
            js_code = """
            (function() {
                let info = {};
                info.time = document.querySelector('#current-time')?.innerText || 'N/A';
                info.date = document.querySelector('#current-date')?.innerText || 'N/A';
                info.city = document.querySelector('#current-city')?.innerText || 'N/A';
                info.day = document.querySelector('#current-day')?.innerText || 'N/A';
                info.coins = document.querySelector('#current-coins')?.innerText || 'N/A';
                return JSON.stringify(info);
            })();
            """
            
            result = self.tab.Runtime.evaluate(expression=js_code)
            json_str = result.get('result', {}).get('value', '')
            
            if not json_str:
                return "📋 No info available"
            
            info = json.loads(json_str)
            formatted = f"""📊 **游戏信息**
🕐 时间: {info['time']}
📅 日期: {info['date']}
🏙️ 城市: {info['city']}
📆 相处天数: 第 {info['day']} 天
💰 金币: {info['coins']}"""
            
            return formatted
            
        except Exception as e:
            return f"❌ Failed to get info: {e}"
    
    async def get_diary_entry(self, index: int) -> str:
        """
        Get a diary entry by index.
        
        Args:
            index: Diary entry index (0 is latest)
            
        Returns:
            Diary entry text or error message
        """
        if not self.connected:
            if not await self.initialize():
                return "❌ Not connected to game"
        
        try:
            print(f"[MeijuBridge] Getting diary entry {index}...")
            
            # First, dismiss any popup modals (like diary notification)
            await self._dismiss_modals()
            await asyncio.sleep(0.3)
            
            # JavaScript to open diary, wait for content to load, get entry, close diary
            js_code = f"""
            (function() {{
                // Open diary
                let diaryBtn = document.querySelector('#diary-btn');
                if (!diaryBtn) return JSON.stringify({{error: 'Diary button not found'}});
                diaryBtn.click();
                
                return JSON.stringify({{status: 'opened'}});
            }})();
            """
            
            result = self.tab.Runtime.evaluate(expression=js_code)
            value = result.get('result', {}).get('value', '')
            open_result = json.loads(value)
            
            if 'error' in open_result:
                return f"❌ {open_result['error']}"
            
            # Wait for diary content to load (longer wait for rendering)
            print("[MeijuBridge] Waiting for diary to load...")
            await asyncio.sleep(1.2)
            
            # Now get the diary entry
            get_entry_js = f"""
            (function() {{
                // Get diary entry
                let entry = document.querySelector('.diary-entry[data-entry-index="{index}"]');
                if (!entry) {{
                    return JSON.stringify({{error: 'Diary entry {index} not found'}});
                }}
                
                let date = entry.getAttribute('data-date') || 'Unknown';
                let content = entry.innerText || '';
                
                return JSON.stringify({{ date: date, content: content }});
            }})();
            """
            
            result = self.tab.Runtime.evaluate(expression=get_entry_js)
            value = result.get('result', {}).get('value', '')
            entry_data = json.loads(value)
            
            # Close diary after reading (like a human would)
            close_js = """
            (function() {
                let backBtn = document.querySelector('#diary-back-btn');
                if (backBtn) {
                    backBtn.click();
                    return 'closed';
                }
                return 'no_button';
            })();
            """
            self.tab.Runtime.evaluate(expression=close_js)
            print("[MeijuBridge] Closed diary")
            
            if 'error' in entry_data:
                return f"❌ {entry_data['error']}"
            
            formatted = f"""📔 **日记 - {entry_data['date']}**

{entry_data['content']}"""
            
            return formatted
            
        except Exception as e:
            return f"❌ Failed to get diary: {e}"
    
    async def send_message(self, message: str) -> Optional[str]:
        """
        Send a message using CDP by manipulating DOM.
        Automatically detects story mode and uses appropriate input.
        """
        if not self.connected:
            if not await self.initialize():
                return "❌ Not connected to game"
        
        try:
            # Dismiss any popups first (like diary notifications)
            await self._dismiss_modals()
            
            # Check if in story mode
            is_story, _, _, _ = await self.check_story_mode()
            
            if is_story:
                # Use story mode input
                send_js = f"""
                (function() {{
                    function isVisible(el) {{
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        return style.display !== 'none' && 
                               style.visibility !== 'hidden' && 
                               el.offsetWidth > 0;
                    }}
                    
                    // Try story input
                    let input = document.querySelector('#story-player-input');
                    let sendBtn = document.querySelector('#story-player-input').parentElement.parentElement.querySelector('button');
                    
                    if (isVisible(input) && sendBtn) {{
                        input.value = {repr(message)};
                        input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        sendBtn.click();
                        return "OK_STORY";
                    }}
                    
                    return "ERROR: Story mode detected but input not available";
                }})();
                """
                
                result = self.tab.Runtime.evaluate(expression=send_js)
                status = result.get('result', {}).get('value', '')
                
                if status == "OK_STORY":
                    print(f"[MeijuBridge] 📖 Sent story mode message: {message}")
                    return "✅ Message sent in story mode. Waiting for story to continue..."
                else:
                    return f"❌ {status}"
            
            # Normal mode: Get all text before sending
            before_text = await self._get_chat_text()
            before_messages = await self._get_chat_messages()
            
            # Send message via JavaScript - detect which input is visible
            send_js = f"""
            (function() {{
                // Helper to check if element is visible
                function isVisible(el) {{
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && 
                           style.visibility !== 'hidden' && 
                           el.offsetWidth > 0 && 
                           el.offsetHeight > 0;
                }}
                
                // Try both input systems
                let input = document.querySelector('#chat-panel-input');
                let sendBtn = document.querySelector('#chat-panel-send-btn');
                
                // If chat panel not visible, use persistent input
                if (!isVisible(input) || !isVisible(sendBtn)) {{
                    input = document.querySelector('#persistent-input');
                    sendBtn = document.querySelector('#persistent-send-btn');
                }}
                
                if (!input) return "ERROR: Cannot find input box";
                if (!sendBtn) return "ERROR: Cannot find send button";
                
                // Set input value
                input.value = {repr(message)};
                input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                
                // Click send button
                sendBtn.click();
                
                return "OK";
            }})();
            """
            
            result = self.tab.Runtime.evaluate(expression=send_js)
            send_result = result.get('result', {}).get('value', '')
            
            if "ERROR" in send_result:
                return f"❌ {send_result}\nUse $bridge --calibration to inspect DOM"
            
            # Wait for reply
            await asyncio.sleep(0.8)
            reply = await self._wait_for_reply(message, before_text, len(before_messages))
            
            return reply if reply else "⏱️ No reply received (timeout)"
            
        except Exception as e:
            return f"❌ Send failed: {e}"

    async def trigger_greeting(self) -> Optional[str]:
        """
        Trigger the persistent greeting button ("让Yuki先说") so Yuki starts first.
        Intended for normal chat mode.
        """
        if not self.connected:
            if not await self.initialize():
                return "❌ Not connected to game"

        try:
            await self._dismiss_modals()

            # Capture chat snapshot before trigger
            before_text = await self._get_chat_text()
            before_messages = await self._get_chat_messages()

            trigger_js = """
            (function() {
                function isVisible(el) {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' &&
                           style.visibility !== 'hidden' &&
                           el.offsetWidth > 0 &&
                           el.offsetHeight > 0;
                }

                let btn = document.querySelector('#persistent-greeting-btn');
                if (!btn) return "ERROR: Greeting button not found";
                if (!isVisible(btn)) return "ERROR: Greeting button not visible";

                btn.click();
                return "OK";
            })();
            """

            result = self.tab.Runtime.evaluate(expression=trigger_js)
            trigger_result = result.get('result', {}).get('value', '')

            if "ERROR" in trigger_result:
                return f"❌ {trigger_result}"

            # Wait for Yuki reply after trigger
            await asyncio.sleep(0.5)
            reply = await self._wait_for_reply("", before_text, len(before_messages))

            return reply if reply else "⏱️ No reply received (timeout)"

        except Exception as e:
            return f"❌ Greeting trigger failed: {e}"
    
    async def _get_chat_text(self) -> str:
        """Get chat text from DOM (only the chat history area)"""
        try:
            # Query chat history directly - works even if hidden
            js_code = """
            (function() {
                let chatArea = document.querySelector('#chat-history-area');
                if (chatArea && chatArea.innerText.trim()) {
                    return chatArea.innerText;
                }
                
                // Fallback: try to find any chat content
                let chatMessages = document.querySelectorAll('.chat-message, .message');
                if (chatMessages.length > 0) {
                    return Array.from(chatMessages).map(el => el.innerText).join('\\n');
                }
                
                return '';
            })();
            """
            result = self.tab.Runtime.evaluate(expression=js_code)
            return result.get('result', {}).get('value', '')
        except:
            return ""

    async def _get_chat_messages(self) -> list[dict[str, str]]:
        """Get structured chat messages from DOM: [{sender, content}]"""
        try:
            js_code = """
            (function() {
                const out = [];
                const nodes = document.querySelectorAll('#chat-history-area .chat-message');
                for (const node of nodes) {
                    const senderEl = node.querySelector('.sender');
                    const contentEl = node.querySelector('.content');
                    const sender = (senderEl ? senderEl.innerText : '').trim();
                    const content = (contentEl ? contentEl.innerText : node.innerText || '').trim();
                    out.push({ sender: sender, content: content });
                }
                return JSON.stringify(out);
            })();
            """
            result = self.tab.Runtime.evaluate(expression=js_code)
            raw = result.get('result', {}).get('value', '[]')
            data = json.loads(raw)
            if isinstance(data, list):
                return data
            return []
        except Exception:
            return []
    
    async def _wait_for_reply(self, sent_text: str, before_text: str, before_count: int = 0) -> str:
        """Wait for Yuki's reply"""
        phase_timeout = self.poll_timeout
        sent_text_norm = self._norm(sent_text).strip()
        sent_text_flat = sent_text_norm.replace("\n", " ").replace("\r", " ").strip()

        # A) Wait for our message to appear
        start = time.time()
        while time.time() - start < phase_timeout:
            msgs = await self._get_chat_messages()
            if msgs and len(msgs) > before_count:
                last = msgs[-1]
                sender = (last.get('sender') or '').strip().lower()
                content = self._norm(last.get('content', '')).strip()
                content_flat = content.replace("\n", " ").replace("\r", " ").strip()
                if sender.startswith('我') and (
                    sent_text_norm in content
                    or sent_text_flat in content_flat
                    or content in sent_text_norm
                ):
                    break

            chat = await self._get_chat_text()
            chat_norm = self._norm(chat)
            chat_flat = chat_norm.replace("\n", " ")
            if (
                (sent_text_norm and sent_text_norm in chat_norm)
                or (sent_text_flat and sent_text_flat in chat_flat)
                or (chat != before_text and len(chat_norm.strip()) > len(self._norm(before_text).strip()))
            ):
                break
            await asyncio.sleep(self.POLL_INTERVAL)
        else:
            return ""

        # B) Wait for Yuki to reply
        start = time.time()
        while time.time() - start < phase_timeout:
            msgs = await self._get_chat_messages()
            if msgs:
                sender = (msgs[-1].get('sender') or '').strip().lower()
                if sender.startswith('yuki'):
                    break

            chat = await self._get_chat_text()
            sp = self._get_last_speaker(chat)
            if sp == "yuki":
                break
            await asyncio.sleep(self.POLL_INTERVAL)
        else:
            return ""

        # C) Wait for reply to stabilize
        last_reply = ""
        stable = 0
        start = time.time()
        while time.time() - start < phase_timeout:
            reply = ""

            msgs = await self._get_chat_messages()
            if msgs:
                last = msgs[-1]
                sender = (last.get('sender') or '').strip().lower()
                if sender.startswith('yuki'):
                    reply = self._clean_yuki_reply(last.get('content', ''))

            if not reply:
                chat = await self._get_chat_text()
                reply = self._extract_last_yuki(chat)

            if reply and reply != last_reply:
                last_reply = reply
                stable = 0
            else:
                stable += 1

            if last_reply and stable >= self.STABLE_ROUNDS:
                return last_reply

            await asyncio.sleep(self.POLL_INTERVAL)

        return last_reply or ""
    
    @staticmethod
    def _norm(s: str) -> str:
        """Normalize line endings"""
        return (s or "").replace("\r\n", "\n")
    
    @staticmethod
    def _clean_yuki_reply(text: str) -> str:
        """Clean up Yuki's reply text"""
        if not text:
            return ""
        
        t = text.strip()
        
        # Remove gift icon and button artifacts that might appear
        t = re.sub(r"🎁\s*", "", t)
        t = re.sub(r"输入你想说的话\.\.\.", "", t)
        
        return t.strip()
    
    def _get_last_speaker(self, chat_text: str) -> str:
        """Get the last speaker from chat text"""
        t = self._norm(chat_text)
        speakers = []
        for m in re.finditer(r"(?m)^(Yuki：|Yuki:|我：|我:)\s*", t):
            tag = m.group(1)
            speakers.append("yuki" if tag.startswith("Yuki") else "me")
        return speakers[-1] if speakers else "unknown"
    
    def _extract_last_yuki(self, chat_text: str) -> str:
        """Extract the last message from Yuki"""
        t = self._norm(chat_text)
        ms = list(re.finditer(r"(?m)^Yuki[：:]\s*", t))
        if not ms:
            return ""
        
        start = ms[-1].start()
        tail = t[start:].strip()
        
        # Cut at next "我:"
        cut = re.search(r"(?m)^(我：|我:)\s*", tail)
        if cut:
            tail = tail[:cut.start()].strip()
        
        tail = re.sub(r"(?m)^Yuki[：:]\s*", "", tail, count=1).strip()
        return self._clean_yuki_reply(tail)
