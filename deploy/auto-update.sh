#!/bin/sh
# Auto-deploy, run every 5 minutes by clanbot-update.timer (install: sudo sh setup-github.sh auto).
# A new commit on main goes live only if test_bot.py passes; if the bot then won't stay up, it rolls back.
# Any problem is written to deploy-note.txt, which the bot posts in #bot-commands.
set -u
APP=/opt/clanbot/app
CAND=/opt/clanbot/candidate
PY=/opt/clanbot/venv/bin/python
PIP=/opt/clanbot/venv/bin/pip
NOTE=/opt/clanbot/deploy-note.txt
FAILED=/opt/clanbot/.deploy-failed
asbot() { sudo -u clanbot "$@"; }
note() { printf '%s\n' "$1" | tee -a "$NOTE"; chown clanbot:clanbot "$NOTE"; }

asbot git -C "$APP" fetch -q origin main || { echo "git fetch failed; trying again next time"; exit 0; }
OLD=$(asbot git -C "$APP" rev-parse HEAD)
NEW=$(asbot git -C "$APP" rev-parse origin/main)
[ "$OLD" = "$NEW" ] && exit 0
[ -f "$FAILED" ] && [ "$(cat "$FAILED")" = "$NEW" ] && exit 0  # already rejected this commit; wait for a newer one
DESC=$(asbot git -C "$APP" log -1 --format='%h %s' "$NEW")
CURRENT=$(asbot git -C "$APP" log -1 --format='%h %s' HEAD)

# 1. Check the new version in a separate folder before touching the running one.
asbot git -C "$APP" worktree remove --force "$CAND" 2>/dev/null; rm -rf "$CAND"
asbot git -C "$APP" worktree prune
asbot git -C "$APP" worktree add -q --detach "$CAND" "$NEW"
$PIP install -q -r "$CAND/requirements.txt"
if ! OUT=$(cd "$CAND" && asbot "$PY" test_bot.py 2>&1); then
    echo "$NEW" > "$FAILED"
    asbot git -C "$APP" worktree remove --force "$CAND"
    note "Auto-update to \`$DESC\` skipped: its checks failed. Still running \`$CURRENT\`.
$(printf '%s\n' "$OUT" | tail -n 8)"
    exit 0
fi
asbot git -C "$APP" worktree remove --force "$CAND"

# 2. Switch and restart.
if ! asbot git -C "$APP" merge -q --ff-only "$NEW"; then
    echo "$NEW" > "$FAILED"
    note "Auto-update to \`$DESC\` skipped: GitHub history was rewritten. Run deploy/update.sh by hand."
    exit 0
fi
chown -R clanbot:clanbot /opt/clanbot
BEFORE=$(systemctl show -p NRestarts --value clanbot)
systemctl restart clanbot
sleep 45

# 3. Still up without crash-restarting? Otherwise roll back.
if [ "$(systemctl is-active clanbot)" != active ] || [ "$(systemctl show -p NRestarts --value clanbot)" != "$BEFORE" ]; then
    echo "$NEW" > "$FAILED"
    asbot git -C "$APP" reset -q --hard "$OLD"
    $PIP install -q -r "$APP/requirements.txt"
    chown -R clanbot:clanbot /opt/clanbot
    note "Auto-update to \`$DESC\` crashed on start, so it was rolled back to \`$CURRENT\`. Details: journalctl -u clanbot -n 50"
    systemctl restart clanbot
fi
