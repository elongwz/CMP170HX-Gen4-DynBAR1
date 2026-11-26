#!/usr/bin/env python3
"""Small-transfer P2P latency probe (dynamic BAR1 mode).

Measures per-operation latency for tiny cross-GPU copies using CUDA events,
so we can compare against the earlier mailbox numbers (~17 us fixed cost).
Also measures a same-GPU copy as the submission-overhead baseline.
"""
import torch

assert torch.cuda.device_count() >= 2, "need 2 GPUs"
dev0, dev1 = 0, 1


def make(dev, nbytes):
    n = max(1, nbytes // 4)
    t = torch.zeros(n, dtype=torch.int32, device=f"cuda:{dev}")
    return t


def timed_copy(src_dev, dst_dev, nbytes, iters=200):
    src = make(src_dev, nbytes)
    dst = make(dst_dev, nbytes)
    s = torch.cuda.Stream(device=dst_dev)
    # warm-up
    for _ in range(20):
        with torch.cuda.stream(s):
            dst.copy_(src, non_blocking=True)
    s.synchronize()
    ev0 = torch.cuda.Event(enable_timing=True)
    ev1 = torch.cuda.Event(enable_timing=True)
    ev0.record(s)
    for _ in range(iters):
        with torch.cuda.stream(s):
            dst.copy_(src, non_blocking=True)
    ev1.record(s)
    s.synchronize()
    ms = ev0.elapsed_time(ev1)
    return ms * 1000.0 / iters  # us per op


print(f"{'size':>10} {'0->1 us':>10} {'1->0 us':>10} {'same-gpu us':>12}")
for nbytes in (16, 64, 256, 1024, 4096, 16384, 65536):
    a = timed_copy(dev0, dev1, nbytes)
    b = timed_copy(dev1, dev0, nbytes)
    c = timed_copy(dev0, dev0, nbytes)
    print(f"{nbytes:>10} {a:>10.2f} {b:>10.2f} {c:>12.2f}")

# bandwidth at a mid size for reference
for mb in (8, 64, 256):
    nbytes = mb * 1024 * 1024
    src = make(dev0, nbytes)
    dst = make(dev1, nbytes)
    s = torch.cuda.Stream(device=dev1)
    for _ in range(3):
        with torch.cuda.stream(s):
            dst.copy_(src, non_blocking=True)
    s.synchronize()
    ev0 = torch.cuda.Event(enable_timing=True)
    ev1 = torch.cuda.Event(enable_timing=True)
    ev0.record(s)
    with torch.cuda.stream(s):
        dst.copy_(src, non_blocking=True)
    ev1.record(s)
    s.synchronize()
    ms = ev0.elapsed_time(ev1)
    print(f"bulk {mb:>4} MiB 0->1: {nbytes / (ms / 1000) / 1e9:.2f} GB/s")
