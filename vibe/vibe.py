import re
import json
import aiohttp
import asyncio
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
            "llm_model": "gpt-5.1",
            "gemini_api_key": "AIzaSyDZgXVvwZ0lB07A4VIJQ2WIf62qit52a6Q",
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

        songs = None
        try:
            songs = await self.generate_playlist(query)
        except Exception as e:
            log.warning(f"LLM failed, falling back to direct search: {e}")
            await ctx.send(f"⚠️ AI unavailable, searching directly for **{query}**...")

        if not songs:
            play_cmd = self.bot.get_command("play")
            if play_cmd is None:
                await ctx.send("❌ Could not find the `play` command.")
                return
            await ctx.invoke(play_cmd, query=f"ytsearch:{query}")
            await ctx.send(f"✅ Searching directly for **{query}** on YouTube.")
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

        Keys: `api_key`, `base_url`, `model`, `gemini_key`
        """
        key_map = {
            "api_key": "llm_api_key",
            "base_url": "llm_base_url",
            "model": "llm_model",
            "gemini_key": "gemini_api_key",
        }
        config_key = key_map.get(key.lower())
        if not config_key:
            await ctx.send(f"Invalid key. Valid keys: {', '.join(key_map.keys())}")
            return

        await getattr(self.config, config_key).set(value)
        display = f"{value[:10]}..." if len(value) > 10 else value
        await ctx.send(f"✅ Set `{key}` to `{display}`")

    @vibe_group.command(name="direct", aliases=["d"])
    async def vibe_direct(self, ctx: commands.Context, *, query: str):
        """Search and play directly on YouTube without AI.

        Example: `[p]vibe direct slipknot`
        """
        audio = self.bot.get_cog("Audio")
        if audio is None:
            await ctx.send("❌ Audio cog not loaded.")
            return
        if not ctx.author.voice:
            await ctx.send("❌ You need to be in a voice channel!")
            return

        await ctx.send(f"🔍 Searching YouTube for **{query}**...")
        play_cmd = self.bot.get_command("play")
        if play_cmd is None:
            await ctx.send("❌ Could not find the `play` command.")
            return
        await ctx.invoke(play_cmd, query=f"ytsearch:{query}")

    @vibe_group.command(name="debug")
    @commands.is_owner()
    async def vibe_debug(self, ctx: commands.Context):
        """Show current LLM config and recent log entries."""
        api_key = await self.config.llm_api_key()
        base_url = await self.config.llm_base_url()
        model = await self.config.llm_model()
        gemini_key = await self.config.gemini_api_key()

        msg = f"**LLM Config:**\n"
        msg += f"API Key: `{'SET' if api_key else 'NOT SET'}`\n"
        msg += f"Base URL: `{base_url}`\n"
        msg += f"Model: `{model}`\n"
        msg += f"Gemini Key: `{'SET' if gemini_key else 'NOT SET'}`\n"

        await ctx.send(msg)

    FALLBACK_MODELS = ["qwen3.6-plus-free", "deepseek-v4-flash-free", "minimax-m2.5-free"]

    async def generate_playlist(self, vibe: str) -> list:
        api_key = await self.config.llm_api_key()
        base_url = await self.config.llm_base_url()
        model = await self.config.llm_model()
        gemini_key = await self.config.gemini_api_key()

        log.info(f"LLM config: api_key={'SET' if api_key else 'NOT SET'}, base_url={base_url}, model={model}")

        if not api_key:
            raise ValueError("LLM API key not set. Use `[p]vibe set api_key <key>`")

        system_prompt = (
            "You are a music curator assistant. Given a vibe, genre, band, or song, "
            "return a JSON array of exactly 10 songs.\n\n"
            "Return ONLY a JSON array in this exact format, nothing else:\n"
            '[{"title": "Song Name", "artist": "Artist Name", "year": "1999"}]\n\n'
            "Do not include any explanation, markdown, or text outside the JSON."
        )

        body = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Create a playlist for this vibe: \"{vibe}\""},
            ],
            "max_tokens": 2000,
        }

        url = f"{base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.9",
        }

        models_to_try = [model]
        last_error = None

        for attempt_model in models_to_try:
            body["model"] = attempt_model
            log.info(f"Trying Zen model: {attempt_model}")
            log.info(f"Zen request body: {json.dumps(body, indent=2)[:500]}")

            connector = aiohttp.TCPConnector(ssl=False, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(total=15)

        try:
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with session.post(url, headers=headers, json=body) as resp:
                    log.info(f"LLM response status: {resp.status}")
                    if resp.status == 429:
                        log.warning("Zen rate limited, trying Gemini fallback")
                        if gemini_key:
                            return await self._call_gemini(vibe, gemini_key)
                        raise RuntimeError("Rate limited, no Gemini key")

                    if resp.status != 200:
                        text = await resp.text()
                        log.error(f"LLM API error {resp.status}: {text}")
                        if gemini_key:
                            return await self._call_gemini(vibe, gemini_key)
                        raise RuntimeError(f"LLM API error {resp.status}")

                    data = await resp.json()
                    log.info(f"LLM response received, choices: {len(data.get('choices', []))}")
                    choice = data.get("choices", [{}])[0]
                    content = choice.get("message", {}).get("content", "")

                    if not content:
                        raise RuntimeError("Empty response from LLM")

                    return self._parse_response(content)

        except aiohttp.ClientConnectorError as e:
            if gemini_key:
                return await self._call_gemini(vibe, gemini_key)
            raise RuntimeError(f"Connection failed: {e}")
        except asyncio.TimeoutError:
            if gemini_key:
                return await self._call_gemini(vibe, gemini_key)
            raise RuntimeError("LLM request timed out")

        raise RuntimeError("All models failed")

    async def _call_gemini(self, vibe: str, api_key: str) -> list:
        system_prompt = (
            "You are a music curator assistant. Given a vibe, genre, band, or song, "
            "return a JSON array of exactly 10 songs.\n\n"
            "Return ONLY a JSON array in this exact format, nothing else:\n"
            '[{"title": "Song Name", "artist": "Artist Name", "year": "1999"}]\n\n'
            "Do not include any explanation, markdown, or text outside the JSON."
        )

        body = {
            "contents": [
                {"role": "user", "parts": [{"text": f"{system_prompt}\n\nCreate a playlist for this vibe: \"{vibe}\""}]}
            ],
            "generationConfig": {"maxOutputTokens": 2000, "temperature": 0.7},
        }

        masked_key = api_key[:8] + "..." if len(api_key) > 8 else "***"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}

        log.info(f"Gemini request: POST {url.split('?')[0]}?key={masked_key}")
        log.info(f"Gemini request body: {json.dumps(body, indent=2)}")

        connector = aiohttp.TCPConnector(ssl=False, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(total=15)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.post(url, headers=headers, json=body) as resp:
                text = await resp.text()
                log.info(f"Gemini response status: {resp.status}")
                log.info(f"Gemini response headers: {dict(resp.headers)}")
                log.info(f"Gemini response body: {text}")

                if resp.status != 200:
                    raise RuntimeError(f"Gemini API error {resp.status}: {text[:1000]}")

                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    raise RuntimeError(f"Gemini returned invalid JSON: {text[:500]}")

                content = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")

                if not content:
                    raise RuntimeError(f"Empty response from Gemini: {data}")

                return self._parse_response(content)

    def _parse_response(self, content: str) -> list:
        json_str = content.strip()
        if json_str.startswith("```json"):
            json_str = json_str.replace("```json", "").replace("```", "").strip()
        elif json_str.startswith("```"):
            json_str = json_str.replace("```", "").strip()

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            json_str = self._try_fix_json(json_str)
            if json_str:
                try:
                    data = json.loads(json_str)
                except json.JSONDecodeError:
                    data = None
            else:
                data = None

        if data is None:
            songs = self._regex_fallback(content)
        elif isinstance(data, list):
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

    def _try_fix_json(self, json_str: str) -> str:
        json_str = json_str.rstrip(", \n\r\t")
        if not json_str.startswith("["):
            start = json_str.find("[")
            if start != -1:
                json_str = json_str[start:]
            else:
                return ""
        if not json_str.endswith("]"):
            last_bracket = json_str.rfind("]")
            if last_bracket != -1:
                json_str = json_str[:last_bracket + 1]
            else:
                json_str = json_str.rstrip() + "]"
        json_str = json_str.rstrip("]")
        json_str = json_str.rstrip(", \n\r\t")
        json_str += "]"
        return json_str

    def _regex_fallback(self, content: str) -> list:
        pattern = r'"title"\s*:\s*"([^"]+)"\s*,\s*"artist"\s*:\s*"([^"]+)"'
        matches = re.findall(pattern, content)
        return [{"title": t, "artist": a, "year": ""} for t, a in matches]
