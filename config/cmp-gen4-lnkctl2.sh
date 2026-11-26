#!/bin/bash
# CMP170HX Gen4: ensure both GPU endpoints advertise 16GT/s as the LnkCtl2 target speed.
# The gen2 late-clamp rewrites LnkCtl2 target to 2 (5GT/s) after the driver loads, which
# does not hurt a link that already trained at 16GT/s but will block any retrain; this
# service restores it once nvidia is up.
set -u

# wait for both GPUs to appear (driver load can take a while on a dock)
for _ in $(seq 1 90); do
    if nvidia-smi -L >/dev/null 2>&1 && [ "$(nvidia-smi -L 2>/dev/null | wc -l)" -ge 2 ]; then
        break
    fi
    sleep 2
done
sleep 3

for bdf in 05:00.0 08:00.0; do
    cur=$(setpci -s "$bdf" CAP_EXP+30.w 2>/dev/null || true)
    [ -z "$cur" ] && continue
    if [ "$cur" != "0004" ] && [ "$cur" != "0044" ]; then
        setpci -s "$bdf" CAP_EXP+30.w=0x0004:0x000f
        echo "cmp-gen4-lnkctl2: $bdf LnkCtl2 $cur -> $(setpci -s $bdf CAP_EXP+30.w)"
    fi
done
exit 0
