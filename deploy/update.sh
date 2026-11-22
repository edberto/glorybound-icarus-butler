#!/bin/sh
# Update the bot on the server from GitHub:  sudo sh /opt/clanbot/app/deploy/update.sh
set -e
sudo -u clanbot git -C /opt/clanbot/app pull --ff-only
/opt/clanbot/venv/bin/pip install -q -r /opt/clanbot/app/requirements.txt
chown -R clanbot:clanbot /opt/clanbot
systemctl restart clanbot
echo "Updated to: $(sudo -u clanbot git -C /opt/clanbot/app log -1 --format='%h %s')"
