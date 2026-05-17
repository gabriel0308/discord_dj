from .vibe import VibeCog

async def setup(bot):
    await bot.add_cog(VibeCog(bot))
