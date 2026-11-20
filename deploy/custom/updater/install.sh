#!/bin/sh
# 在 Linux 宿主机以 root 安装固定用途升级器。
set -eu
[ "$(id -u)" = 0 ] || { printf '%s\n' '请使用 sudo sh deploy/custom/updater/install.sh'; exit 1; }
[ "$(uname -s)" = Linux ] || { printf '%s\n' '升级服务仅支持 Linux'; exit 1; }
[ -x /usr/bin/python3 ] || { printf '%s\n' '请先安装 /usr/bin/python3'; exit 1; }
command -v docker >/dev/null
command -v systemctl >/dev/null
/usr/bin/python3 -c 'import sys; assert sys.version_info >= (3, 9), "需要 Python 3.9 或更新版本"'
/usr/bin/python3 - <<'PY'
import json
from pathlib import Path
state = Path('/var/lib/sub2api-updater/status.json')
if state.exists() and json.loads(state.read_text()).get('phase') in {'queued', 'pulling', 'recreating', 'checking'}:
    raise SystemExit('存在未结束的升级任务，请检查任务状态后再安装或重启升级服务')
PY
task_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
[ -f "$task_dir/.env" ] || { printf '%s\n' '请先配置 deploy/custom/.env'; exit 1; }
docker compose --project-directory "$task_dir" --env-file "$task_dir/.env" -f "$task_dir/compose.yml" config --quiet
install -d -m 755 /opt/sub2api-updater
install -m 644 "$task_dir/updater/updater.py" /opt/sub2api-updater/updater.py
install -d -m 700 /var/lib/sub2api-updater
# 目录由 systemd 保留，重启升级器时容器挂载仍指向同一个目录。
install -d -m 755 /run/sub2api-updater
if [ ! -f /etc/sub2api-updater.env ]; then
    printf 'SUB2API_DEPLOY_DIR="%s"\n' "$task_dir" > /etc/sub2api-updater.env
    chmod 600 /etc/sub2api-updater.env
fi
install -m 644 "$task_dir/updater/sub2api-updater.service" /etc/systemd/system/sub2api-updater.service
systemctl daemon-reload
systemctl enable --now sub2api-updater
systemctl restart sub2api-updater
printf '%s\n' '升级服务已安装；首次请重新部署应用镜像，使新接口和 socket 挂载生效。'
