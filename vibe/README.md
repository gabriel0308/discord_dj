# Vibe Cog for Red-DiscordBot

AI-powered music playlist generation for Red-DiscordBot.

## Features

- Generate playlists using AI (LLM)
- Automatically adds songs to the Audio cog queue
- Supports any vibe, genre, artist, or mood
- Continuous playback until paused/disconnected

## Installation

1. Add this cog to your Red-DiscordBot:
   ```
   [p]repo add vibe https://github.com/yourusername/discord-vibe-bot --branch main
   ```

   Or copy the `vibe` folder to your Red-DiscordBot's cogs directory:
   ```
   ~/.local/share/Red-DiscordBot/data/Red-DiscordBot/cogs/Vibe/
   ```

2. Load the cog:
   ```
   [p]load vibe
   ```

3. Make sure the Audio cog is loaded:
   ```
   [p]load audio
   ```

4. Set your LLM API key:
   ```
   [p]vibe set api_key sk-I7rQ5kWZNMdfR0pHMVZdvV8bEjfPtdsZar4jc9V0SqMVxOcKYZh34NdW2LzOJP8e
   ```

5. (Optional) Set custom LLM base URL or model:
   ```
   [p]vibe set base_url https://opencode.ai/zen/v1
   [p]vibe set model nemotron-3-super-free
   ```

## Usage

- `[p]vibe play <query>` - Generate a playlist and start playing
  - Examples:
    - `[p]vibe play slipknot`
    - `[p]vibe play jazz for studying`
    - `[p]vibe play 80s rock hits`

- `[p]vibe set <key> <value>` - Set LLM configuration (owner only)
  - Keys: `api_key`, `base_url`, `model`

## Requirements

- Red-DiscordBot 3.5.0+
- Audio cog (loaded)
- LLM API key (OpenCode Zen or compatible)

## Notes

- This cog uses the Audio cog's internal `_get_tracks` method for efficiency
- Falls back to the public `play` command if internal method fails
- The AI generates 20 songs per request
- Songs are added to the queue with a 0.3s delay to avoid rate limits
