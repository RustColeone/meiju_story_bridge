# Chrome DevTools Protocol Setup for 妹居物语

## Step 1: Find the Game Executable

The game is built with Electron, so you need to find the `.exe` file.

Common locations:
- Check where the game is installed
- Look for files like `妹居物语.exe` or similar

## Step 2: Launch with Remote Debugging

You need to close the game completely and relaunch it with the `--remote-debugging-port` flag.

### Option A: Create a Shortcut
1. Right-click the game executable → Create Shortcut
2. Right-click the shortcut → Properties
3. In "Target" field, add at the end: ` --remote-debugging-port=9222`
4. Example: `"C:\Games\MeijuStory\妹居物语.exe" --remote-debugging-port=9222`
5. Launch using this shortcut

### Option B: Command Line
```powershell
& "C:\Path\To\妹居物语.exe" --remote-debugging-port=9222
```

## Step 3: Verify Connection

Once the game is running with debugging enabled, open your browser and visit:
```
http://localhost:9222
```

You should see a list of pages. The game's main page will be listed there.

## Step 4: Run the Bot

The bot will automatically connect to `localhost:9222` when you use bridge commands.

## Troubleshooting

**"Connection refused"**
- Make sure the game is running
- Verify you launched with `--remote-debugging-port=9222`
- Check if port 9222 is already in use

**"No tabs found"**
- The game window might not be fully loaded yet
- Try clicking around in the game first
- Restart the game with the flag

**Find the executable**
```powershell
# Search for .exe files in common game directories
Get-ChildItem -Path "C:\Program Files" -Recurse -Filter "*妹居*" -ErrorAction SilentlyContinue
Get-ChildItem -Path "C:\Program Files (x86)" -Recurse -Filter "*妹居*" -ErrorAction SilentlyContinue
Get-ChildItem -Path "$env:LOCALAPPDATA" -Recurse -Filter "*妹居*" -ErrorAction SilentlyContinue
```
