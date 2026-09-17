#!/usr/bin/env bash
# WordType 一键部署脚本 —— 在目标 Ubuntu 服务器上以 root 执行
# 用法: 解压 wordtype-deploy.tar.gz 后，在解压目录执行 sudo bash deploy/deploy.sh
# （脚本向上级目录找 backend/ 和 frontend/dist/）
set -euo pipefail

APP_DIR=/opt/wordtype
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONF_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> 1/7 系统依赖"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip nginx rsync >/dev/null

echo "==> 2/7 专用系统用户"
id -u wordtype >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin wordtype

echo "==> 3/7 同步代码"
mkdir -p "$APP_DIR"
rsync -a --delete "$SRC_DIR/backend/"  "$APP_DIR/backend/"  --exclude venv --exclude __pycache__ --exclude .venv --exclude "*.log"
mkdir -p "$APP_DIR/frontend"
rsync -a --delete "$SRC_DIR/frontend/dist/" "$APP_DIR/frontend/dist/"
mkdir -p "$APP_DIR/backend/data" "$APP_DIR/backend/backup"

echo "==> 4/7 Python 虚拟环境"
cd "$APP_DIR/backend"
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install --upgrade pip -q
./venv/bin/pip install -r requirements.txt -q

echo "==> 5/7 权限"
chown -R wordtype:wordtype "$APP_DIR"

echo "==> 6/7 systemd 服务"
cp "$CONF_DIR/wordtype.service" /etc/systemd/system/wordtype.service
systemctl daemon-reload
systemctl enable --now wordtype
sleep 2
systemctl is-active wordtype >/dev/null || { journalctl -u wordtype -n 30 --no-pager; exit 1; }

echo "==> 7/7 Nginx"
cp "$CONF_DIR/nginx-wordtype.conf" /etc/nginx/sites-available/wordtype
ln -sf /etc/nginx/sites-available/wordtype /etc/nginx/sites-enabled/wordtype
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo
echo "✅ 部署完成"
echo "   管理员账号: 见 $APP_DIR/backend/.env 中的 ADMIN_USERNAME / ADMIN_PASSWORD"
echo "   访问地址:   http://<服务器公网IP>/"
echo "   服务日志:   journalctl -u wordtype -f"
