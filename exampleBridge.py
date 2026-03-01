"""
Example Bridge Implementation
This demonstrates how to create a custom bridge following the pattern used in meiju_bridge.py

To create your own bridge:
1. Copy this file to your_bridge.py
2. Implement the four abstract methods
3. Update bridgeParser.py to import your bridge class
4. Test with $bridge commands
"""
from bridgeBase import BridgedObject
from typing import Optional
import asyncio


class ExampleBridge(BridgedObject):
    """
    Simple example bridge that echoes messages.
    
    Replace this implementation with your actual bridge logic:
    - Game automation (like meiju_bridge.py)
    - API calls (REST, GraphQL, etc.)
    - Webhooks (Discord, Slack, Teams, etc.)
    - Database logging
    - File operations
    - etc.
    """
    
    def __init__(self, channel_id: str):
        super().__init__(channel_id)
        self.connected = False
        self.message_count = 0
        
        # Add your custom initialization here
        # Examples:
        # - self.api_key = "..."
        # - self.webhook_url = "..."
        # - self.game_window_pos = None
    
    async def initialize(self) -> bool:
        """
        Initialize/connect to your target application.
        
        Examples of what to do here:
        - Calibrate window positions (see meiju_bridge.py)
        - Test API connection
        - Verify webhook URL
        - Open database connection
        - Load configuration
        
        Returns:
            True if initialization succeeded, False otherwise
        """
        print("[ExampleBridge] Initializing...")
        
        # TODO: Replace with your initialization logic
        await asyncio.sleep(0.1)  # Simulate async operation
        
        self.connected = True
        self.message_count = 0
        
        print("[ExampleBridge] ✅ Initialized successfully")
        return True
    
    async def disconnect(self) -> bool:
        """
        Cleanup and disconnect from your target application.
        
        Examples of what to do here:
        - Save calibration data
        - Close connections
        - Clear cached data
        - Log final statistics
        
        Returns:
            True if disconnection succeeded, False otherwise
        """
        print("[ExampleBridge] Disconnecting...")
        
        # TODO: Replace with your cleanup logic
        self.connected = False
        
        print("[ExampleBridge] ✅ Disconnected")
        return True
    
    def get_status(self) -> str:
        """
        Return status information about the bridge.
        
        Show relevant information such as:
        - Connection state
        - Configuration details
        - Statistics
        - Health checks
        
        Returns:
            Status string (Markdown formatted for Discord)
        """
        status = "**Example Bridge Status:**\n"
        status += f"Connected: {'✅ Yes' if self.connected else '❌ No'}\n"
        status += f"Messages sent: {self.message_count}\n"
        status += f"Listen mode: {'🟢 ON' if self.listen_mode else '🔴 OFF'}"
        
        # TODO: Add your custom status info
        # Examples:
        # - status += f"\nAPI health: {self.api_status}"
        # - status += f"\nLast sync: {self.last_sync_time}"
        
        return status
    
    async def send_message(self, message: str) -> Optional[str]:
        """
        Send a message to your target application.
        
        This is the main integration point. Examples:
        
        API Bridge:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"text": message}) as resp:
                    data = await resp.json()
                    return data.get("reply")
        
        Webhook Bridge:
            async with aiohttp.ClientSession() as session:
                await session.post(webhook_url, json={"content": message})
            return None  # No reply expected
        
        GUI Automation:
            # See meiju_bridge.py for complete example
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._send_to_app, message)
            reply = await loop.run_in_executor(None, self._wait_for_reply)
            return reply
        
        Args:
            message: The message text to send
            
        Returns:
            Optional reply string. Return None if no reply is expected.
            Return error message starting with ❌ to indicate failure.
        """
        if not self.connected:
            return "❌ Bridge not initialized. Use --init first."
        
        self.message_count += 1
        
        # TODO: Replace with your actual send logic
        print(f"[ExampleBridge] Sending: {message}")
        await asyncio.sleep(0.1)  # Simulate async operation
        
        # Example: Echo the message back
        reply = f"🔁 Echo #{self.message_count}: {message}"
        
        print(f"[ExampleBridge] Reply: {reply}")
        return reply


# =============================================================================
# REAL-WORLD EXAMPLES
# =============================================================================

class APIBridgeExample(BridgedObject):
    """Example: Bridge to a REST API"""
    
    def __init__(self, channel_id: str):
        super().__init__(channel_id)
        self.api_url = "https://api.example.com"
        self.api_key = "your-api-key"
        self.session = None
    
    async def initialize(self) -> bool:
        import aiohttp
        self.session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {self.api_key}"}
        )
        # Test connection
        try:
            async with self.session.get(f"{self.api_url}/health") as resp:
                return resp.status == 200
        except:
            return False
    
    async def disconnect(self) -> bool:
        if self.session:
            await self.session.close()
        return True
    
    def get_status(self) -> str:
        return f"**API Bridge**\nURL: {self.api_url}\nConnected: {'✅' if self.session else '❌'}"
    
    async def send_message(self, message: str) -> Optional[str]:
        async with self.session.post(
            f"{self.api_url}/send",
            json={"message": message}
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("reply")
            return f"❌ API error: {resp.status}"


class WebhookBridgeExample(BridgedObject):
    """Example: Forward to a webhook"""
    
    def __init__(self, channel_id: str):
        super().__init__(channel_id)
        self.webhook_url = "https://hooks.example.com/webhook"
    
    async def initialize(self) -> bool:
        return True  # No setup needed
    
    async def disconnect(self) -> bool:
        return True  # No cleanup needed
    
    def get_status(self) -> str:
        return f"**Webhook Bridge**\nURL: {self.webhook_url}"
    
    async def send_message(self, message: str) -> Optional[str]:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.webhook_url,
                json={"content": message}
            ) as resp:
                if resp.status in [200, 204]:
                    return None  # Success, no reply
                return f"❌ Webhook failed: {resp.status}"


# =============================================================================
# USAGE GUIDE
# =============================================================================
"""
STEP-BY-STEP: Creating Your Own Bridge

1. CREATE YOUR BRIDGE FILE
   - Copy this file to my_app_bridge.py
   - Rename ExampleBridge to MyAppBridge

2. IMPLEMENT THE FOUR METHODS
   - initialize(): Setup your connection
   - send_message(): Main logic to send messages
   - disconnect(): Cleanup
   - get_status(): Return status info

3. UPDATE bridgeParser.py
   Change the import at the top:
   ```python
   from my_app_bridge import MyAppBridge
   ```
   
   Update the instance creation:
   ```python
   if channel_id not in bridge_instances:
       bridge_instances[channel_id] = MyAppBridge(channel_id)
   ```

4. TEST IN DISCORD
   ```
   $bridge --init
   $bridge send test message
   $bridge --status
   $bridge --listen on
   ```

5. CUSTOMIZE COMMANDS (Optional)
   You can modify bridgeParser.py to add custom commands:
   ```python
   elif cmd.startswith('--custom'):
       # Your custom command logic
       return ("Custom response", 'custom', bridge)
   ```

SEE ALSO:
- meiju_bridge.py - Complete GUI automation example
- BRIDGE_README.md - Detailed documentation
- bridgeBase.py - Abstract base class definition
"""
# =============================================================================
# 
# To create your own bridge:
# 
# 1. Copy this file and rename it (e.g., myGameBridge.py)
# 
# 2. Rename the class (e.g., class MyGameBridge(BridgedObject))
# 
# 3. Implement the abstract methods:
#    - send_message(): How to send messages to your target
#    - initialize(): How to connect/setup your bridge
#    - disconnect(): How to cleanup/disconnect
#    - get_status(): What info to show about your bridge
# 
# 4. Update bridgeParser.py with your custom commands
# 
# 5. In main.py, change:
#    from exampleBridge import ExampleBridge
#    to:
#    from myGameBridge import MyGameBridge
#    
#    And create your bridge instance instead of ExampleBridge
# 
# Example real implementations:
# 
# - Game Chat Bridge (like meiju_bridge.py):
#   - initialize(): Calibrate window positions, find game window
#   - send_message(): Use pyautogui to type message, wait for reply
#   - disconnect(): Save calibration, cleanup
#   - get_status(): Show window found, calibration status
# 
# - Slack Bridge:
#   - initialize(): Authenticate with Slack API
#   - send_message(): Post message via Slack API
#   - disconnect(): Revoke tokens
#   - get_status(): Show workspace, channel info
# 
# - Webhook Bridge:
#   - initialize(): Test webhook URL
#   - send_message(): POST to webhook
#   - disconnect(): Clear cached data
#   - get_status(): Show webhook URL, success rate
# 
# =============================================================================
