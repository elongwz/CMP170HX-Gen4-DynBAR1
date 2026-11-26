#!/bin/bash
# 已解锁卡的运行态验收：逐项检查并打印 PASS/FAIL
#
# 用法:
#   sudo bash tools/bringup-verify.sh [BDF]
#   BDF 默认 0000:0b:00.0
#
# 退出码: 0 = 全部通过, 1 = 有失败项

BDF="${1:-$(lspci -n 2>/dev/null | awk '$3=="10de:20c2"{print "0000:"$1; exit}')}"
BDF="${BDF:-0000:0b:00.0}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
PASS=0; FAIL=0; WARN=0

ok()   { echo "  ✅ $1"; PASS=$((PASS+1)); }
bad()  { echo "  ❌ $1"; FAIL=$((FAIL+1)); }
warn() { echo "  ⚠️  $1"; WARN=$((WARN+1)); }
hdr()  { echo; echo "── $1 ──"; }

hdr "1. 设备与驱动"

if lspci -nn 2>/dev/null | grep -q '10de:20c2'; then
    ok "CMP 170HX 在总线上"
else
    bad "找不到 10de:20c2"; echo "     检查卡是否插好 / 是否被其他驱动占用"
fi

if lsmod 2>/dev/null | grep -q '^nvidia '; then
    ok "nvidia 驱动已加载（$(lsmod | grep -cE '^nvidia') 个模块）"
else
    bad "nvidia 驱动未加载"; echo "     看: dmesg | tail -20"
fi

hdr "2. 显存（熔丝是否生效）"

MEM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
if [ "$MEM" = "81920" ]; then
    ok "显存 81920 MiB（80 GiB）"
elif [ -n "$MEM" ]; then
    bad "显存 $MEM MiB，期望 81920"
    echo "     8 GB 左右 → 驱动用了错误的 profile（需 CMPUNLOCKER_CARD_PROFILE=mixed）"
    echo "     64 GB 左右 → 只解锁了 64GB 档"
else
    bad "nvidia-smi 读不到显存"
fi

hdr "3. PCIe 链路（硬件真实状态）"

LNK=$(lspci -vv -s "$BDF" 2>/dev/null | grep 'LnkSta:' | head -1)
SPEED=$(echo "$LNK" | grep -oE 'Speed [0-9.]+GT/s' | awk '{print $2}')
WIDTH=$(echo "$LNK" | grep -oE 'Width x[0-9]+' | awk '{print $2}')

if [ "$SPEED" = "16GT/s" ] && [ "$WIDTH" = "x16" ]; then
    ok "LnkSta: 16GT/s x16（Gen4）"
else
    bad "LnkSta: ${SPEED:-?} ${WIDTH:-?}，期望 16GT/s x16"
    echo "     修复: 重编驱动（Gen4 由驱动 post-init 自动完成），见 BRINGUP.md §3"
fi

hdr "4. NVML 视图（仅供参考，会误导）"

GEN=$(nvidia-smi --query-gpu=pcie.link.gen.current --format=csv,noheader 2>/dev/null | tr -d ' ')
DMX=$(nvidia-smi -q 2>/dev/null | grep -A5 'PCIe Generation' | grep 'Device Max' | grep -oE '[0-9]+')
if [ "$GEN" = "3" ]; then
    warn "nvidia-smi 报 Gen3 —— 这是 GSP 固件对 CMP 的身份认知，改不掉也不用改"
    [ "$DMX" = "4" ] && echo "     但 Device Max=$DMX（真实能力），且 LnkSta 已是 16GT/s，功能正常"
else
    ok "nvidia-smi gen.current=$GEN"
fi

hdr "5. SM 数量"

BENCH=""
for cand in "$HOME/bin/nvidia_bench" /home/*/bin/nvidia_bench; do
    [ -x "$cand" ] && { BENCH="$cand"; break; }
done
if [ -n "$BENCH" ]; then
    SMS=$(timeout 120 "$BENCH" 2>/dev/null | grep -E '^\s*SMs:' | grep -oE '[0-9]+' | head -1)
    if [ "$SMS" = "126" ]; then ok "SM = 126"
    elif [ -n "$SMS" ]; then bad "SM = $SMS，期望 126（GPC/TPC 熔丝未全解）"
    else warn "nvidia_bench 未输出 SM 数"; fi
else
    warn "找不到 nvidia_bench，跳过 SM 检查"
    echo "     也可用 CUDA 读取 cuDeviceGetAttribute(...,16)"
fi

hdr "6. 熔丝状态（只读）"

if [ "$(id -u)" = "0" ]; then
    FBPA=$(CMPROOT="$ROOT" python3 - "$BDF" <<'PY' 2>/dev/null
import os, sys
sys.path.insert(0, os.path.join(os.environ["CMPROOT"], "tools"))
try:
    import fuse_rw as F
    F.restore(sys.argv[1], wait_s=1.0)
    with F.Bar0(sys.argv[1]) as b:
        print(f"{b.rd32(0x00820368):08x}")
except Exception:
    print("")
PY
)
    if [ "$FBPA" = "00c03000" ]; then ok "FBPA_DISABLE = 0x00c03000（20 个 FBPA = 80 GB）"
    elif [ -n "$FBPA" ]; then bad "FBPA_DISABLE = 0x$FBPA，期望 0x00c03000（卡未解锁？）"
    else warn "读不到熔丝（BAR0 不可访问）"; fi
else
    warn "需要 root 才能读熔丝，跳过（sudo 重跑）"
fi

hdr "7. PCIe 带宽（受主机内存影响）"

if [ -f "$ROOT/tools/pcie_bandwidth.py" ]; then
    BW=$(timeout 180 python3 "$ROOT/tools/pcie_bandwidth.py" --mb 256 --iters 10 2>/dev/null | grep H2D | grep -oE '[0-9]+\.[0-9]+')
    if [ -n "$BW" ]; then
        if   awk "BEGIN{exit !($BW >= 24)}"; then ok "H2D = $BW GB/s（Gen4 x16 满速）"
        elif awk "BEGIN{exit !($BW >= 13)}"; then
            warn "H2D = $BW GB/s（Gen4 但被主机内存限制）"
            echo "     4 条内存约 15、2 条约 26 GB/s —— 这是主机侧瓶颈，不是显卡"
        else bad "H2D = $BW GB/s，偏低"; fi
    else
        warn "带宽测不出来（libcuda 不可用？）"
    fi
else
    warn "找不到 tools/pcie_bandwidth.py"
fi

hdr "8. Gen4 是否由驱动自动完成"

if [ -e /etc/systemd/system/cmpunlocker-gen4.service ] || \
   systemctl is-enabled cmpunlocker-gen4.service >/dev/null 2>&1; then
    warn "检测到旧的 cmpunlocker-gen4 服务/模块方案"
    echo "     现在 Gen4 已由驱动 post-init 完成，不再需要它；可以删除："
    echo "       sudo systemctl disable --now cmpunlocker-gen4.service"
    echo "       sudo rm -f /usr/local/bin/pcie-gen4 /usr/local/lib/cmpunlocker/{xpe1,xptest}.ko"
elif dmesg 2>/dev/null | grep -q 'CMP Gen4: PCIe link trained'; then
    ok "驱动已自行完成 Gen4 训练（dmesg: 'CMP Gen4: PCIe link trained'）"
else
    warn "dmesg 里没看到驱动的 Gen4 训练记录"
    echo "     若 LnkSta 已是 16GT/s，可能日志已被轮转，可忽略"
fi

echo
echo "═══ 结果: $PASS 通过 / $FAIL 失败 / $WARN 警告 ═══"
if [ "$FAIL" -gt 0 ]; then
    echo "参考: BRINGUP.md §7 常见故障"
    exit 1
fi
[ "$WARN" -gt 0 ] && echo "警告项通常是已知的显示/环境限制，不影响功能"
exit 0
