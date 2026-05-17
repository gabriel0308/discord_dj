import re
import json
import aiohttp
import asyncio
from typing import Optional
import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import pagify, box

class VibeCog(commands.Cog):
    """Generate playlists using AI and play them continuously."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1778977000)
        default_global = {
            "llm_api_key": "",
            "llm_base_url": "https://opencode.ai/zen/v1",
            "llm_model": "nemotron-3-super-free",
        }
        self.config.register_global(**default_global)
        self._session: Optional[aiohttp.ClientSession] = None

    async def red_delete_data_for_user(self, **kwargs):
        pass

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def cog_unload(self):
        if self._session and not self._session.closed:
            await self._session.close()

    @commands.group(name="vibe", aliases=["v"])
    async def vibe_group(self, ctx: commands.Context):
        """AI-powered music vibe commands."""
        pass

    @vibe_group.command(name="play")
    async def vibe_play(self, ctx: commands.Context, *, query: str):
        """Generate a playlist from AI and start playing.

        Example: `[p]vibe play slipknot`
        Example: `[p]vibe play jazz for studying`
        Example: `[p]vibe play 80s rock hits`
        """
        audio = self.bot.get_cog("Audio")
        if audio is None:
            await ctx.send("❌ Audio cog not loaded. Please load it with `[p]load audio`.")
            return

        if not ctx.author.voice:
            await ctx.send("❌ You need to be in a voice channel!")
            return

        channel = ctx.author.voice.channel

        # Connect to voice channel using Audio cog
        try:
            await audio.command_connect(ctx, channel)
        except Exception:
            await ctx.send("❌ Failed to connect to voice channel.")
            return

        await ctx.send(f"🎵 Generating playlist for vibe: **{query}**...")

        try:
            songs = await self.generate_playlist(query)
        except Exception as e:
            await ctx.send(f"❌ Failed to generate playlist: {e}")
            return

        if not songs:
            await ctx.send("❌ No songs found for that vibe.")
            return

        await ctx.send(f"📋 Found {len(songs)} songs. Adding to queue...")

        added = 0
        for song in songs:
            search_query = f"{song['title']} {song['artist']}"
            try:
                # Try internal API first (most efficient)
                tracks = await audio._get_tracks(ctx, search_query)
                if tracks:
                    player = audio.get_player(ctx.guild)
                    if player:
                        await player.queue.put(tracks[0])
                        added += 1
                else:
                    # Fallback to public play command
                    await ctx.invoke(audio.play, query=search_query)
                    added += 1
                await asyncio.sleep(0.3)
            except Exception as e:
                self.bot.logger.debug(f"Failed to enqueue '{search_query}': {e}")
                continue

        await ctx.send(f"✅ Added {added}/{len(songs)} songs to the queue. Now playing!")

    @vibe_group.command(name="set")
    @commands.is_owner()
    async def vibe_set(self, ctx: commands.Context, key: str, *, value: str):
        """Set LLM configuration (owner only).

        Keys: `api_key`, `base_url`, `model`
        """
        key_map = {
            "api_key": "llm_api_key",
            "base_url": "llm_base_url",
            "model": "llm_model",
        }
        config_key = key_map.get(key.lower())
        if not config_key:
            await ctx.send(f"Invalid key. Valid keys: {', '.join(key_map.keys())}")
            return

        await getattr(self.config, config_key).set(value)
        display = f"{value[:10]}..." if len(value) > 10 else value
        await ctx.send(f"✅ Set `{key}` to `{display}`")

    async def generate_playlist(self, vibe: str) -> list:
        api_key = await self.config.llm_api_key()
        base_url = await self.config.llm_base_url()
        model = await self.config.llm_model()

        if not api_key:
            raise ValueError("LLM API key not set. Use `[p]vibe set api_key <key>`")

        system_prompt = (
            "You are a music curator assistant. Given a vibe, genre, band, or song, "
            "return a JSON array of exactly 20 songs.\n\n"
            "Return ONLY a JSON array in this exact format, nothing else:\n"
            '[{"title": "Song Name", "artist": "Artist Name", "year": "1999"}]\n\n'
            "Do not include any explanation, markdown, or text outside the JSON."
        )

        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Create a playlist for this vibe: \"{vibe}\""},
            ],
            "max_tokens": 2000,
        }

        async with self.session.post(
            f"{base_url}/chat/completions",
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
            },
            json=body,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"LLM API error {resp.status}: {text}")

            data = await resp.json()

        choice = data.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")

        if not content:
            raise RuntimeError("Empty response from LLM")

        songs = self._parse_response(content)
        return songs

    def _parse_response(self, content: str) -> list:
        json_str = content.strip()
        if json_str.startswith("```json"):
            json_str = json_str.replace("```json", "").replace("```", "").strip()
        elif json_str.startswith("```"):
            json_str = json_str.replace("```", "").strip()

        data = json.loads(json_str)

        if isinstance(data, list):
            songs = data
        elif isinstance(data, dict) and "songs" in data:
            songs = data["songs"]
        else:
            raise ValueError("Invalid LLM response format")

        return [
            {"title": s.get("title", ""), "artist": s.get("artist", ""), "year": s.get("year", "")}
            for s in songs
            if s.get("title") and s.get("artist")
        ]
