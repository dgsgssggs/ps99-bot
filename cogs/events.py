# cogs/events.py — Event system (Mines mult, Towers mult, Coinflips in a row)

import discord
from discord.ext import commands
from discord import app_commands
from utils import is_owner, fmt_gems, error_embed, COLOR_GOLD, COLOR_ERROR, COLOR_INFO

EVENT_TYPES = {
    "mines":     ("mines_mult",       "💣 Mines Highest Multiplier"),
    "towers":    ("towers_mult",      "🏰 Towers Highest Multiplier"),
    "coinflips": ("coinflip_streak",  "🪙 Coinflip Win Streak"),
}


class Events(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    event = app_commands.Group(name="event", description="Manage casino events (owner only)")

    @event.command(name="start", description="Start an event")
    @app_commands.describe(type="Event type")
    @app_commands.choices(type=[
        app_commands.Choice(name="💣 Mines — Highest Multiplier", value="mines"),
        app_commands.Choice(name="🏰 Towers — Highest Multiplier", value="towers"),
        app_commands.Choice(name="🪙 Coinflips — Most in a Row",  value="coinflips"),
    ])
    async def event_start(self, interaction: discord.Interaction, type: str):
        if not is_owner(interaction.user.id):
            return await interaction.response.send_message(embed=error_embed("Owner only."), ephemeral=True)
        db_key, label = EVENT_TYPES[type]
        await self.bot.db.set_event_active(db_key, True)

        ch_id = await self.bot.db.get_config("event_channel")
        ch_mention = f"<#{ch_id}>" if ch_id else "*(no event channel configured — use /setchannel)*"

        embed = discord.Embed(
            title=f"🏆 Event Started: {label}",
            description=(
                f"The event is now live! Beat the current record to take the top spot.\n"
                f"Records will be posted in {ch_mention}.\n\n"
                f"Records reset to 0 at event start."
            ),
            color=COLOR_GOLD
        )
        await interaction.response.send_message(embed=embed)

        # Announce in event channel if configured
        if ch_id:
            for guild in self.bot.guilds:
                ch = guild.get_channel(int(ch_id))
                if ch:
                    try: await ch.send(embed=embed)
                    except Exception: pass

    @event.command(name="stop", description="Stop an active event")
    @app_commands.describe(type="Event type")
    @app_commands.choices(type=[
        app_commands.Choice(name="💣 Mines — Highest Multiplier", value="mines"),
        app_commands.Choice(name="🏰 Towers — Highest Multiplier", value="towers"),
        app_commands.Choice(name="🪙 Coinflips — Most in a Row",  value="coinflips"),
    ])
    async def event_stop(self, interaction: discord.Interaction, type: str):
        if not is_owner(interaction.user.id):
            return await interaction.response.send_message(embed=error_embed("Owner only."), ephemeral=True)
        db_key, label = EVENT_TYPES[type]
        event = await self.bot.db.get_event(db_key)
        await self.bot.db.set_event_active(db_key, False)

        embed = discord.Embed(title=f"🏁 Event Ended: {label}", color=COLOR_INFO)
        if event and event["holder_id"]:
            val = event["record_value"]
            fmt_val = f"x{val:.2f}" if type != "coinflips" else f"{int(val)} in a row"
            embed.description = f"🏆 **Winner: {event['holder_name']}** — {fmt_val}"
        else:
            embed.description = "No record was set during this event."
        await interaction.response.send_message(embed=embed)

    @event.command(name="status", description="Check current event records")
    async def event_status(self, interaction: discord.Interaction):
        embed = discord.Embed(title="🏆 Active Events", color=COLOR_GOLD)
        any_active = False
        for type_key, (db_key, label) in EVENT_TYPES.items():
            event = await self.bot.db.get_event(db_key)
            if event and event["active"]:
                any_active = True
                val = event["record_value"]
                fmt_val = f"x{val:.2f}" if type_key != "coinflips" else f"{int(val)} in a row"
                holder = event["holder_name"] or "No record yet"
                embed.add_field(name=label, value=f"**{fmt_val}** — {holder}", inline=False)
        if not any_active:
            embed.description = "No events are currently active."
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Events(bot))
