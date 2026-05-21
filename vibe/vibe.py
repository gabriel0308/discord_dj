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
            "gemini_api_key": "",
        }
        self.config.register_global(**default_global)

        self.session_state = {}

    async def red_delete_data_for_user(self, **kwargs):
        pass

    async def cog_unload(self):
        self.session_state.clear()

    @commands.Cog.listener()
    async def on_red_audio_queue_end(self, guild: discord.Guild, track, requester):
        log.info(f"Queue ended for guild {guild.id}")
        state = self.session_state.get(guild.id)
        if not state or not state.get("active"):
            return

        if state.get("extending"):
            return

        log.info(f"Triggering auto-extend for guild {guild.id}")
        state["active"] = False
        state["extending"] = True
        await self._auto_extend(guild.id, state)
        state["active"] = True
        state["extending"] = False

    async def _auto_extend(self, guild_id: int, state: dict):
        vibe = state.get("vibe", "")
        played = state.get("played", [])
        channel_id = state.get("channel_id")

        if not vibe or not channel_id:
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        log.info(f"Auto-extending playlist for guild {guild_id}, vibe: {vibe}")
        await channel.send(f"🔄 Queue ending! Generating 10 more songs for **{vibe}**...")

        exclude_list = ", ".join([f'"{s["title"]}" by "{s["artist"]}"' for s in played[-20:]])
        system_prompt = (
            f"You are a music curator assistant. Return a JSON array of exactly 10 songs.\n\n"
            f"Vibe/genre: {vibe}\n"
            f"DO NOT repeat any of these recently played songs:\n[{exclude_list}]\n\n"
            "Rules:\n"
            "1. All 10 songs must be from DIFFERENT artists — no repeated artists.\n"
            "2. None of the songs should match the recently played list above.\n"
            "3. Choose songs that match the vibe/genre/era.\n\n"
            "Return ONLY a JSON array in this exact format, nothing else:\n"
            '[{"title": "Song Name", "artist": "Artist Name", "year": "1999"}]\n\n'
            "Do not include any explanation, markdown, or text outside the JSON."
        )

        try:
            gemini_key = await self.config.gemini_api_key()
            api_key = await self.config.llm_api_key()
            base_url = await self.config.llm_base_url()
            model = await self.config.llm_model()

            songs = None
            if gemini_key:
                try:
                    songs = await self._call_gemini(vibe, gemini_key, system_prompt)
                except Exception as e:
                    log.warning(f"Gemini auto-extend failed: {e}")

            if not songs and api_key:
                try:
                    songs = await self._call_zen(vibe, api_key, base_url, model, system_prompt)
                except Exception as e:
                    log.warning(f"Zen auto-extend failed: {e}")

            if not songs:
                await channel.send("⚠️ Could not generate more songs. Queue will end.")
                return

            play_cmd = self.bot.get_command("play")
            if play_cmd is None:
                return

            added = 0
            for song in songs:
                search_query = f"{song['title']} {song['artist']}"
                try:
                    log.info(f"Auto-extend enqueueing: {search_query}")
                    fake_ctx = await self._build_fake_ctx(channel, search_query)
                    await fake_ctx.invoke(play_cmd, query=search_query)
                    added += 1
                    await asyncio.sleep(0.5)
                except Exception as e:
                    log.warning(f"Auto-extend enqueue failed for '{search_query}': {e}")
                    continue

            state["played"].extend(songs[:added])

            await channel.send(f"✅ Added {added} more songs to the queue!")

        except Exception as e:
            log.error(f"Auto-extend error: {e}")
            await channel.send(f"⚠️ Error extending playlist: {str(e)[:200]}")

    async def _build_fake_ctx(self, channel, query: str):
        guild = channel.guild
        member = guild.me
        message = discord.Object(id=0)
        message.channel = channel
        message.guild = guild
        message.author = member
        message.content = f".play {query}"
        message._state = channel._state
        return await self.bot.get_context(message)

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
                log.info(f"Enqueueing: {search_query}")
                await ctx.invoke(play_cmd, query=search_query)
                added += 1
                log.info(f"Successfully enqueued {added}/{len(songs)}")
                await asyncio.sleep(0.5)
            except Exception as e:
                log.warning(f"Failed to enqueue '{search_query}': {e}")
                continue

        await ctx.send(f"✅ Added {added}/{len(songs)} songs to the queue. Now playing!")

        self.session_state[ctx.guild.id] = {
            "vibe": query,
            "active": True,
            "channel_id": ctx.channel.id,
            "played": songs[:added],
            "extending": False,
        }

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
        msg += f"Zen API Key: `{'SET' if api_key else 'NOT SET'}`\n"
        msg += f"Zen Base URL: `{base_url}`\n"
        msg += f"Zen Model: `{model}`\n"
        msg += f"Gemini Key: `{'SET' if gemini_key else 'NOT SET'}`\n"
        msg += f"\n**Primary:** `{'Gemini' if gemini_key else 'Zen'}`"

        await ctx.send(msg)

    @vibe_group.command(name="stop", aliases=["s"])
    async def vibe_stop(self, ctx: commands.Context):
        """Stop auto-extending the playlist."""
        state = self.session_state.get(ctx.guild.id)
        if state:
            state["active"] = False
            await ctx.send("✅ Auto-extend disabled. Queue will end when finished.")
        else:
            await ctx.send("No active vibe session.")

    @vibe_group.command(name="status")
    async def vibe_status(self, ctx: commands.Context):
        """Show current vibe session status."""
        state = self.session_state.get(ctx.guild.id)
        if not state:
            await ctx.send("No active vibe session.")
            return

        msg = f"**Vibe Session:**\n"
        msg += f"Vibe: **{state['vibe']}**\n"
        msg += f"Auto-extend: `{'ON' if state['active'] else 'OFF'}`\n"
        msg += f"Songs played: `{len(state['played'])}`\n"
        if state["played"]:
            last = state["played"][-1]
            msg += f"Last: `{last['title']}` by `{last['artist']}`"

        await ctx.send(msg)

    async def generate_playlist(self, vibe: str) -> list:
        gemini_key = await self.config.gemini_api_key()
        api_key = await self.config.llm_api_key()
        base_url = await self.config.llm_base_url()
        model = await self.config.llm_model()

        system_prompt = (
            "You are a music curator assistant. Given a vibe, genre, band, or song, "
            "return a JSON array of exactly 10 songs.\n\n"
            "Rules:\n"
            "1. The FIRST song MUST be an exact match for the query (the exact song or artist requested).\n"
            "2. The remaining 9 songs must be from DIFFERENT artists — no repeated artists.\n"
            "3. Choose songs that match the vibe/genre/era of the query.\n\n"
            "Return ONLY a JSON array in this exact format, nothing else:\n"
            '[{"title": "Song Name", "artist": "Artist Name", "year": "1999"}]\n\n'
            "Do not include any explanation, markdown, or text outside the JSON."
        )

        if gemini_key:
            log.info("Using Gemini as primary provider")
            try:
                return await self._call_gemini(vibe, gemini_key, system_prompt)
            except Exception as e:
                import traceback
                log.warning(f"Gemini failed: {e}")
                log.warning(traceback.format_exc())

        if api_key:
            log.info(f"Using Zen provider: {model}")
            try:
                return await self._call_zen(vibe, api_key, base_url, model, system_prompt)
            except Exception as e:
                log.warning(f"Zen failed: {e}")

        raise RuntimeError("No LLM provider available")

    async def _call_zen(self, vibe: str, api_key: str, base_url: str, model: str, system_prompt: str) -> list:
        body = {
            "model": model,
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

        log.info(f"Zen request: POST {url}")
        log.info(f"Zen request body: {json.dumps(body, indent=2)[:800]}")

        connector = aiohttp.TCPConnector(ssl=False, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(total=30)

        try:
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with session.post(url, headers=headers, json=body) as resp:
                    text = await resp.text()
                    log.info(f"Zen response status: {resp.status}")
                    log.info(f"Zen response body: {text[:1000]}")

                    if resp.status != 200:
                        raise RuntimeError(f"Zen API error {resp.status}: {text[:500]}")

                    data = json.loads(text)
                    log.info(f"Zen response received, choices: {len(data.get('choices', []))}")
                    choice = data.get("choices", [{}])[0]
                    content = choice.get("message", {}).get("content", "")

                    if not content:
                        raise RuntimeError("Empty response from Zen")

                    return self._parse_response(content)
        except asyncio.TimeoutError:
            raise RuntimeError("Zen request timed out after 30s")
        except Exception as e:
            import traceback
            log.error(f"Zen exception: {e}")
            log.error(traceback.format_exc())
            raise

    async def _call_gemini(self, vibe: str, api_key: str, system_prompt: str) -> list:
        body = {
            "contents": [
                {"role": "user", "parts": [{"text": f"{system_prompt}\n\nCreate a playlist for this vibe: \"{vibe}\""}]}
            ],
            "generationConfig": {"maxOutputTokens": 2000, "temperature": 0.7},
        }

        masked_key = api_key[:8] + "..." if len(api_key) > 8 else "***"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}

        log.info(f"Gemini request: POST {url.split('?')[0]}?key={masked_key}")
        log.info(f"Gemini request body: {json.dumps(body, indent=2)}")

        connector = aiohttp.TCPConnector(ssl=False, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(total=60)

        log.info(f"Gemini timeout set to 60s")

        try:
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with session.post(url, headers=headers, json=body) as resp:
                    text = await resp.text()
                    log.info(f"Gemini response status: {resp.status}")
                    log.info(f"Gemini response body: {text[:1000]}")

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
        except asyncio.TimeoutError:
            raise RuntimeError("Gemini request timed out after 60s")
        except Exception as e:
            import traceback
            log.error(f"Gemini exception: {e}")
            log.error(traceback.format_exc())
            raise

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

        parsed = [
            {"title": s.get("title", ""), "artist": s.get("artist", ""), "year": s.get("year", "")}
            for s in songs
            if s.get("title") and s.get("artist")
        ]

        seen_artists = set()
        deduped = []
        for s in parsed:
            artist_lower = s["artist"].lower()
            if artist_lower not in seen_artists:
                seen_artists.add(artist_lower)
                deduped.append(s)

        return deduped

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
