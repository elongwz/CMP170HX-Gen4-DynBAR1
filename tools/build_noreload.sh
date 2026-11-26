#!/bin/bash
# 安全重编 cmp-unlocker 驱动：屏蔽 build.sh 末尾的自动热重载
# （热重载在 BAR1/GSP 受损时会把整机卡死，只能物理断电）
# 用法: sudo bash build_noreload.sh /path/to/cmpunlocker-v0.5-xxx
set -e
DRV=${1:-/home/gis/p2p/cmpunlocker-v0.5-oc-80g-gen3}
mkdir -p /tmp/shim
printf '#!/bin/sh\nexit 0\n' > /tmp/shim/modprobe
printf '#!/bin/sh\nexit 0\n' > /tmp/shim/systemctl
chmod +x /tmp/shim/modprobe /tmp/shim/systemctl
printf '%s\n' "$SUDO_PW" > /tmp/.pw; chmod 600 /tmp/.pw
cd "$DRV/driver"
setsid nohup sudo -S env PATH=/tmp/shim:$PATH bash build.sh \
  > /tmp/build.log 2>&1 < /tmp/.pw &
echo "build started; tail -f /tmp/build.log"
