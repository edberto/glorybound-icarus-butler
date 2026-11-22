"""Run: python3 test_bot.py"""
from bot import match, parse_report, render_report

clan = [
    {"role": "Member", "name": "Zippo", "joined": 30, "donTotal": 0, "donWeek": 0, "xpTotal": 0, "xpWeek": 0},
    {"role": "Officer", "name": "rexgyrox", "joined": 20, "donTotal": 160000, "donWeek": 20000, "xpTotal": 3200, "xpWeek": 400},
    {"role": "Leader", "name": "Ariusantsarowana", "joined": 10, "donTotal": 140000, "donWeek": 0, "xpTotal": 2800, "xpWeek": 0},
    {"role": "Member", "name": "Alpha", "joined": 5, "donTotal": 1, "donWeek": 1, "xpTotal": 1, "xpWeek": 1},
]

# first claim pins the name; an impostor with the same display name later gets nothing
assign, pinned, alerts = match(clan, {1: (100, "rex", "rexgyrox")}, {})
assert assign[1]["role"] == "Officer" and pinned == {"rexgyrox": 100} and not alerts
assign, pinned, alerts = match(clan, {1: (100, "rex", "rexgyrox"), 2: (999, "fake", "rexgyrox")}, pinned)
assert set(assign) == {1} and len(alerts) == 1

# two unpinned accounts with the same display name: nobody gets the role
assign, _, alerts = match(clan, {1: (100, "a", "Zippo"), 2: (200, "b", "Zippo")}, {})
assert not assign and len(alerts) == 1

# names that leave the clan drop their pin
_, pinned, _ = match(clan, {}, {"rexgyrox": 100, "Gone": 5})
assert pinned == {"rexgyrox": 100}

# big table: split under 2000 chars, sorted by role then joined, round-trips through the parser
big = clan + [{"role": "Member", "name": f"member_{i:02}", "joined": 100 + i, "donTotal": 10**6 * i,
               "donWeek": 5000, "xpTotal": 9000, "xpWeek": -1} for i in range(60)]
prev = {"rexgyrox": {"role": "Officer", "donTotal": 150000, "donWeek": 0, "xpTotal": 3000, "xpWeek": 0}}
msgs = render_report("2026-11-23", big, prev)
assert len(msgs) > 1 and all(len(m) < 2000 for m in msgs), [len(m) for m in msgs]
assert msgs[0].startswith(f"**2026-11-23_partition_1_of_{len(msgs)}**")
first_rows = msgs[0].split("```")[1].splitlines()[3:6]
assert [r.split(" | ")[0].strip() for r in first_rows] == ["Ariusantsarowana", "rexgyrox", "Alpha"]
assert "160,000 (+10,000)" in msgs[0] and "(new)" in msgs[0]

date, rows = parse_report(list(reversed(msgs)) + ["chatter"], before="2026-11-30")
assert date == "2026-11-23" and len(rows) == len(big)
assert rows["rexgyrox"] == {"role": "Officer", "donTotal": 160000, "donWeek": 20000, "xpTotal": 3200, "xpWeek": 400}
assert rows["member_05"]["xpWeek"] == -1

# a missing partition makes that edition unusable; older complete ones still work
older = render_report("2026-11-16", clan, {})
assert parse_report(msgs[1:] + older, before="2026-11-30")[0] == "2026-11-16"
# reports dated today or later are ignored (we want the *previous* week)
assert parse_report(msgs, before="2026-11-23") is None
print("ok")

# read-only channels keep the category's visibility and add "no posting" for @everyone
import discord
from bot import channel_overwrites
everyone, member_role = "everyone", "Member"
cat = {everyone: discord.PermissionOverwrite(view_channel=False), member_role: discord.PermissionOverwrite(view_channel=True)}
ro = channel_overwrites(cat, everyone, True)
assert ro[everyone].view_channel is False and ro[everyone].send_messages is False
assert ro[member_role] == cat[member_role] and cat[everyone].send_messages is None  # category untouched
assert channel_overwrites({}, everyone, True)[everyone].send_messages is False
assert channel_overwrites(cat, everyone, False) == cat
print("ok (channels)")

# Bloxlink budget: linked people daily, unlinked people back off 5m -> 10m -> ... -> 1 day
from bot import due_for_lookup
day = 86400
assert due_for_lookup(None, 0)
assert not due_for_lookup((123, 0, 0), day - 1) and due_for_lookup((123, 0, 0), day)
assert not due_for_lookup((None, 0, 0), 299) and due_for_lookup((None, 0, 0), 300)
assert not due_for_lookup((None, 0, 3), 2399) and due_for_lookup((None, 0, 3), 2400)
assert not due_for_lookup((None, 0, 50), day - 1) and due_for_lookup((None, 0, 50), day)
# one never-verifying visitor costs ~9 lookups on day one, then 1 a day
t, misses, calls = 0, 0, 0
while t < day:
    calls += 1
    t += min(300 * 2 ** misses, day)
    misses += 1
assert calls <= 10, calls
print("ok (bloxlink budget)")

# manual overrides beat the clan API; otherwise clan role, else Visitor
from bot import wanted_role
officer = {"role": "Officer", "name": "rexgyrox"}
assert wanted_role(officer, None) == "Officer"
assert wanted_role(officer, "Member") == "Member"
assert wanted_role(None, "Officer") == "Officer"
assert wanted_role(None, None) == "Visitor"
assert wanted_role({"role": "Leader", "name": "x"}, None) == "Member"  # Leader is given by hand
print("ok (overrides)")

# pretty names: text channels must already be in the form Discord stores (lowercase, no U+FE0F)
from types import SimpleNamespace
from bot import PRETTY, LAYOUT, HONEYPOT, is_named
for plain, shown in PRETTY.items():
    assert "️" not in shown, shown
    if plain in {n for names in LAYOUT.values() for n in names} | {HONEYPOT}:
        assert shown == shown.lower() and " " not in shown, shown
assert is_named(SimpleNamespace(name="general"), "general")  # old plain channels are found, then renamed
assert is_named(SimpleNamespace(name="💬・general"), "general")
assert not is_named(SimpleNamespace(name="general-2"), "general")
print("ok (names)")

# Admin category: Officers see it but can't edit its channels; Members/Visitors don't see it
from bot import ClanBot
g = SimpleNamespace(default_role="everyone", me="bot")
roles = {n: n for n in ("Leader", "Officer", "Member", "Visitor")}
admin = ClanBot.category_overwrites("Admin", g, roles)
assert admin["Officer"].view_channel is True and admin["Officer"].manage_channels is False
assert admin["Officer"].manage_roles is False and admin["everyone"].view_channel is False
assert "Member" not in admin and admin["Leader"].view_channel is True
assert ClanBot.category_overwrites("Icarus", g, roles)["Member"].view_channel is True
print("ok (admin category)")

# #bot-commands log: one line per member, packed into messages under 2000 chars
from bot import chunk_lines, role_change_line
line = role_change_line("<@1>", {"Officer"}, {"Visitor"}, {"role": "Officer", "name": "rexgyrox"}, "rex", False)
assert line.startswith("<@1>: +Officer, -Visitor (clan Officer **rexgyrox**, Roblox @rex)") and "/bind" in line
assert role_change_line("<@2>", {"Visitor"}, {"Member"}, None, None, False) == "<@2>: +Visitor, -Member (not a matched clan member)"
assert "/bind" not in role_change_line("<@3>", {"Officer"}, set(), None, None, True)
msgs = chunk_lines([f"<@{i}>: +Member, -Visitor (clan Member **someone_{i}**, Roblox @someone)" for i in range(300)])
assert len(msgs) > 1 and all(len(m) <= 1900 for m in msgs) and sum(m.count("\n") + 1 for m in msgs) == 300
assert chunk_lines([]) == []
print("ok (bot log)")

# looking-for-party cards
from bot import lfp_should_close, lfp_embed, join_url, LFP_MAX_AGE
import bot as _bot
assert not lfp_should_close(0, 60, 2) and lfp_should_close(0, LFP_MAX_AGE, 2)
assert not lfp_should_close(0, 300, 0)  # offline right after posting: give them 10 min to get in game
assert lfp_should_close(0, 600, 0) and not lfp_should_close(0, 600, 1)
e = lfp_embed("Rex", None, "Weekly Trials", "need healer", "rexgyrox", 2, 0)
assert e.title.startswith("Weekly Trials") and e.fields[1].value == "In game" and e.description == "need healer"
assert lfp_embed("Rex", None, "PvP", "", "rex", None, 0).description is None
_bot.PLACE_ID = "123"
assert join_url(456) == "https://www.roblox.com/games/start?placeId=123&userId=456"
print("ok (lfp)")
