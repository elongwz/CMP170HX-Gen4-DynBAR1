#!/bin/bash
# Test whether ACS Upstream Forwarding blocks switch-local P2P.
set -u
CPU=/tmp/p2p_lat.py
echo "--- baseline ---"
python3 $CPU BEFORE
for b in 02:00.0 04:10.0 07:00.0; do setpci -s $b ECAP_ACS+0x6.w=0x0000; done
echo -n "--- ACS cleared: "
for b in 02:00.0 04:10.0 07:00.0; do echo -n "$b=$(setpci -s $b ECAP_ACS+0x6.w) "; done
echo
python3 $CPU ACS0
for b in 02:00.0 04:10.0 07:00.0; do setpci -s $b ECAP_ACS+0x6.w=0x0011; done
echo -n "--- restored: "
for b in 02:00.0 04:10.0 07:00.0; do echo -n "$b=$(setpci -s $b ECAP_ACS+0x6.w) "; done
echo
python3 $CPU AFTER
