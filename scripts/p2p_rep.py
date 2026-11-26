import time,torch
N=1<<30
a=torch.empty(N//4,dtype=torch.float32,device="cuda:0")
b=torch.empty(N//4,dtype=torch.float32,device="cuda:1")
def run(dst,src,ns,label,iters=5):
    streams=[torch.cuda.Stream(device=dst.device) for _ in range(ns)]
    chunk=(N//4)//ns
    dst.copy_(src); torch.cuda.synchronize()
    t=time.perf_counter()
    for _ in range(iters):
        for i in range(ns):
            with torch.cuda.stream(streams[i]):
                s=i*chunk; e=(i+1)*chunk
                dst[s:e].copy_(src[s:e],non_blocking=True)
        torch.cuda.synchronize()
    dt=(time.perf_counter()-t)/iters
    print("%-20s %6.2f GB/s"%(label,N/dt/1e9),flush=True)
for rep in range(3):
    print("--- rep",rep)
    run(b,a,2,"0->1 s2")
    run(a,b,2,"1->0 s2")
    run(b,a,1,"0->1 s1")
    run(a,b,1,"1->0 s1")