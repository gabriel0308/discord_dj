import re
import json
import subprocess
import asyncio
import tempfile
import logging
from typing import Optional
import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import pagify, box

log = logging.getLogger("red.vibe")

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

    async def red_delete_data_for_user(self, **kwargs):
        pass

    async def cog_unload(self):
        pass

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

        await ctx.send(f"🎵 Generating playlist for vibe: **{query}**...")

        try:
            songs = await self.generate_playlist(query)
        except Exception as e:
            import traceback
            tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            await ctx.send(f"❌ Failed to generate playlist:\n```\n{tb[:1900]}\n```")
            return

        if not songs:
            await ctx.send("❌ No songs found for that vibe.")
            return

        await ctx.send(f"📋 Found {len(songs)} songs. Adding to queue...")

        added = 0
        play_cmd = self.bot.get_command("play")
        if play_cmd is None:
            await ctx.send("❌ Could not find the `play` command. Make sure Audio cog is loaded.")
            return

        for song in songs:
            search_query = f"{song['title']} {song['artist']}"
            try:
                await ctx.invoke(play_cmd, query=search_query)
                added += 1
                await asyncio.sleep(0.5)
            except Exception as e:
                log.debug(f"Failed to enqueue '{search_query}': {e}")
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

        log.info(f"LLM config: api_key={'SET' if api_key else 'NOT SET'}, base_url={base_url}, model={model}")

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

        # Write body to temp file to avoid shell escaping issues
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(body, f)
            tmp_file = f.name

        url = f"{base_url}/chat/completions"
        log.info(f"Sending request to {url} with model {model}")

        def do_curl():
            try:
                result = subprocess.run(
                    ['curl', '-s', '--max-time', '30', '-X', 'POST', url,
                     '-H', 'Content-Type: application/json',
                     '-H', f'x-api-key: {api_key}',
                     '-H', 'User-Agent: Mozilla/5.0',
                     '-d', f'@{tmp_file}'],
                    capture_output=True,
                    text=True,
                    timeout=35
                )
                return result
            finally:
                import os
                try:
                    os.unlink(tmp_file)
                except:
                    pass

        try:
            result = await asyncio.to_thread(do_curl)
        except subprocess.TimeoutExpired:
            raise RuntimeError("LLM request timed out")

        log.info(f"curl exit code: {result.returncode}")
        log.info(f"curl stdout length: {len(result.stdout)}")
        if result.stderr:
            log.info(f"curl stderr: {result.stderr[:500]}")

        if result.returncode != 0:
            raise RuntimeError(f"curl failed (code {result.returncode}): {result.stderr[:500]}")

        if 'error' in result.stdout.lower() or result.returncode != 0:
            log.error(f"LLM response error: {result.stdout[:500]}")
            raise RuntimeError(f"LLM API error: {result.stdout[:500]}")

        log.info(f"LLM response received, length: {len(result.stdout)} chars")

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            log.error(f"Invalid JSON response: {result.stdout[:500]}")
            raise RuntimeError(f"Invalid JSON from LLM: {result.stdout[:200]}")

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
