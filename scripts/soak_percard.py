#!/usr/bin/env python3
"""Per-card soak: PCIe + HBM load on one card at a time, look for link/ECC/Xid problems."""
import subprocess, sys, time, torch

SECS = 25

def sh(c):
    return subprocess.run(c, shell=True, capture_output=True, text=True).stdout.strip()

def xid_counts():
    out = sh("printf 'gis123\\n' | sudo -S dmesg 2>/dev/null | grep -o 'Xid (PCI:0000:[0-9a-f]*): [0-9]*'")
    d = {}
    for line in out.splitlines():
        d[line] = d.get(line, 0) + 1
    return d

def link(bdf):
    return sh(f"printf 'gis123\\n' | sudo -S lspci -vvv -s {bdf} 2>/dev/null | grep -E 'LnkSta:|LnkCtl2:' | tr -s '\\t'")

def soak(dev, bdf):
    torch.cuda.set_device(dev)
    a = torch.empty(2 << 30, dtype=torch.uint8, device=f"cuda:{dev}")
    h = torch.empty(512 << 20, dtype=torch.uint8, pin_memory=True)
    m = torch.randn(8192, 8192, device=f"cuda:{dev}")
    before = xid_counts()
    t_end = time.time() + SECS
    it = 0
    while time.time() < t_end:
        h.copy_(a[: 512 << 20], non_blocking=True)
        a[: 512 << 20].copy_(h, non_blocking=True)
        m = m @ m.T
        m = m / m.abs().max()
        it += 1
    torch.cuda.synchronize()
    after = xid_counts()
    new = {k: after[k] - before.get(k, 0) for k in after if after[k] > before.get(k, 0)}
    print(f"GPU{dev} ({bdf}): {it} iters, new Xid: {new if new else 'none'}")
    print("   link:", link(bdf).replace("\n", " | "))
    print("   clocks:", sh(f"nvidia-smi --query-gpu=index,temperature.gpu,power.draw,clocks.sm,clocks_throttle_reasons.active --format=csv,noheader").splitlines()[dev])
    del a, h, m
    torch.cuda.empty_cache()

for dev, bdf in [(0, "05:00.0"), (1, "08:00.0")]:
    soak(dev, bdf)
