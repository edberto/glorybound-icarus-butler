"""Test Bloxlink -> Roblox -> clan matching for one Discord user, without starting the bot.

Usage:  python3 check_bloxlink.py <discord_user_id>
Reads GUILD_ID and BLOXLINK_KEY from .env (or the environment). Python stdlib only.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

if Path(".env").exists():
    for line in Path(".env").read_text().splitlines():
        k, _, v = line.partition("=")
        if v and not k.startswith("#"):
            os.environ.setdefault(k.strip(), v.strip())

CLAN_URL = os.environ.get("CLAN_URL", "https://glorybound-clan-network.vercel.app/api/clans/q7vd1z_toioa4")


def call(url, headers=None, body=None):
    req = urllib.request.Request(url, headers={"User-Agent": "clan-bot-check", **(headers or {})},
                                 data=json.dumps(body).encode() if body else None)
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


user = sys.argv[1] if len(sys.argv) > 1 else sys.exit(__doc__)
guild, key = os.environ.get("GUILD_ID"), os.environ.get("BLOXLINK_KEY")
if not guild or not key:
    sys.exit("Set GUILD_ID and BLOXLINK_KEY in .env first")

status, data = call(f"https://api.blox.link/v4/public/guilds/{guild}/discord-to-roblox/{user}", {"Authorization": key})
print(f"1. Bloxlink: HTTP {status} -> {data}")
rid = data.get("robloxID") or data.get("robloxId") if isinstance(data, dict) else None
if not rid:
    sys.exit("   Not linked (or bad key/guild). The bot would give Visitor.")

status, data = call("https://users.roblox.com/v1/users", body={"userIds": [int(rid)], "excludeBannedUsers": False})
u = data["data"][0]
print(f"2. Roblox: id {u['id']}, username @{u['name']}, display name '{u['displayName']}'")

status, data = call(CLAN_URL)
hits = [m for m in data["clan"]["members"] if m["name"] == u["displayName"]]
if hits:
    print(f"3. Clan: matched '{hits[0]['name']}' as {hits[0]['role']} -> bot would give that role")
else:
    print(f"3. Clan: no member named '{u['displayName']}' -> bot would give Visitor")
