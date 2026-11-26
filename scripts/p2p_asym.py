#!/usr/bin/env python3
"""Interleaved per-direction P2P + per-card H2D/D2H, to attribute asymmetry to a card."""
import time, sys, torch

TOTAL = 2 << 30            # 2 GiB payload per direction
CHUNK = 256 << 20          # 256 MiB chunks
REPS = 4

def sync():
    torch.cuda.synchronize()

def p2p(src, dst, nbytes):
    torch.cuda.set_device(src)
    a = torch.empty(nbytes, dtype=torch.uint8, device=f"cuda:{src}")
    b = torch.empty(nbytes, dtype=torch.uint8, device=f"cuda:{dst}")
    a.fill_(0x11)
    # warm
    for off in range(0, nbytes, CHUNK):
        b[off:off+CHUNK].copy_(a[off:off+CHUNK])
    sync()
    t0 = time.perf_counter()
    for _ in range(REPS):
        for off in range(0, nbytes, CHUNK):
            b[off:off+CHUNK].copy_(a[off:off+CHUNK])
    sync()
    dt = (time.perf_counter() - t0) / REPS
    ok = int((b[::4096] == 0x11).all().item())
    del a, b
    torch.cuda.empty_cache()
    return nbytes / dt / 1e9, ok

def one_way(dev, nbytes, to_device):
    torch.cuda.set_device(dev)
    h = torch.empty(nbytes, dtype=torch.uint8, pin_memory=True)
    d = torch.empty(nbytes, dtype=torch.uint8, device=f"cuda:{dev}")
    h.fill_(0x22); d.fill_(0x33)
    for off in range(0, nbytes, CHUNK):
        if to_device: d[off:off+CHUNK].copy_(h[off:off+CHUNK], non_blocking=True)
        else:         h[off:off+CHUNK].copy_(d[off:off+CHUNK], non_blocking=True)
    sync()
    best = 0.0
    for _ in range(3):
        t0 = time.perf_counter()
        for off in range(0, nbytes, CHUNK):
            if to_device: d[off:off+CHUNK].copy_(h[off:off+CHUNK], non_blocking=True)
            else:         h[off:off+CHUNK].copy_(d[off:off+CHUNK], non_blocking=True)
        sync()
        best = max(best, nbytes / (time.perf_counter() - t0) / 1e9)
    del h, d
    torch.cuda.empty_cache()
    return best

print("devices:", torch.cuda.device_count())
print("--- per-card one-way (1 GiB) ---")
for dev in range(torch.cuda.device_count()):
    h2d = one_way(dev, 1 << 30, True)
    d2h = one_way(dev, 1 << 30, False)
    print(f"GPU{dev}: H2D {h2d:6.2f} GB/s   D2H {d2h:6.2f} GB/s")

print(f"--- P2P interleaved, {TOTAL>>20} MiB/chunk {CHUNK>>20} MiB, {REPS} reps ---")
res = {(0,1): [], (1,0): []}
for r in range(3):
    for pair in [(0,1), (1,0)]:
        bw, ok = p2p(pair[0], pair[1], TOTAL)
        res[pair].append(bw)
        print(f"  rep{r} {pair[0]}->{pair[1]}: {bw:6.2f} GB/s  data_ok={ok}")
for pair, vals in res.items():
    print(f"SUMMARY {pair[0]}->{pair[1]}: min {min(vals):.2f} max {max(vals):.2f} avg {sum(vals)/len(vals):.2f} GB/s")
