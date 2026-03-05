"""
Backward-compatible entrypoint.
Use main_discord.py for Discord adapter wiring.
"""

from main_discord import run


if __name__ == "__main__":
    run()
