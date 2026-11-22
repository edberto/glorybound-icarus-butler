# glorybound-icarus-butler

What it does:
- **Read-only channels:** Public has glorybound-announcements, announcements and rules; Icarus has announcements. Only Administrators (Leader, server admins) can post in them. Posts from followed announcement channels still come through.
- **#honeypot** (outside all categories): the bot posts a warning there. Anyone who posts is kicked, and their messages from the last 5 minutes in every channel are deleted. It does this by banning (which deletes the messages) and unbanning right away, so they can rejoin with an invite. Admins aren't kicked; their message is just deleted. Every kick is logged in `#bot-commands`.
- **Names:** channels show with emoji (`🏺 Public`, `💬・general`, `🍯・honeypot` …). Existing channels with the plain names are renamed rather than duplicated. Change the look in `PRETTY` at the top of `bot.py`.
- **Admin category:** Leader and Officers can see and use it. Officers can't create, edit or delete its channels or change their permissions. Everyone else can't see it.
- **Nicknames:** everyone linked with Bloxlink gets their Roblox display name as their server nickname (not the owner or anyone above the bot; Discord forbids it). Turn **off** Bloxlink's own nickname updates so the two bots don't fight.
- **Layout:** creates any missing roles, categories and channels, and fixes their permissions. It runs on startup and when you use `/check-server`. It never deletes anything; extra channels are only listed.
- **Join me to create:** every category has one. Joining it makes "<name>'s room", moves you into it and lets you edit that room. Empty rooms are deleted.
- **Roles, every 5 min and when someone joins:** Bloxlink → Roblox ID → Roblox display name → matched against the clan API. Clan members get Officer or Member; everyone else gets Visitor.
- **Monday 21:50 (Jakarta):** a table in `#clan-watcher` sorted by role, then by join date. Each stat shows its value and the change since last week. It is posted as `<date>_partition_N_of_M` messages. If the database is ever lost, the bot rebuilds last week's numbers from the newest complete set.
- **Log in `#bot-commands`:** every role the bot adds or removes (one message per sync, with the reason), joins, layout fixes on startup and `/check-server`, overrides, honeypot kicks and display-name clashes.

## Commands (shown only to Officers and up; Leader is never touched by the bot, so give it by hand)
| Command | Does |
|---|---|
| `/check-server` | Checks the layout, fixes it, and lists what changed |
| `/sync-now` | Runs the role sync immediately |
| `/report-now [save]` | Posts the table now. Default is a preview (`_preview_` header) that isn't saved. `save:True` saves it as the snapshot |
| `/role-override member role` | **Admins only.** Keeps Officer, Member or Visitor on someone and stops the sync changing them, e.g. guests or allies in Icarus, or someone who can't verify. Never expires on its own. Logged in `#bot-commands` |
| `/role-override-clear member` | **Admins only.** Hands the member back to the clan sync |
| `/role-overrides` | Lists everyone with an override |
| `/lfp mode notes` | Anyone linked with Bloxlink. Posts "**<name>** is looking for a **<mode>** party" in looking-for-party with **Join**, **Roblox profile** and **Close** buttons. Join privately gives the clicker an Open Roblox button and adds them to the card's "Joining" list. Shows live status; closes after 1 hour or when the poster goes offline. One card per person |
| `/bind clan_name member` | **Admins only.** Says which Discord member really owns a clan display name |

---

## /lfp (looking for party)
- Modes: Survival, Ascension Island, PvP, Trials, Weekly Trials, plus free-text notes.
- **Join works for non-friends** only if the poster sets Roblox **Settings → Privacy → Who can join me? → Everyone**. The card says so.
- Join can't follow someone into a private or reserved server (e.g. a dungeon instance). Post while in a public server.
- Set `PLACE_ID` in `.env` (from Glorybound's page URL `roblox.com/games/<PLACE_ID>/...`).
- **Test once:** have a friend post `/lfp` while in game and click Join. If it starts Glorybound but in a different server, Roblox's web link is ignoring the "follow" part. Then:
  1. Create a **public** GitHub repo (e.g. `icarus-join`), add `docs/join.html` from this repo as `join.html`, and turn on **Settings → Pages** (branch `main`, root).
  2. In `.env` set `JOIN_URL=https://<you>.github.io/icarus-join/join.html?placeId={place_id}&userId={user_id}` and restart the bot.

## Manual setup

### 1. Create the Discord bot (5 min)
1. Go to https://discord.com/developers/applications → **New Application** → name it (e.g. "Icarus Bot").
2. **Bot** tab:
   - **Reset Token** → copy it. This is `DISCORD_TOKEN`. Anyone with it controls your server, so never paste it in chat or commit it.
   - Turn **off** "Public Bot", so only you can invite it.
   - Under Privileged Gateway Intents, turn **on** "Server Members Intent". Leave Presence and Message Content off; the bot doesn't need them.
3. **OAuth2 → URL Generator:** scopes `bot` + `applications.commands`, permission **Administrator**. Open the URL and add the bot to the server. The server owner (Arius) or an admin can do this.
   - Why Administrator: Discord only lets a bot grant permissions it has itself. Officers get "everything except Administrator", so the bot needs all of it.
4. In Discord, turn on **User Settings → Advanced → Developer Mode**. Right-click the server icon → **Copy Server ID**. This is `GUILD_ID`.
5. **Server Settings → Roles:** drag the bot's role (named after the bot) to the **very top**, above Leader. Without this it can't assign roles. The bot will create Leader, Officer, Member and Visitor itself on first start.
6. **Bot → Icon:** upload `assets/avatar.png` (1024×1024). `assets/avatar.svg` is the editable source.
7. Turn on 2FA on the Discord account that owns the application.

### 1b. Server icon
**Server Settings → Overview → Server Icon:** upload `assets/icarus-logo.png` (1024×1024; `assets/icarus-logo.svg` is the editable source).

### 2. Bloxlink key (3 min)
1. Make sure Bloxlink is in the server (https://blox.link → Add to Discord).
2. Go to https://blox.link/dashboard → choose the server → **Developer / API** section → create a **server API key**. This is `BLOXLINK_KEY`.
3. Members link their Roblox account with Bloxlink's `/verify`. Until they do, they stay Visitor. When Bloxlink gives them its Verified role or changes their nickname, the bot looks them up straight away.
4. In Bloxlink's own settings, **don't** let Bloxlink assign the Leader, Officer, Member or Visitor roles, and turn **off** its nickname updates; the bot does both. Keep Bloxlink's "Verified" role on: that's how the bot notices new verifications instantly.
5. **Free tier quota.** Bloxlink announced the free server API with 500 lookups per day (unlimited with API Premium, $2.99/month at launch). The bot stays under this by itself:
   - Linked members are re-checked once a day.
   - Members who haven't verified are re-checked after 5 minutes, then 10, then 20, and so on, until it's once a day.
   - Joins and verifications are checked immediately.
   - It stops at 450 lookups a day (`BLOXLINK_DAILY_LIMIT`).

   A 100-person server uses about 100–150 lookups a day. If you buy API Premium, raise `BLOXLINK_DAILY_LIMIT`.

> The bot calls `https://api.blox.link/v4/public/guilds/<server>/discord-to-roblox/<user>`. If Bloxlink's dashboard shows a different URL, update `BLOXLINK_URL` at the top of `bot.py`.

### 2b. Test Bloxlink before deploying (1 min)
Put `GUILD_ID` and `BLOXLINK_KEY` in `.env`, then run `python3 check_bloxlink.py <your discord user id>`. It needs nothing installed. It prints:
1. what Bloxlink returns,
2. your Roblox username and display name,
3. which clan role the bot would give you.

### 3. Google Cloud VM (free e2-micro, ~20 min)
1. Sign up at https://cloud.google.com/free (card needed for verification). Before the 90-day trial ends, click **Activate full account**, or Google deletes the trial's resources. Staying inside the free limits costs nothing.
2. In https://console.cloud.google.com create a project `icarus-butler`. Under **Billing → Budgets & alerts**, set a 1 USD budget so you're emailed if anything ever costs money.
3. **Compute Engine → VM instances → Create instance:**
   - Region us-west1, us-central1 or us-east1 (only these are free).
   - Machine **e2-micro**.
   - Ubuntu 24.04 LTS on a 30 GB **Standard persistent disk** (the default Balanced disk isn't free).
   - No HTTP/HTTPS firewall rules.
4. Click **SSH** on the VM row (browser terminal). Use **Upload file** to send `bot.py`, `requirements.txt` and `.env`. Then run:
   ```bash
   sudo apt update && sudo apt install -y python3-venv unattended-upgrades
   sudo dpkg-reconfigure -plow unattended-upgrades       # automatic security updates
   sudo useradd --system --create-home --home-dir /opt/clanbot clanbot
   sudo mv ~/bot.py ~/requirements.txt ~/.env /opt/clanbot/
   cd /opt/clanbot
   sudo python3 -m venv venv && sudo venv/bin/pip install -r requirements.txt
   sudo chown -R clanbot:clanbot /opt/clanbot && sudo chmod 600 .env
   ```
5. Create the service: `sudo nano /etc/systemd/system/clanbot.service` and paste:
   ```ini
   [Unit]
   Description=Icarus clan bot
   After=network-online.target
   Wants=network-online.target

   [Service]
   User=clanbot
   WorkingDirectory=/opt/clanbot
   EnvironmentFile=/opt/clanbot/.env
   ExecStart=/opt/clanbot/venv/bin/python bot.py
   Restart=always
   RestartSec=10
   NoNewPrivileges=true
   ProtectSystem=strict
   ReadWritePaths=/opt/clanbot
   PrivateTmp=true

   [Install]
   WantedBy=multi-user.target
   ```
6. Start it and watch the log:
   ```bash
   sudo systemctl daemon-reload && sudo systemctl enable --now clanbot
   journalctl -u clanbot -f
   ```
   On the first start the log lists every role and channel it created or fixed. If you see `WARNING: drag the bot's role above Leader`, do step 1.5, then run `/check-server`.

To update later: upload the new `bot.py` (and `requirements.txt`) via **SSH → Upload file**, then run `sudo mv ~/bot.py ~/requirements.txt /opt/clanbot/ && sudo chown -R clanbot:clanbot /opt/clanbot && sudo /opt/clanbot/venv/bin/pip install -r /opt/clanbot/requirements.txt && sudo systemctl restart clanbot`.

### 4. First-run checks in Discord
1. Look in `#bot-commands`. Each "Gave X Officer" line should be a real officer. If one isn't, run `/bind clan_name:<name> member:@real-person`.
2. Give **Leader** to Arius (and anyone else) by hand.
3. Run `/report-now` (preview) to see the table. The first real Monday report shows `(new)` instead of changes, because there's nothing to compare with yet.

### Data & backups
`clan.db` (SQLite) lives in `/opt/clanbot`. Every night at 21:50 the bot copies it to `backups/clan-Mon.db` … `clan-Sun.db`, keeping 7 days. If the VM disk is lost, the role data rebuilds itself within 5 minutes, and last week's stats are re-read from `#clan-watcher`.

Run the checks: `python3 test_bot.py`
