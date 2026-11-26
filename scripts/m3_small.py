#!/usr/bin/env python3
"""Minimal peer-copy probe: one small copy at a time, flushed after each step.

Same copy pattern as p2p_dynbar1.py (stream on the destination device +
non_blocking copy_), because a bare copy_ across devices is rejected by torch.
Comparison is done on the CPU so that it does not itself need a cross-device op.
Nothing bigger than 8 MiB is allocated here on purpose.
"""
import sys
import time

import torch


def log(msg):
    print(msg, flush=True)


def peer_copy(dst_dev, src_t, dst_t):
    s = torch.cuda.Stream(device=dst_dev)
    with torch.cuda.stream(s):
        dst_t.copy_(src_t, non_blocking=True)
    s.synchronize()


def main():
    log("torch %s cuda %s" % (torch.__version__, torch.version.cuda))
    log("devices %d" % torch.cuda.device_count())

    for mb in (1, 4, 8):
        n = mb * 1024 * 1024
        try:
            src = torch.arange(n, dtype=torch.uint8, device="cuda:0")
            dst = torch.zeros(n, dtype=torch.uint8, device="cuda:1")
            torch.cuda.synchronize()
            log("-- about to copy %d MiB --" % mb)
            t0 = time.time()
            peer_copy(1, src, dst)
            dt = time.time() - t0
            bad = int((dst.cpu() != src.cpu()).sum().item())
            log("copy %d MiB: %.2f GB/s (rw), mismatched=%d"
                % (mb, (2 * n) / dt / 1e9, bad))
            del src, dst
            torch.cuda.empty_cache()
        except Exception as exc:  # noqa: BLE001
            log("FAIL at %d MiB: %r" % (mb, exc))
            return 2

    log("RESULT: small-size probe finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
