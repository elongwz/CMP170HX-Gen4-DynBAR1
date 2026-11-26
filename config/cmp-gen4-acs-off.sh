#!/bin/bash
# CMP170HX / PEX88096 dock: disable ACS on switch downstream ports.
# Upstream Forwarding (bit4) forces GPU<->GPU P2P TLPs up to the host root complex,
# which caps inter-GPU copies at host-bounce speed and silently drops BAR1 P2P writes.
set -u
changed=0
for bdf in $(lspci -D 2>/dev/null | awk '/PEX880xx PCIe Gen 4 Switch/{print $1}'); do
    cur=$(setpci -s "$bdf" ECAP_ACS+0x6.w 2>/dev/null) || continue
    [ -z "$cur" ] && continue
    if [ "$cur" != "0000" ]; then
        setpci -s "$bdf" ECAP_ACS+0x6.w=0x0000 || continue
        new=$(setpci -s "$bdf" ECAP_ACS+0x6.w 2>/dev/null)
        echo "acs-off: $bdf ACSCtl $cur -> $new"
        changed=1
    fi
done
[ "$changed" -eq 0 ] && echo "acs-off: nothing to do"
exit 0
