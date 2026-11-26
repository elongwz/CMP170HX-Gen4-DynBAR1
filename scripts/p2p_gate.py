import ctypes, torch, time, sys
lib = ctypes.CDLL("libcudart.so")
print("peer_access 0->1", torch.cuda.can_device_access_peer(0,1), flush=True)
def one(MB, d):
    n = MB*1024*1024
    s = 0 if d == "01" else 1; t = 1-s
    src = torch.full((n,), 0xAB, dtype=torch.uint8, device=f"cuda:{s}")
    dst = torch.full((n,), 0x00, dtype=torch.uint8, device=f"cuda:{t}")
    torch.cuda.synchronize()
    lib.cudaMemcpyPeerAsync(ctypes.c_void_p(dst.data_ptr()), ctypes.c_int(t),
                            ctypes.c_void_p(src.data_ptr()), ctypes.c_int(s),
                            ctypes.c_size_t(n), ctypes.c_void_p(0))
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    lib.cudaMemcpyPeerAsync(ctypes.c_void_p(dst.data_ptr()), ctypes.c_int(t),
                            ctypes.c_void_p(src.data_ptr()), ctypes.c_int(s),
                            ctypes.c_size_t(n), ctypes.c_void_p(0))
    torch.cuda.synchronize()
    dt = time.perf_counter() - t0
    STEP = 64*1024*1024
    bad = 0; first = -1
    for off in range(0, n, STEP):
        e = min(off+STEP, n)
        a = src[off:e].cpu(); b = dst[off:e].cpu()
        ne = int((a != b).sum())
        if ne:
            bad += ne
            if first < 0: first = off + int((a != b).nonzero()[0])
    print(f"dir{d} {MB}MB: {n/1e9/dt:6.2f} GB/s  mismatched={bad} first_off={first}", flush=True)
    del src, dst
    torch.cuda.empty_cache()
    return bad
for MB in (4, 64, 256):
    if one(MB, "01") or one(MB, "10"):
        print("GATE FAILED — aborting before large sizes", flush=True); sys.exit(1)
print("small sizes OK, trying 1024MB", flush=True)
if one(1024, "01") or one(1024, "10"):
    print("GATE FAILED at 1GiB — aborting before 2GiB", flush=True); sys.exit(1)
print("1GiB OK, trying 2048MB", flush=True)
if one(2048, "01"):
    print("GATE FAILED at 2GiB", flush=True); sys.exit(1)
print("ALL OK", flush=True)