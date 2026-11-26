#!/usr/bin/env python3
"""PCIe H2D/D2H bandwidth test using libcuda directly (no CUDA toolkit needed).
Pinned host memory, synchronous copies. Run on the host:
    python3 pcie_bandwidth.py [--mb 256] [--iters 20]
Reference (converted A100 card, native Gen4 x16): H2D 26.76 / D2H 26.67 GB/s.
Gen3 ~ 13.3 GB/s, Gen2 ~ 6.7 GB/s x16-equivalent (cmp_gen3.md: measured at
reduced width, scaled linearly).
"""
import argparse
import ctypes
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mb", type=int, default=256, help="transfer size in MiB")
    ap.add_argument("--iters", type=int, default=20)
    args = ap.parse_args()
    size = args.mb * 1024 * 1024

    cu = ctypes.CDLL("libcuda.so.1")
    P = ctypes.POINTER
    cu.cuInit.argtypes = [ctypes.c_uint]
    cu.cuDeviceGet.argtypes = [P(ctypes.c_int), ctypes.c_int]
    cu.cuCtxCreate_v2.argtypes = [P(ctypes.c_void_p), ctypes.c_uint, ctypes.c_int]
    cu.cuMemHostAlloc.argtypes = [P(ctypes.c_void_p), ctypes.c_size_t, ctypes.c_uint]
    cu.cuMemAlloc_v2.argtypes = [P(ctypes.c_void_p), ctypes.c_size_t]
    cu.cuMemcpyHtoD_v2.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    cu.cuMemcpyDtoH_v2.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    cu.cuMemFreeHost.argtypes = [ctypes.c_void_p]
    cu.cuMemFree_v2.argtypes = [ctypes.c_void_p]
    cu.cuCtxDestroy_v2.argtypes = [ctypes.c_void_p]
    for f in (cu.cuInit, cu.cuDeviceGet, cu.cuCtxCreate_v2, cu.cuMemHostAlloc,
              cu.cuMemAlloc_v2, cu.cuMemcpyHtoD_v2, cu.cuMemcpyDtoH_v2,
              cu.cuCtxSynchronize, cu.cuMemFreeHost, cu.cuMemFree_v2, cu.cuCtxDestroy_v2):
        f.restype = ctypes.c_int

    def chk(rc, what):
        if rc != 0:
            raise SystemExit("%s failed: %d" % (what, rc))

    chk(cu.cuInit(0), "cuInit")
    dev = ctypes.c_int()
    chk(cu.cuDeviceGet(ctypes.byref(dev), 0), "cuDeviceGet")
    name = ctypes.create_string_buffer(128)
    cu.cuDeviceGetName(name, 128, dev)
    ctx = ctypes.c_void_p()
    chk(cu.cuCtxCreate_v2(ctypes.byref(ctx), 0, dev), "cuCtxCreate")
    print("device: %s" % name.value.decode())

    host = ctypes.c_void_p()
    chk(cu.cuMemHostAlloc(ctypes.byref(host), size, 0x01), "cuMemHostAlloc")  # PORTABLE pinned
    dptr = ctypes.c_void_p()
    chk(cu.cuMemAlloc_v2(ctypes.byref(dptr), size), "cuMemAlloc")
    ctypes.memset(host, 0x5A, size)

    chk(cu.cuMemcpyHtoD_v2(dptr, host, size), "warmup H2D")
    chk(cu.cuCtxSynchronize(), "sync")

    def bench(fn):
        chk(cu.cuCtxSynchronize(), "sync")
        t0 = time.perf_counter()
        for _ in range(args.iters):
            chk(fn(), "copy")
        chk(cu.cuCtxSynchronize(), "sync")
        dt = time.perf_counter() - t0
        return args.iters * size / dt / 1e9

    h2d = bench(lambda: cu.cuMemcpyHtoD_v2(dptr, host, size))
    d2h = bench(lambda: cu.cuMemcpyDtoH_v2(host, dptr, size))
    print("transfer: %d MiB x %d iters (pinned)" % (args.mb, args.iters))
    print("H2D: %.2f GB/s" % h2d)
    print("D2H: %.2f GB/s" % d2h)

    cu.cuMemFreeHost(host)
    cu.cuMemFree_v2(dptr)
    cu.cuCtxDestroy_v2(ctx)


if __name__ == "__main__":
    main()
