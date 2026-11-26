#!/usr/bin/env python3
"""Does the slow direction follow the source GPU or the 'current' CUDA device?"""
import time, torch

N = 1 << 30
CHUNK = 256 << 20
REPS = 3

def copy_dir(src, dst, cur, size=N):
    torch.cuda.set_device(cur)
    a = torch.empty(size, dtype=torch.uint8, device=f"cuda:{src}")
    b = torch.empty(size, dtype=torch.uint8, device=f"cuda:{dst}")
    a.fill_(5)
    for off in range(0, size, CHUNK):
        b[off:off+CHUNK].copy_(a[off:off+CHUNK])
    torch.cuda.synchronize()
    best = 0.0
    for _ in range(REPS):
        t0 = time.perf_counter()
        for off in range(0, size, CHUNK):
            b[off:off+CHUNK].copy_(a[off:off+CHUNK])
        torch.cuda.synchronize()
        best = max(best, size / (time.perf_counter() - t0) / 1e9)
    ok = int((b[::4096] == 5).all().item())
    del a, b
    torch.cuda.empty_cache()
    return best, ok

for pair in [(0, 1), (1, 0)]:
    for cur in [pair[0], pair[1]]:
        bw, ok = copy_dir(pair[0], pair[1], cur)
        print(f"src={pair[0]} dst={pair[1]} current_dev={cur}: {bw:6.2f} GB/s ok={ok}")
