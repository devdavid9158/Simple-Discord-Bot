# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Bot

```bash
# Activate virtual environment (Windows)
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the bot
python bot.py
```

**External requirement**: FFmpeg must be installed and in system PATH (required for audio playback).

## Environment Variables

Create a `.env` file with:
- `DISCORD_TOKEN` — Bot token from Discord Developer Portal
- `GUILD_ID` — Discord server ID (for guild-scoped slash command sync on startup)
- `NOTIFY_CHANNEL_ID` — Channel ID for presence change notifications

## Architecture

Single-file bot (`bot.py`) using `discord.py` with slash commands (`app_commands`). No test infrastructure exists.

**Global state** (module-level variables):
- `queue` (`deque`) — Music playback queue of track dicts
- `current_track` (dict | None) — Currently playing track metadata
- `loop` (bool) — Repeat current track toggle
- `rps_game` (dict | None) — Active Rock-Paper-Scissors game state

**Code sections in `bot.py`**:
1. **Mini-games** — `/roll`, `/rps_start`, `/rps`, `/rps_stop`
2. **Music** — `/play`, `/playnext`, `/pause`, `/resume`, `/skip`, `/stop`, `/loop`, `/playing`, `/queue`, `/remove`, `/shuffle`
3. **Event handlers** — `on_presence_update` (online notifications), `on_voice_state_update` (auto-disconnect when voice channel empties), `on_ready` (slash command sync)
4. **Help** — `/help`

**Music playback flow**: `/play` → `yt_dlp` extracts audio URL and metadata → `discord.FFmpegPCMAudio` → `after` callback invokes `play_next()` for queue progression.

**Slash command sync**: In `on_ready`, commands sync to the specific guild (fast) then globally. Guild sync takes effect immediately; global sync can take up to an hour.

All user-facing strings are in Korean (한국어).
