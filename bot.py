"""Icarus clan Discord bot.

- Keeps the server layout (roles, categories, channels) in shape; never deletes anything.
- Join-to-create voice rooms in every category.
- Every 5 min: clan API + Bloxlink -> Leader/Officer/Member/Visitor roles.
- Monday 21:50 Asia/Jakarta: weekly table in Admin/clan-watcher.
- Alerts (who got Officer, name clashes) go to Admin/bot-commands.
- /role-override: admins pin someone's Officer/Member/Visitor role; the sync leaves them alone.
- #honeypot: posting there gets you kicked and your last 5 minutes of messages deleted.
"""
import asyncio
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks

log = logging.getLogger("clanbot")

if Path(".env").exists():  # lets you run it locally with just a .env (systemd sets these itself on the server)
    for _line in Path(".env").read_text().splitlines():
        _k, _, _v = _line.partition("=")
        if _v and not _k.strip().startswith("#"):
            os.environ.setdefault(_k.strip(), _v.strip())

GUILD_ID = int(os.environ.get("GUILD_ID", "0"))
BLOXLINK_KEY = os.environ.get("BLOXLINK_KEY", "")
CLAN_URL = os.environ.get("CLAN_URL", "https://glorybound-clan-network.vercel.app/api/clans/q7vd1z_toioa4")
BLOXLINK_URL = "https://api.blox.link/v4/public/guilds/{guild}/discord-to-roblox/{user}"
ROBLOX_USERS_URL = "https://users.roblox.com/v1/users"
# Leader = Administrator. The clan API only gives display names, which anyone can copy,
# so by default the bot never hands out Leader; clan Leaders get Member and you assign Leader by hand.
AUTO_LEADER = os.environ.get("AUTO_LEADER", "0") == "1"
# Free Bloxlink server keys get a limited number of lookups per day (500 when they announced it).
BLOXLINK_DAILY_LIMIT = int(os.environ.get("BLOXLINK_DAILY_LIMIT", "450"))
DB_PATH = Path(os.environ.get("DB_PATH", "clan.db"))
JKT = ZoneInfo("Asia/Jakarta")

HUB = "Join me to create"
HONEYPOT = "honeypot"  # outside all categories; anyone who posts here is kicked
HONEYPOT_WARNING = "# DO NOT SEND MESSAGES HERE UNLESS YOU WANT TO BE KICKED"
LAYOUT = {
    "Public": ["glorybound-announcements", "announcements", "rules", "general", "flex", "looking-for-party"],
    "Icarus": ["announcements", "general"],
    "Admin": ["general", "clan-watcher", "bot-commands"],
}
# Nobody can post here except Administrators (Leader, server admins). Followed-channel posts still arrive.
READ_ONLY = {("Public", "glorybound-announcements"), ("Public", "announcements"), ("Public", "rules"),
             ("Icarus", "announcements")}
# What people see. The code uses the plain names; a channel with either name is found and renamed.
# Text channel names must be lowercase, and no emoji that needs U+FE0F, or Discord stores them differently.
PRETTY = {
    "Public": "🏺 Public", "Icarus": "🪽 Icarus", "Admin": "🔒 Admin",
    "glorybound-announcements": "📯・glorybound-announcements", "announcements": "📢・announcements",
    "rules": "📜・rules", "general": "💬・general", "flex": "🏆・flex", "looking-for-party": "🎯・looking-for-party",
    "clan-watcher": "📊・clan-watcher", "bot-commands": "🤖・bot-commands", HONEYPOT: "🍯・honeypot",
    HUB: "➕ Join me to create",
}


def pretty(name):
    return PRETTY.get(name, name)


def is_named(channel, plain):
    return channel.name in (plain, pretty(plain))


ROLE_ORDER = ["Leader", "Officer", "Member", "Visitor"]  # top -> bottom
RANK = {"Leader": 0, "Officer": 1, "Member": 2}
ROLE_FOR = {"Leader": "Leader" if AUTO_LEADER else "Member", "Officer": "Officer", "Member": "Member"}
MANAGED = {"Officer", "Member", "Visitor"} | ({"Leader"} if AUTO_LEADER else set())
FIELDS = ("donTotal", "donWeek", "xpTotal", "xpWeek")
LABELS = ("Don total", "Don week", "XP total", "XP week")
HEADER = re.compile(r"^\*\*(\d{4}-\d{2}-\d{2})_partition_(\d+)_of_(\d+)\*\*")
NO_PINGS = discord.AllowedMentions.none()


def open_db(path=DB_PATH):
    db = sqlite3.connect(path)
    db.executescript("""
    -- Bloxlink lookup cache (everyone in the server), so we don't re-query linked users every 5 min.
    CREATE TABLE IF NOT EXISTS links(discord_id INTEGER PRIMARY KEY, roblox_id INTEGER, checked_at REAL NOT NULL,
        misses INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS api_usage(day TEXT PRIMARY KEY, count INTEGER NOT NULL);
    -- Current clan members who are in Discord.
    CREATE TABLE IF NOT EXISTS members(discord_id INTEGER PRIMARY KEY, roblox_id INTEGER NOT NULL, username TEXT,
        display_name TEXT NOT NULL, role TEXT NOT NULL, joined INTEGER);
    -- Clan display name -> the Roblox account that first claimed it (display names aren't unique).
    CREATE TABLE IF NOT EXISTS bindings(clan_name TEXT PRIMARY KEY, roblox_id INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS snapshots(date TEXT, name TEXT, role TEXT, joined INTEGER, donTotal INTEGER,
        donWeek INTEGER, xpTotal INTEGER, xpWeek INTEGER, PRIMARY KEY(date, name));
    -- Admin-set roles the sync must not touch: discord id -> Officer / Member / Visitor.
    CREATE TABLE IF NOT EXISTS overrides(discord_id INTEGER PRIMARY KEY, role TEXT NOT NULL, set_by INTEGER, set_at REAL);
    CREATE TABLE IF NOT EXISTS rooms(channel_id INTEGER PRIMARY KEY, owner_id INTEGER);
    """)
    return db


def match(clan, accounts, bindings):
    """clan: API member dicts. accounts: {discord_id: (roblox_id, username, display_name)}.
    bindings: {clan_name: roblox_id}. Returns ({discord_id: clan member}, new bindings, alerts)."""
    by_name = {}
    for did, (rid, _, disp) in accounts.items():
        by_name.setdefault(disp, []).append((did, rid))
    assign, pinned, alerts = {}, {}, []
    for m in clan:
        name = m["name"]
        cands = by_name.get(name, [])
        bound = bindings.get(name)
        if bound is not None:
            pinned[name] = bound
            for did, rid in cands:
                if rid != bound:
                    alerts.append(f"<@{did}> (Roblox {rid}) uses display name **{name}**, which belongs to Roblox {bound}. "
                                  f"Not given a role. If they really are that clan member: `/bind`.")
            cands = [c for c in cands if c[1] == bound]
        if len({rid for _, rid in cands}) > 1:
            alerts.append(f"Several Roblox accounts use display name **{name}** ("
                          + ", ".join(f"<@{d}>" for d, _ in cands) + "). Nobody got a role; pick the real one with `/bind`.")
            continue
        for did, rid in cands:  # several Discord accounts on one Roblox account all count
            assign[did] = m
            pinned[name] = rid
    return assign, pinned, alerts


def channel_overwrites(category_overwrites, everyone, read_only):
    """Same as the category; read-only channels also stop @everyone from posting."""
    ow = dict(category_overwrites)
    if read_only:
        e = discord.PermissionOverwrite(**dict(ow.get(everyone, discord.PermissionOverwrite())))
        e.update(send_messages=False, send_messages_in_threads=False,
                 create_public_threads=False, create_private_threads=False)
        ow[everyone] = e
    return ow


def wanted_role(clan_member, override):
    """The one managed role someone should have: an admin override wins, then the clan API, else Visitor."""
    if override:
        return override
    return ROLE_FOR.get(clan_member["role"], "Member") if clan_member else "Visitor"


def chunk_lines(lines, limit=1900):
    """Pack log lines into as few Discord messages as fit under the 2000-char limit."""
    msgs, cur = [], ""
    for line in lines:
        line = line[:limit]
        if cur and len(cur) + 1 + len(line) > limit:
            msgs.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    return msgs + ([cur] if cur else [])


def role_change_line(mention, added, removed, clan_member, roblox_username, overridden):
    """One #bot-commands line per member whose roles the sync changed."""
    changes = ", ".join([f"+{r}" for r in sorted(added)] + [f"-{r}" for r in sorted(removed)])
    if overridden:
        why = "admin override"
    elif clan_member:
        why = f"clan {clan_member['role']} **{clan_member['name']}**, Roblox @{roblox_username}"
    else:
        why = "not a matched clan member"
    line = f"{mention}: {changes} ({why})"
    if not overridden and {"Officer", "Leader"} & set(added):
        line += " - wrong person? `/bind` the right one"
    return line


def due_for_lookup(row, now):
    """row: (roblox_id, checked_at, misses) or None. Linked: once a day.
    Not linked: back off 5, 10, 20 min ... up to a day (verifying is caught by on_member_update instead)."""
    if row is None:
        return True
    rid, checked, misses = row
    wait = 86400 if rid is not None else min(300 * 2 ** misses, 86400)
    return now - checked >= wait


def _cell(value, prev):
    return f"{value:,} ({value - prev:+,})" if prev is not None else f"{value:,} (new)"


def render_report(date, clan, prev, limit=1900):
    """Split the weekly table into messages under Discord's 2000-char limit."""
    rows = []
    for m in sorted(clan, key=lambda m: (RANK.get(m["role"], 9), m.get("joined") or 0)):
        p = prev.get(m["name"])
        rows.append([m["name"], m["role"]] + [_cell(m.get(f) or 0, p[f] if p else None) for f in FIELDS])
    head = ["Name", "Role", *LABELS]
    widths = [max(len(r[i]) for r in rows + [head]) for i in range(len(head))]

    def line(r):
        return " | ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip()

    top = [line(head), "-+-".join("-" * w for w in widths)]
    chunks, cur = [], []
    for r in map(line, rows):
        if cur and len("\n".join(top + cur + [r])) > limit - 60:
            chunks.append(cur)
            cur = []
        cur.append(r)
    chunks.append(cur)
    n = len(chunks)
    return [f"**{date}_partition_{i}_of_{n}**\n```\n" + "\n".join(top + c) + "\n```" for i, c in enumerate(chunks, 1)]


def parse_report(texts, before):
    """Newest complete report dated before `before` -> (date, {name: {role, fields}}), else None."""
    editions = {}
    for text in texts:
        h = HEADER.match(text)
        if h and h[1] < before:
            editions.setdefault((h[1], int(h[3])), {})[int(h[2])] = text
    for (date, n), parts in sorted(editions.items(), reverse=True):
        if set(parts) != set(range(1, n + 1)):
            continue  # incomplete edition
        rows = {}
        for i in sorted(parts):
            for ln in parts[i].split("```")[1].strip("\n").splitlines()[2:]:
                cells = [c.strip() for c in ln.split(" | ")]
                rows[cells[0]] = {"role": cells[1],
                                  **{f: int(c.split(" ")[0].replace(",", "")) for f, c in zip(FIELDS, cells[2:])}}
        return date, rows
    return None


class UpstreamError(Exception):
    """An API we depend on misbehaved; skip this cycle instead of stripping roles."""


class ClanBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(intents=intents, allowed_mentions=NO_PINGS)
        self.tree = app_commands.CommandTree(self)
        self.db = open_db()
        self.lock = asyncio.Lock()
        self.alerted = set()
        self.last_room = {}  # member id -> when we last made them a room
        self.trapping = set()  # member ids currently being honeypot-kicked

    @property
    def guild(self):
        return self.get_guild(GUILD_ID)

    def channel(self, cat, name):
        category = next(c for c in self.guild.categories if is_named(c, cat))
        return next(c for c in category.text_channels if is_named(c, name))

    def room_ids(self):
        return {r[0] for r in self.db.execute("SELECT channel_id FROM rooms")}

    # ---------- startup & commands ----------
    async def setup_hook(self):
        self.web = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        g = discord.Object(GUILD_ID)
        staff = app_commands.default_permissions(manage_guild=True)  # Officers and up; hidden from others

        async def run(i, coro, done):
            await i.response.defer(ephemeral=True)
            try:
                out = await coro
                await i.followup.send((out or done)[:1900], ephemeral=True)
            except Exception as e:
                log.exception("command failed")
                await i.followup.send(f"Failed: {type(e).__name__}", ephemeral=True)

        @self.tree.command(name="check-server", description="Check the server layout and fix what's off", guild=g)
        @staff
        async def check_server(i: discord.Interaction):
            async def go():
                notes = await self.ensure_layout(self.guild)
                await self.bot_log([f"**/check-server by {i.user.mention}:**"] + [f"- {n}" for n in notes]
                                   if notes else [])
                return "\n".join(notes)
            await run(i, go(), "Server already matches the layout.")

        @self.tree.command(name="sync-now", description="Re-check clan roles now", guild=g)
        @staff
        async def sync_now(i: discord.Interaction):
            await run(i, self.sync(), "Roles synced.")

        @self.tree.command(name="report-now", description="Post the clan-watcher table now", guild=g)
        @app_commands.describe(save="Save as this week's snapshot (default: preview only)")
        @staff
        async def report_now(i: discord.Interaction, save: bool = False):
            await run(i, self.report(store=save), "Posted in #clan-watcher.")

        @self.tree.command(name="bind", description="Say which Discord member owns a clan display name", guild=g)
        @app_commands.default_permissions(administrator=True)
        async def bind(i: discord.Interaction, clan_name: str, member: discord.Member):
            async def go():
                # /bind decides who becomes Officer, so admins only, even if someone loosens the command setting
                if not i.user.guild_permissions.administrator:
                    return "Only admins can use /bind."
                row = self.db.execute("SELECT roblox_id FROM links WHERE discord_id=?", (member.id,)).fetchone()
                if not row or row[0] is None:
                    return f"{member.mention} isn't linked in Bloxlink yet."
                self.db.execute("INSERT OR REPLACE INTO bindings VALUES (?, ?)", (clan_name, row[0]))
                self.db.commit()
                log.info("%s bound %r to roblox %s", i.user, clan_name, row[0])
                await self.sync()
                return f"**{clan_name}** now belongs to {member.mention} (Roblox {row[0]})."
            await run(i, go(), "")

        async def admins_only(i, go):
            # these decide who gets Officer, so admins only, even if someone loosens the command setting
            if not i.user.guild_permissions.administrator:
                return await i.response.send_message("Only admins can use this.", ephemeral=True)
            await run(i, go(), "")

        @self.tree.command(name="role-override", description="Keep this role on a member; the clan sync won't change it",
                           guild=g)
        @app_commands.default_permissions(administrator=True)
        async def role_override(i: discord.Interaction, member: discord.Member,
                                role: Literal["Officer", "Member", "Visitor"]):
            async def go():
                self.db.execute("INSERT OR REPLACE INTO overrides VALUES (?, ?, ?, ?)",
                                (member.id, role, i.user.id, time.time()))
                self.db.commit()
                await self.channel("Admin", "bot-commands").send(
                    f"{i.user.mention} set a role override: {member.mention} stays **{role}** until "
                    "`/role-override-clear`.")
                await self.sync()
                return f"{member.mention} is now **{role}** and the sync will leave them alone."
            await admins_only(i, go)

        @self.tree.command(name="role-override-clear", description="Let the clan sync manage this member again",
                           guild=g)
        @app_commands.default_permissions(administrator=True)
        async def role_override_clear(i: discord.Interaction, member: discord.Member):
            async def go():
                gone = self.db.execute("DELETE FROM overrides WHERE discord_id=?", (member.id,)).rowcount
                self.db.commit()
                if not gone:
                    return f"{member.mention} has no override."
                await self.channel("Admin", "bot-commands").send(
                    f"{i.user.mention} cleared the role override on {member.mention}; the clan sync manages them again.")
                await self.sync()
                return f"Cleared. {member.mention} now gets whatever the clan API says."
            await admins_only(i, go)

        @self.tree.command(name="role-overrides", description="List members whose role is set by hand", guild=g)
        @app_commands.default_permissions(administrator=True)
        async def role_overrides(i: discord.Interaction):
            async def go():
                rows = self.db.execute("SELECT discord_id, role, set_by, set_at FROM overrides ORDER BY role").fetchall()
                return "\n".join(f"<@{d}> **{r}** (by <@{by}>, <t:{int(at)}:d>)" for d, r, by, at in rows)
            await run(i, go(), "No role overrides.")

        await self.tree.sync(guild=g)

    async def on_ready(self):
        if self.sync_loop.is_running():
            return  # reconnect
        if self.guild is None:
            raise SystemExit(f"Bot isn't in guild {GUILD_ID}")
        notes = await self.ensure_layout(self.guild)
        for note in notes:
            log.info("layout: %s", note)
        await self.bot_log(["**Started; server layout changes:**"] + [f"- {n}" for n in notes] if notes else [])
        for (cid,) in self.db.execute("SELECT channel_id FROM rooms").fetchall():
            ch = self.guild.get_channel(cid)
            if ch is None or not ch.members:
                await self.drop_room(ch, cid)
        self.sync_loop.start()
        self.daily_loop.start()

    async def close(self):
        await self.web.close()
        await super().close()

    # ---------- layout ----------
    @staticmethod
    def category_overwrites(cat_name, guild, roles):
        show, hide = discord.PermissionOverwrite(view_channel=True), discord.PermissionOverwrite(view_channel=False)
        if cat_name == "Public":
            return {guild.default_role: show}
        if cat_name == "Icarus":
            return {guild.default_role: hide, guild.me: show, **{roles[n]: show for n in ("Leader", "Officer", "Member")}}
        # Admin: Officers see and use it, but can't create, edit or delete its channels or their permissions
        officer = discord.PermissionOverwrite(view_channel=True, manage_channels=False, manage_roles=False,
                                              manage_webhooks=False)
        return {guild.default_role: hide, guild.me: show, roles["Leader"]: show, roles["Officer"]: officer}

    async def ensure_layout(self, guild):
        notes = []
        roles = {}
        for name in ROLE_ORDER:
            roles[name] = discord.utils.get(guild.roles, name=name)
            if roles[name] is None:
                roles[name] = await guild.create_role(name=name, reason="clan layout")
                notes.append(f"created role {name}")
        if any(roles[n] >= guild.me.top_role for n in ROLE_ORDER):
            notes.append("WARNING: drag the bot's role above Leader (Server Settings > Roles), then run /check-server")
            return notes  # Discord won't let the bot edit roles above it
        if roles["Leader"].permissions != discord.Permissions(administrator=True):
            await roles["Leader"].edit(permissions=discord.Permissions(administrator=True))
            notes.append("set Leader permissions to Administrator")
        officer = discord.Permissions.all()
        officer.administrator = False  # Administrator would bypass the hidden Admin category
        if roles["Officer"].permissions != officer:
            await roles["Officer"].edit(permissions=officer)
            notes.append("set Officer permissions (everything except Administrator)")
        pos = [roles[n].position for n in ROLE_ORDER]
        if any(a <= b for a, b in zip(pos, pos[1:])):
            top = guild.me.top_role.position
            await guild.edit_role_positions({roles[n]: max(1, top - 1 - i) for i, n in enumerate(ROLE_ORDER)})
            notes.append("reordered roles: Leader > Officer > Member > Visitor")

        rooms = self.room_ids()
        for cat_name, texts in LAYOUT.items():
            want = self.category_overwrites(cat_name, guild, roles)
            cat = next((c for c in guild.categories if is_named(c, cat_name)), None)
            if cat is None:
                cat = await guild.create_category(pretty(cat_name), overwrites=want)
                notes.append(f"created category {pretty(cat_name)}")
            elif cat.name != pretty(cat_name):
                await cat.edit(name=pretty(cat_name))
                notes.append(f"renamed category {cat_name} -> {pretty(cat_name)}")
            for target, ow in want.items():  # enforce only the permissions we set; leave other tweaks alone
                cur = cat.overwrites_for(target)
                wrong = {k: v for k, v in ow if v is not None and getattr(cur, k) != v}
                if wrong:
                    cur.update(**wrong)
                    await cat.set_permissions(target, overwrite=cur)
                    notes.append(f"fixed {cat_name} permissions for {target}: {', '.join(wrong)}")
            needed = [(t, discord.ChannelType.text) for t in texts] + [(HUB, discord.ChannelType.voice)]
            for name, kind in needed:
                ch = (next((c for c in cat.channels if is_named(c, name) and c.type == kind), None)
                      or next((c for c in guild.channels
                               if c.category is None and is_named(c, name) and c.type == kind), None))
                read_only = (cat_name, name) in READ_ONLY
                ow = channel_overwrites(cat.overwrites, guild.default_role, read_only)
                if ch is None:
                    make = cat.create_text_channel if kind == discord.ChannelType.text else cat.create_voice_channel
                    await make(pretty(name), overwrites=ow)
                    notes.append(f"created {cat_name} / {name}" + (" (read-only)" if read_only else ""))
                elif (ch.category != cat or ch.name != pretty(name)
                      or (ch.overwrites != ow if read_only else not ch.permissions_synced)):
                    if read_only:
                        await ch.edit(name=pretty(name), category=cat, overwrites=ow)
                    else:
                        await ch.edit(name=pretty(name), category=cat, sync_permissions=True)
                    notes.append(f"fixed {cat_name} / {name}")
            for c in cat.channels:
                if not any(is_named(c, n) for n, _ in needed) and c.id not in rooms:
                    notes.append(f"extra channel left alone: {cat_name} / {c.name}")

        # honeypot: outside every category, visible and writable for everyone
        open_ow = {guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=True)}
        pot = next((c for c in guild.text_channels if is_named(c, HONEYPOT)), None)
        if pot is None:
            pot = await guild.create_text_channel(pretty(HONEYPOT), overwrites=open_ow)
            notes.append(f"created #{HONEYPOT}")
        elif pot.category is not None or pot.overwrites != open_ow or pot.name != pretty(HONEYPOT):
            await pot.edit(name=pretty(HONEYPOT), category=None, overwrites=open_ow)
            notes.append(f"fixed #{HONEYPOT}")
        if not [m async for m in pot.history(limit=50) if m.author == guild.me and m.content == HONEYPOT_WARNING]:
            await pot.send(HONEYPOT_WARNING)
            notes.append(f"posted the warning in #{HONEYPOT}")
        return notes

    # ---------- honeypot ----------
    async def on_message(self, message):
        ch = message.channel
        if (message.author.bot or message.guild is None or message.guild.id != GUILD_ID
                or not is_named(ch, HONEYPOT) or ch.category is not None):
            return
        member = message.author
        if member.guild_permissions.administrator:  # don't kick admins testing it; just clean up
            return await message.delete()
        if member.id in self.trapping:
            return  # spam burst: already handling this person
        self.trapping.add(member.id)
        try:
            # "Softban": a ban deletes their messages from the last 5 minutes in every channel,
            # the unban right after makes it a kick (they can rejoin with an invite).
            await member.ban(delete_message_seconds=300, reason="posted in #honeypot")
            await member.guild.unban(member, reason="honeypot softban: kick only")
            await self.channel("Admin", "bot-commands").send(
                f"Kicked {member.mention} (`{member}`, {member.id}) for posting in #{HONEYPOT}; "
                "deleted their messages from the last 5 minutes.")
        except discord.HTTPException as e:
            log.warning("honeypot action on %s failed: %s", member.id, e)
            await self.channel("Admin", "bot-commands").send(
                f"{member.mention} posted in #{HONEYPOT} but I couldn't kick/clean up ({e.status}). "
                "Is my role above theirs? If the ban stuck, unban them in Server Settings > Bans.")
        finally:
            self.trapping.discard(member.id)

    # ---------- join-to-create rooms ----------
    async def on_voice_state_update(self, member, before, after):
        if before.channel == after.channel or member.guild.id != GUILD_ID:
            return
        if before.channel and before.channel.id in self.room_ids() and not before.channel.members:
            await self.drop_room(before.channel, before.channel.id)
        hub = after.channel
        if hub and is_named(hub, HUB) and hub.category and any(is_named(hub.category, c) for c in LAYOUT):
            # hub-hopping would make the bot create/delete channels nonstop and hit Discord's rate limits
            if time.monotonic() - self.last_room.get(member.id, -60) < 15:
                return await member.move_to(None)
            self.last_room[member.id] = time.monotonic()
            ow = dict(hub.category.overwrites)
            ow[member] = discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True)
            room = await hub.category.create_voice_channel(f"{member.display_name}'s room"[:100], overwrites=ow)
            self.db.execute("INSERT INTO rooms VALUES (?, ?)", (room.id, member.id))
            self.db.commit()
            try:
                await member.move_to(room)
            except discord.HTTPException:  # they left the hub before we could move them
                await self.drop_room(room, room.id)

    async def drop_room(self, ch, cid):
        self.db.execute("DELETE FROM rooms WHERE channel_id=?", (cid,))
        self.db.commit()
        if ch is not None:
            try:
                await ch.delete(reason="empty join-to-create room")
            except discord.NotFound:
                pass

    # ---------- role sync ----------
    async def fetch_clan(self):
        async with self.web.get(CLAN_URL) as r:
            if r.status != 200:
                raise UpstreamError(f"clan API {r.status}")
            members = (await r.json(content_type=None))["clan"]["members"]
        if not members:
            raise UpstreamError("clan API returned no members")
        return members

    async def refresh_links(self, discord_ids, force=()):
        """Ask Bloxlink about people who are due (see due_for_lookup), `force` first, within the daily budget."""
        now = time.time()
        day = datetime.now(JKT).date().isoformat()
        known = {d: (r, t, m) for d, r, t, m in self.db.execute("SELECT * FROM links")}
        due = [d for d in force if d in discord_ids] + [
            d for d in discord_ids if d not in force and due_for_lookup(known.get(d), now)]
        (used,) = self.db.execute("SELECT COALESCE(SUM(count), 0) FROM api_usage WHERE day=?", (day,)).fetchone()
        for did in due:
            if used >= BLOXLINK_DAILY_LIMIT:
                log.warning("Bloxlink daily budget (%s) used up; %s lookups wait", BLOXLINK_DAILY_LIMIT, len(due))
                break
            used += 1
            self.db.execute("INSERT INTO api_usage VALUES (?, 1) ON CONFLICT(day) DO UPDATE SET count = count + 1",
                            (day,))
            async with self.web.get(BLOXLINK_URL.format(guild=GUILD_ID, user=did),
                                    headers={"Authorization": BLOXLINK_KEY}) as r:
                if r.status == 429:
                    log.warning("Bloxlink rate limit; continuing next cycle")
                    break
                if r.status == 404:
                    data = {}
                elif r.status == 200:
                    data = await r.json(content_type=None)
                else:  # bad key / outage: keep what we know rather than marking everyone unlinked
                    self.db.commit()
                    raise UpstreamError(f"Bloxlink {r.status}")
            rid = data.get("robloxID") or data.get("robloxId")
            rid = int(rid) if str(rid or "").isdigit() else None
            prev = known.get(did)
            misses = 0 if rid is not None else (prev[2] + 1 if prev and prev[0] is None else 0)
            self.db.execute("INSERT OR REPLACE INTO links VALUES (?, ?, ?, ?)", (did, rid, now, misses))
        self.db.execute("DELETE FROM api_usage WHERE day < ?", (day,))
        self.db.commit()

    async def roblox_users(self, ids):
        out = {}
        for i in range(0, len(ids), 100):
            async with self.web.post(ROBLOX_USERS_URL, json={"userIds": ids[i:i + 100], "excludeBannedUsers": False}) as r:
                if r.status != 200:
                    raise UpstreamError(f"Roblox users API {r.status}")
                out.update({u["id"]: (u["name"], u["displayName"]) for u in (await r.json())["data"]})
        return out

    async def sync(self, force=()):
        async with self.lock:
            guild = self.guild
            clan = await self.fetch_clan()
            people = [m for m in guild.members if not m.bot]
            await self.refresh_links([m.id for m in people], force)
            links = {d: r for d, r, _, _ in self.db.execute("SELECT * FROM links") if r is not None}
            users = await self.roblox_users(sorted({links[m.id] for m in people if m.id in links}))
            accounts = {m.id: (links[m.id], *users[links[m.id]]) for m in people
                        if m.id in links and links[m.id] in users}
            assign, pinned, alerts = match(clan, accounts, dict(self.db.execute("SELECT * FROM bindings")))
            with self.db:
                self.db.execute("DELETE FROM bindings")
                self.db.executemany("INSERT INTO bindings VALUES (?, ?)", pinned.items())
                self.db.execute("DELETE FROM members")
                self.db.executemany("INSERT INTO members VALUES (?, ?, ?, ?, ?, ?)",
                                    [(d, *accounts[d], m["role"], m.get("joined")) for d, m in assign.items()])
            roles = {n: discord.utils.get(guild.roles, name=n) for n in MANAGED}
            overrides = dict(self.db.execute("SELECT discord_id, role FROM overrides"))
            changes = []
            for member in people:
                m = assign.get(member.id)
                want = {roles[wanted_role(m, overrides.get(member.id))]}
                have = {r for r in member.roles if r.name in MANAGED}
                if want - have:
                    await member.add_roles(*(want - have), reason="clan sync")
                if have - want:
                    await member.remove_roles(*(have - want), reason="clan sync")
                if want != have:
                    changes.append(role_change_line(
                        member.mention, {r.name for r in want - have}, {r.name for r in have - want}, m,
                        accounts[member.id][1] if member.id in accounts else None, member.id in overrides))
            await self.bot_log(["**Role sync**"] + changes if changes else [])
            await self.bot_log([a for a in alerts if a not in self.alerted])
            self.alerted = set(alerts)

    async def bot_log(self, lines):
        """Post to Admin / bot-commands (no pings), split under Discord's message limit."""
        for msg in chunk_lines(lines):
            await self.channel("Admin", "bot-commands").send(msg)

    async def on_member_join(self, member):
        if member.guild.id != GUILD_ID or member.bot:
            return
        await member.add_roles(discord.utils.get(member.guild.roles, name="Visitor"), reason="new member")
        await self.bot_log([f"{member.mention} joined: +Visitor"])
        try:
            await self.sync(force=[member.id])
        except Exception:
            log.exception("sync on join failed")

    async def on_member_update(self, before, after):
        """Bloxlink gives its own roles / nickname when someone verifies: look them up right away."""
        if after.guild.id != GUILD_ID or after.bot:
            return
        changed = {r.name for r in set(before.roles) ^ set(after.roles)} - MANAGED - {"Leader"}
        if changed or before.nick != after.nick:
            try:
                await self.sync(force=[after.id])
            except Exception:
                log.exception("sync on member update failed")

    @tasks.loop(minutes=5)
    async def sync_loop(self):
        try:
            await self.sync()
        except Exception:
            log.exception("sync failed")

    # ---------- weekly report ----------
    def previous_snapshot(self, date):
        (last,) = self.db.execute("SELECT MAX(date) FROM snapshots WHERE date < ?", (date,)).fetchone()
        if last is None:
            return None
        cols = ("role",) + FIELDS
        return {name: dict(zip(cols, rest)) for name, *rest in self.db.execute(
            "SELECT name, role, donTotal, donWeek, xpTotal, xpWeek FROM snapshots WHERE date=?", (last,))}

    async def recover_snapshot(self, date):
        """Local data lost? Rebuild last week's snapshot from the newest complete report in #clan-watcher."""
        ch = self.channel("Admin", "clan-watcher")
        found = parse_report([m.content async for m in ch.history(limit=500) if m.author == self.user], date)
        if found is None:
            return None
        d, rows = found
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
                                [(d, n, r["role"], *(r[f] for f in FIELDS)) for n, r in rows.items()])
        log.info("recovered snapshot %s from #clan-watcher", d)
        return rows

    async def report(self, store):
        date = datetime.now(JKT).date().isoformat()
        clan = await self.fetch_clan()
        prev = self.previous_snapshot(date) or await self.recover_snapshot(date) or {}
        if store:
            with self.db:
                self.db.executemany("INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                    [(date, m["name"], m["role"], m.get("joined"), *(m.get(f) or 0 for f in FIELDS))
                                     for m in clan])
        ch = self.channel("Admin", "clan-watcher")
        for msg in render_report(date, clan, prev):
            # previews use a different header so recovery never mistakes them for a real week
            await ch.send(msg if store else msg.replace("_partition_", "_preview_", 1))

    @tasks.loop(time=dtime(21, 50, tzinfo=JKT))
    async def daily_loop(self):
        if datetime.now(JKT).weekday() == 0:  # Monday, before the 22:00 reset
            try:
                await self.report(store=True)
            except Exception:
                log.exception("weekly report failed")
        backups = DB_PATH.resolve().parent / "backups"
        backups.mkdir(exist_ok=True)
        dst = sqlite3.connect(backups / f"clan-{datetime.now(JKT):%a}.db")  # keeps the last 7 days
        self.db.backup(dst)
        dst.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for var in ("DISCORD_TOKEN", "GUILD_ID", "BLOXLINK_KEY"):
        if not os.environ.get(var):
            raise SystemExit(f"Missing {var} (see .env.example)")
    ClanBot().run(os.environ["DISCORD_TOKEN"], log_handler=None)
