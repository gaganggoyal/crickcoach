#!/usr/bin/env bash
# Put VANSH on the shared VPS behind https://vansh.best.
#
#   DEPLOY_HOST=root@203.0.113.10 ./deploy/vps.sh     deploy (safe to run again; it updates in place)
#
# Settings for your own server can live in deploy/vps.local.env, which git ignores:
#   DEPLOY_HOST=root@203.0.113.10
#   DOMAIN=vansh.example.com           the site's domain (default vansh.best)
#   CADDY_FILE=/srv/caddy/Caddyfile    the Caddyfile on the host
#   CADDY_CONTAINER=caddy              the Docker container that runs Caddy
#   CADDY_CONFIG=/etc/caddy/Caddyfile  where that container sees the Caddyfile
#
# Layout on the box:
#   /var/www/vansh        the code and its virtualenv, owned by root (the app cannot rewrite itself)
#   /var/lib/vansh        sessions, video, the lane calibration and the machine queue (user vansh)
#   /etc/vansh/vansh.env  settings, including the coach key (made once, root only, never overwritten)
#
# Caddy runs in Docker and owns 80/443 for every site on the box. It reaches
# VANSH on the Docker bridge gateway, and ufw only lets the bridge in.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$ROOT/deploy/vps.local.env" ]; then
  # shellcheck disable=SC1091
  . "$ROOT/deploy/vps.local.env"
fi
HOST="${DEPLOY_HOST:-}"
DOMAIN="${DOMAIN:-vansh.best}"
APP="${APP:-/var/www/vansh}"
DATA="${DATA:-/var/lib/vansh}"
ENV_FILE="${ENV_FILE:-/etc/vansh/vansh.env}"
PORT="${PORT:-8094}"
CADDY_FILE="${CADDY_FILE:-/etc/caddy/Caddyfile}"
CADDY_CONTAINER="${CADDY_CONTAINER:-caddy}"
CADDY_CONFIG="${CADDY_CONFIG:-/etc/caddy/Caddyfile}"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=20 -o ServerAliveCountMax=6"
if [ -z "$HOST" ]; then
  echo "Say which server: DEPLOY_HOST=root@your-server ./deploy/vps.sh, or put DEPLOY_HOST in deploy/vps.local.env." >&2
  exit 2
fi

echo "→ reaching ${HOST}"
if ! $SSH "$HOST" true; then
  echo "Cannot reach ${HOST} over SSH. If port 22 is refused while the websites work, fail2ban has" >&2
  echo "probably banned this computer's address (logging in without root@ counts as a failure)." >&2
  echo "From another network, such as a phone hotspot, or the host's console, run:" >&2
  echo "    fail2ban-client unban <this computer's public IP>" >&2
  exit 1
fi

echo "→ uploading the code to ${APP}"
$SSH "$HOST" "mkdir -p ${APP}"
rsync -az --delete -e "$SSH" \
  --exclude .git --exclude .venv --exclude '__pycache__' --exclude '*.pyc' --exclude .DS_Store \
  --exclude /data --exclude /bench --exclude /tests --exclude /deploy --exclude /.claude \
  "$ROOT/" "${HOST}:${APP}/"

echo "→ installing and starting VANSH"
$SSH "$HOST" "APP=${APP} DATA=${DATA} ENV_FILE=${ENV_FILE} PORT=${PORT} bash -s" <<'REMOTE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

if ! python3 -c 'import ensurepip, venv' >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq python3-venv >/dev/null
fi
python3 --version

id vansh >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin vansh
mkdir -p "$DATA" /etc/vansh

# An earlier layout kept the data inside the code folder. Move it out once.
if [ -d "$APP/data" ]; then
  for part in sessions machine lane.json secret.key; do
    if [ -e "$APP/data/$part" ] && [ ! -e "$DATA/$part" ]; then mv "$APP/data/$part" "$DATA/"; fi
  done
  rm -rf "$APP/data"
fi
chown -R vansh:vansh "$DATA"
chmod 750 "$DATA"

if [ ! -x "$APP/.venv/bin/python" ]; then
  python3 -m venv "$APP/.venv"
fi
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
"$APP/.venv/bin/python" - <<'PY'
import subprocess, imageio_ffmpeg, cv2, numpy
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-version"], check=True, capture_output=True)
print("ffmpeg, OpenCV", cv2.__version__, "and numpy", numpy.__version__, "are ready")
PY
chown -R root:root "$APP"
chmod -R go-w "$APP"

if [ ! -f "$ENV_FILE" ]; then
  KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
  ( umask 077
    cat > "$ENV_FILE" <<EOF
# VANSH settings. After editing: systemctl restart vansh
VANSH_DATA=$DATA
# The coach signs in with this key. Anyone with it can see sessions and drive the machine queue.
VANSH_COACH_KEY=$KEY
# Uncomment when the edge bridge runs at the nets (python -m crickcoach.edge machine --armed):
# VANSH_MACHINE=edge
# Refuse uploads once sessions use this much disk:
VANSH_MAX_GB=20
EOF
  )
  echo "Made ${ENV_FILE} with a new coach key. Read it with: cat ${ENV_FILE}"
fi
chmod 600 "$ENV_FILE"

# The port must be free, or held by VANSH itself.
holder="$(ss -lntpH "sport = :$PORT" || true)"
if [ -n "$holder" ] && ! printf '%s' "$holder" | grep -q 'python'; then
  echo "port $PORT is used by something else:" >&2
  printf '%s\n' "$holder" >&2
  exit 1
fi

cat > /etc/systemd/system/vansh.service <<UNIT
[Unit]
Description=VANSH net coach (vansh.best)
After=network-online.target docker.service
Wants=network-online.target

[Service]
User=vansh
Group=vansh
WorkingDirectory=$APP
EnvironmentFile=$ENV_FILE
ExecStart=$APP/.venv/bin/python -m crickcoach --host 0.0.0.0 --port $PORT --proxy
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DATA
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
MemoryMax=3G
TasksMax=256

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable vansh >/dev/null
systemctl restart vansh

for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/tmp/vansh-health.json 2>/dev/null; then
    echo "✓ VANSH answers on port ${PORT}"
    cat /tmp/vansh-health.json; echo
    break
  fi
  if [ "$i" = 30 ]; then
    echo "✗ VANSH did not come up" >&2
    journalctl -u vansh -n 60 --no-pager >&2
    exit 1
  fi
  sleep 1
done

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  if ! ufw status | grep -E "^${PORT}/tcp +ALLOW +172\.16\.0\.0/12" >/dev/null; then
    ufw allow from 172.16.0.0/12 to any port "$PORT" proto tcp comment 'vansh: docker bridge to host' >/dev/null
    echo "✓ ufw lets the Docker bridge reach port ${PORT}"
  fi
fi
REMOTE

echo "→ adding ${DOMAIN} to Caddy"
$SSH "$HOST" "CADDY_FILE=${CADDY_FILE} CADDY_CONTAINER=${CADDY_CONTAINER} CADDY_CONFIG=${CADDY_CONFIG} PORT=${PORT} DOMAIN=${DOMAIN} bash -s" <<'REMOTE'
set -euo pipefail
gateway="$(docker inspect "$CADDY_CONTAINER" -f '{{range .NetworkSettings.Networks}}{{.Gateway}} {{end}}' | awk '{print $1}')"
gateway="${gateway:-172.18.0.1}"
begin="# >>> ${DOMAIN} (managed by Robocric deploy/vps.sh)"
end="# <<< ${DOMAIN}"
block="$begin
${DOMAIN} {
	encode gzip
	reverse_proxy ${gateway}:${PORT}
}

www.${DOMAIN} {
	redir https://${DOMAIN}{uri} permanent
}
$end"
domain_re="$(printf '%s' "$DOMAIN" | sed 's/\./\\./g')"
if grep -qE "^[[:space:]]*(www\\.)?${domain_re}[[:space:]]*\\{" "$CADDY_FILE" && ! grep -qF "$begin" "$CADDY_FILE"; then
  echo "The Caddyfile already has a ${DOMAIN} block that this script did not write. Leaving it alone." >&2
  grep -nE "${domain_re}" "$CADDY_FILE" >&2
  exit 1
fi
backup="${CADDY_FILE}.bak-vansh-$(date +%Y%m%d%H%M%S)"
cp "$CADDY_FILE" "$backup"
python3 - "$CADDY_FILE" "$begin" "$end" "$block" <<'PY'
import sys
path, begin, end, block = sys.argv[1:5]
text = open(path).read()
if begin in text and end in text:
    head, rest = text.split(begin, 1)
    _, tail = rest.split(end, 1)
    text = head.rstrip("\n") + "\n\n" + block + tail
else:
    text = text.rstrip("\n") + "\n\n" + block + "\n"
open(path, "w").write(text)
PY
if ! docker exec "$CADDY_CONTAINER" caddy validate --config "$CADDY_CONFIG" --adapter caddyfile >/tmp/caddy-validate.txt 2>&1; then
  echo "✗ Caddy rejected the change. Restoring the backup." >&2
  tail -20 /tmp/caddy-validate.txt >&2
  cp "$backup" "$CADDY_FILE"
  exit 1
fi
docker exec "$CADDY_CONTAINER" caddy reload --config "$CADDY_CONFIG" --adapter caddyfile
echo "✓ Caddy reloaded (proxying to ${gateway}:${PORT}). Backup: ${backup}"
REMOTE

echo "→ checking https://${DOMAIN}"
for i in $(seq 1 30); do
  if curl -fsS --max-time 20 "https://${DOMAIN}/api/health"; then
    echo
    echo "Done. The coach key is in ${ENV_FILE} on the server: ${SSH} ${HOST} cat ${ENV_FILE}"
    exit 0
  fi
  sleep 3
done
echo "${DOMAIN} did not answer yet. Certificates can take a minute; check: docker logs ${CADDY_CONTAINER} --tail 50" >&2
exit 1
