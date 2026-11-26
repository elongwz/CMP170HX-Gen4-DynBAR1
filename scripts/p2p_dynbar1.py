#!/usr/bin/env python3
"""Dynamic-BAR1 P2P validation.

Purpose: prove that peer copies are *bit-exact* at large sizes and at high
framebuffer offsets (the two cases that silently corrupted / hung the card
under static BAR1 identity mapping when BAR1(64GiB) < FB(80GiB)).

Usage: python p2p_dynbar1.py [--sizes 4,64,256,1024,2048] [--high 1]
"""
import argparse
import sys
import time

import torch

GB = 1024 ** 3


def make(nbytes, dev, seed):
    n = nbytes // 4
    g = torch.Generator().manual_seed(seed)
    t = torch.randn(n, generator=g, dtype=torch.float32).to(dev, non_blocking=False)
    return t, t.cpu()  # (device tensor, bit-exact cpu reference)


def peer_copy(dst_dev, src_t, dst_t):
    s = torch.cuda.Stream(device=dst_dev)
    with torch.cuda.stream(s):
        dst_t.copy_(src_t, non_blocking=True)
    s.synchronize()


def verify(dst_t, ref_cpu):
    got = dst_t.cpu()
    bad = (got.view(torch.int32) != ref_cpu.view(torch.int32)).sum().item()
    return bad


def rate(nbytes, dt):
    return nbytes / dt / 1e9


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="4,64,256,1024,2048")
    ap.add_argument("--src", type=int, default=0)
    ap.add_argument("--dst", type=int, default=1)
    ap.add_argument("--high", type=int, default=1, help="1 = also test a high-FB-offset allocation")
    args = ap.parse_args()

    n_gpu = torch.cuda.device_count()
    print(f"GPUs: {n_gpu}")
    for i in range(n_gpu):
        p = torch.cuda.get_device_properties(i)
        print(f"  dev{i}: {p.name} {p.total_memory/GB:.1f} GiB")
    if n_gpu < 2:
        print("need 2 GPUs")
        return 2

    print(f"can_device_access_peer({args.src},{args.dst}) = "
          f"{torch.cuda.can_device_access_peer(args.src, args.dst)}")

    fail = 0
    print(f"\n=== size sweep {args.src} -> {args.dst}")
    for mb in [int(x) for x in args.sizes.split(",") if x]:
        nbytes = mb * 1024 * 1024
        src_t, ref = make(nbytes, args.src, seed=1000 + mb)
        dst_t = torch.empty_like(src_t, device=args.dst)
        peer_copy(args.dst, src_t, dst_t)
        # warm-up pass already done above; measure a second pass
        t0 = time.perf_counter()
        peer_copy(args.dst, src_t, dst_t)
        dt = time.perf_counter() - t0
        bad = verify(dst_t, ref)
        status = "OK" if bad == 0 else f"CORRUPT({bad})"
        if bad:
            fail += 1
        print(f"  {mb:5d} MiB  {rate(nbytes,dt):6.2f} GB/s  {status}")
        del src_t, dst_t, ref
        torch.cuda.empty_cache()

    if args.high:
        print(f"\n=== high-FB-offset probe (dst dev{args.dst}): fill 70 GiB, then 1 GiB target")
        filler = torch.empty(int(70 * GB // 4), dtype=torch.float32, device=args.dst)
        filler.fill_(0.5)
        nbytes = 1 * GB
        src_t, ref = make(nbytes, args.src, seed=4242)
        dst_t = torch.empty_like(src_t, device=args.dst)   # should land above the 64 GiB BAR1 window
        peer_copy(args.dst, src_t, dst_t)
        t0 = time.perf_counter()
        peer_copy(args.dst, src_t, dst_t)
        dt = time.perf_counter() - t0
        bad = verify(dst_t, ref)
        if bad:
            fail += 1
        print(f"  1024 MiB @ high offset  {rate(nbytes,dt):6.2f} GB/s  "
              f"{'OK' if bad==0 else f'CORRUPT({bad})'}")

    print(f"\nRESULT: {'PASS' if fail == 0 else f'FAIL ({fail} bad transfers)'}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
