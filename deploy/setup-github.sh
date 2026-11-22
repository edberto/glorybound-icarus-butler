#!/bin/sh
# One-time: switch the server from uploaded files to a GitHub checkout.
#   Step 1:  sudo sh setup-github.sh key     -> prints a key; add it on GitHub as a read-only deploy key
#   Step 2:  sudo sh setup-github.sh clone   -> clones the repo and points the service at it
# Optional: sudo sh /opt/clanbot/app/deploy/setup-github.sh auto  -> check GitHub every 5 min and deploy new commits
#           sudo sh /opt/clanbot/app/deploy/setup-github.sh manual -> turn that off again
set -e
REPO=git@github.com:edberto/glorybound-icarus-butler.git
case "$1" in
  key)
    apt-get install -y -q git >/dev/null
    sudo -u clanbot mkdir -p /opt/clanbot/.ssh
    [ -f /opt/clanbot/.ssh/id_ed25519 ] || sudo -u clanbot ssh-keygen -q -t ed25519 -N "" -C icarus-butler-server -f /opt/clanbot/.ssh/id_ed25519
    echo; echo "Add this as a deploy key (read-only) on GitHub:"; echo
    cat /opt/clanbot/.ssh/id_ed25519.pub ;;
  clone)
    sudo -u clanbot env GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" git clone -q "$REPO" /opt/clanbot/app
    /opt/clanbot/venv/bin/pip install -q -r /opt/clanbot/app/requirements.txt
    rm -f /opt/clanbot/bot.py /opt/clanbot/requirements.txt
    sed -i 's#^ExecStart=.*#ExecStart=/opt/clanbot/venv/bin/python /opt/clanbot/app/bot.py#' /etc/systemd/system/clanbot.service
    chown -R clanbot:clanbot /opt/clanbot
    systemctl daemon-reload && systemctl restart clanbot
    echo "Done. The bot now runs from GitHub. To update later: sudo sh /opt/clanbot/app/deploy/update.sh" ;;
  auto)
    cat > /etc/systemd/system/clanbot-update.service <<'UNIT'
[Unit]
Description=Deploy new Icarus bot commits from GitHub (tested, with rollback)

[Service]
Type=oneshot
# run a copy, because the update itself may replace the script
ExecStart=/bin/sh -c 'cp /opt/clanbot/app/deploy/auto-update.sh /run/clanbot-auto-update.sh && exec /bin/sh /run/clanbot-auto-update.sh'
UNIT
    cat > /etc/systemd/system/clanbot-update.timer <<'UNIT'
[Unit]
Description=Check GitHub for Icarus bot updates every 5 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
UNIT
    systemctl daemon-reload && systemctl enable --now clanbot-update.timer
    echo "Auto-update is on: GitHub is checked every 5 minutes. Log: journalctl -u clanbot-update -n 50" ;;
  manual)
    systemctl disable --now clanbot-update.timer && echo "Auto-update is off. Update by hand with deploy/update.sh" ;;
  *) echo "usage: sudo sh setup-github.sh key|clone|auto|manual"; exit 1 ;;
esac
