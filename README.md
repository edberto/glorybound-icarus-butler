# glorybound-icarus-butler

What it does:
- **Read-only channels:** Public has glorybound-announcements, announcements and rules; Icarus has announcements. Only Administrators (Leader, server admins) can post in them. Posts from followed announcement channels still come through.
- **#honeypot** (outside all categories): the bot posts a warning there. Anyone who posts is kicked, and their messages from the last 5 minutes in every channel are deleted. It does this by banning (which deletes the messages) and unbanning right away, so they can rejoin with an invite. Admins aren't kicked; their message is just deleted. Every kick is logged in `#bot-commands`.
- **Layout:** creates any missing roles, categories and channels, and fixes their permissions. It runs on startup and when you use `/check-server`. It never deletes anything; extra channels are only listed.
- **Join me to create:** every category has one. Joining it makes "<name>'s room", moves you into it and lets you edit that room. Empty rooms are deleted.
- **Roles, every 5 min and when someone joins:** Bloxlink → Roblox ID → Roblox display name → matched against the clan API. Clan members get Officer or Member; everyone else gets Visitor.
- **Monday 21:50 (Jakarta):** a table in `#clan-watcher` sorted by role, then by join date. Each stat shows its value and the change since last week. It is posted as `<date>_partition_N_of_M` messages. If the database is ever lost, the bot rebuilds last week's numbers from the newest complete set.
- **Alerts in `#bot-commands`:** who just got Officer, and any display-name clashes.

## Commands (shown only to Officers and up)
| Command | Does |
|---|---|
| `/check-server` | Checks the layout, fixes it, and lists what changed |
| `/sync-now` | Runs the role sync immediately |
| `/report-now [save]` | Posts the table now. Default is a preview (`_preview_` header) that isn't saved. `save:True` saves it as the snapshot |
| `/bind clan_name member` | **Admins only.** Says which Discord member really owns a clan display name |

---

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
6. Turn on 2FA on the Discord account that owns the application.

### 2. Bloxlink key (3 min)
1. Make sure Bloxlink is in the server (https://blox.link → Add to Discord).
2. Go to https://blox.link/dashboard → choose the server → **Developer / API** section → create a **server API key**. This is `BLOXLINK_KEY`.
3. Members link their Roblox account with Bloxlink's `/verify`. Until they do, they stay Visitor. The bot checks again every 5 min, so they get their role within 5 minutes of verifying.
4. In Bloxlink's own settings, **don't** let Bloxlink assign the Leader, Officer, Member or Visitor roles too; the two bots would fight over them. Bloxlink's "Verified" role and nicknames are fine.

> The bot calls `https://api.blox.link/v4/public/guilds/<server>/discord-to-roblox/<user>`. If Bloxlink's dashboard shows a different URL, update `BLOXLINK_URL` at the top of `bot.py`.

### 2b. Test Bloxlink before deploying (1 min)
Put `GUILD_ID` and `BLOXLINK_KEY` in `.env`, then run `python3 check_bloxlink.py <your discord user id>`. It needs nothing installed. It prints:
1. what Bloxlink returns,
2. your Roblox username and display name,
3. which clan role the bot would give you.

### 3. Oracle VM (15 min)
1. https://cloud.oracle.com → sign up for the Always Free tier.
2. **Compute → Instances → Create instance.**
   - Image: **Ubuntu 24.04**.
   - Shape: `VM.Standard.A1.Flex` (Ampere) with 1 OCPU / 6 GB, or `VM.Standard.E2.1.Micro` if Ampere has no capacity. Both are free and both are plenty.
   - Under SSH keys, upload your public key or download the generated one.
   - Networking: the default VCN is fine. The bot only makes outgoing connections, so **don't open any ports** besides the default SSH (22).
3. When it's running, copy the **Public IP** and connect: `ssh -i <key> ubuntu@<ip>`.
4. On the VM:
   ```bash
   sudo apt update && sudo apt install -y python3-venv unattended-upgrades
   sudo dpkg-reconfigure -plow unattended-upgrades       # automatic security updates
   sudo useradd --system --create-home --home-dir /opt/clanbot clanbot
   ```
5. Copy the files from your computer (run this on your computer, not the VM):
   ```bash
   scp -i <key> bot.py requirements.txt .env.example ubuntu@<ip>:/tmp/
   ```
6. Back on the VM:
   ```bash
   sudo mv /tmp/bot.py /tmp/requirements.txt /tmp/.env.example /opt/clanbot/
   cd /opt/clanbot
   sudo cp .env.example .env && sudo nano .env            # fill in the three values
   sudo python3 -m venv venv && sudo venv/bin/pip install -r requirements.txt
   sudo chown -R clanbot:clanbot /opt/clanbot && sudo chmod 600 .env
   ```
7. Create the service: `sudo nano /etc/systemd/system/clanbot.service` and paste:
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
8. Start it and watch the log:
   ```bash
   sudo systemctl daemon-reload && sudo systemctl enable --now clanbot
   journalctl -u clanbot -f
   ```
   On the first start the log lists every role and channel it created or fixed. If you see `WARNING: drag the bot's role above Leader`, do step 1.5, then run `/check-server`.

To update later: copy the new `bot.py` over, then `sudo systemctl restart clanbot`.

### 4. First-run checks in Discord
1. Look in `#bot-commands`. Each "Gave X Officer" line should be a real officer. If one isn't, run `/bind clan_name:<name> member:@real-person`.
2. Give **Leader** to Arius (and anyone else) by hand.
3. Run `/report-now` (preview) to see the table. The first real Monday report shows `(new)` instead of changes, because there's nothing to compare with yet.

### Data & backups
`clan.db` (SQLite) lives in `/opt/clanbot`. Every night at 21:50 the bot copies it to `backups/clan-Mon.db` … `clan-Sun.db`, keeping 7 days. If the VM disk is lost, the role data rebuilds itself within 5 minutes, and last week's stats are re-read from `#clan-watcher`.

Run the checks: `python3 test_bot.py`
