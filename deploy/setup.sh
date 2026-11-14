#!/bin/sh
set -eu
REPO=git@github.com:mankidev/aimusic-blocklist.git
id aimusic >/dev/null 2>&1 || useradd --system --home-dir /var/lib/aimusic --create-home --shell /usr/sbin/nologin aimusic
cd /var/lib/aimusic
[ -f deploy_key ] || sudo -u aimusic ssh-keygen -q -t ed25519 -N "" -C aimusic-crawler -f deploy_key
if [ ! -d repo ]; then
  sudo -u aimusic env GIT_SSH_COMMAND="ssh -i /var/lib/aimusic/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" git clone -q "$REPO" repo
  sudo -u aimusic git -C repo config user.name aimusic-crawler
  sudo -u aimusic git -C repo config user.email aimusic-crawler@users.noreply.github.com
fi
install -m 644 repo/deploy/aimusic.service repo/deploy/aimusic.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now aimusic.timer
