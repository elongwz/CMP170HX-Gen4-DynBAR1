#!/usr/bin/env python3
"""METHOD3 diagnostics: make the dynamic-BAR1 path audible in dmesg.

Changes (idempotent, asserts each anchor occurs exactly once):
  1) nvGpuOpsGetExternalAllocPtesOrPhysAddrs: print the gate inputs right before
     the isBar1P2PSupported / !kbusIsStaticBar1Enabled check.
  2) print right after dynBar1Mapped = NV_TRUE (window base that will be used).
  3) the "dynamic BAR1 P2P map ... bar1Off ... dmaBase" success print: LEVEL_INFO
     -> LEVEL_ERROR so it shows with default RM logging.
  4) nvGpuOpsGetP2PCaps: print what we report to UVM (bar1DmaAddress/Size[0..1],
     static-BAR1 state of both GPUs, p2pLink).
"""
import sys

P = "/home/gis/p2p/cmpunlocker-v0.5-oc-80g-gen3/driver/.build/open-gpu-kernel-modules-610.43.03/src/nvidia/src/kernel/rmapi/nv_gpu_ops.c"

MARK = "M3DIAG"

gate_old = """            if (isBar1P2PSupported &&
                !kbusIsStaticBar1Enabled(pAdjustedMemDesc->pGpu,
                                         GPU_GET_KERNEL_BUS(pAdjustedMemDesc->pGpu)))
            {
"""

gate_new = """            NV_PRINTF(LEVEL_ERROR,
                      "M3DIAG pte-gate: mapGPU%d peerGPU%d peerSup=%d indirect=%d "
                      "bar1Sup=%d staticPeer=%d staticMap=%d hMem=0x%x size=0x%llx\\n",
                      gpuGetInstance(pMappingGpu),
                      gpuGetInstance(pAdjustedMemDesc->pGpu),
                      (int)isPeerSupported, (int)isIndirectPeerSupported,
                      (int)isBar1P2PSupported,
                      (int)kbusIsStaticBar1Enabled(pAdjustedMemDesc->pGpu,
                                                   GPU_GET_KERNEL_BUS(pAdjustedMemDesc->pGpu)),
                      (int)kbusIsStaticBar1Enabled(pMappingGpu,
                                                   GPU_GET_KERNEL_BUS(pMappingGpu)),
                      hMemory, (unsigned long long)memdescGetSize(pAdjustedMemDesc));
            if (isBar1P2PSupported &&
                !kbusIsStaticBar1Enabled(pAdjustedMemDesc->pGpu,
                                         GPU_GET_KERNEL_BUS(pAdjustedMemDesc->pGpu)))
            {
"""

mapped_old = """                dynBar1Mapped = NV_TRUE;
            }
"""

mapped_new = """                dynBar1Mapped = NV_TRUE;
                NV_PRINTF(LEVEL_ERROR,
                          "M3DIAG pte-dyn: GPU%d->GPU%d hMem=0x%x windowBase=0x%llx\\n",
                          gpuGetInstance(pMappingGpu),
                          gpuGetInstance(pAdjustedMemDesc->pGpu),
                          hMemory, (unsigned long long)dynBar1DmaBase);
            }
"""

info_old = """    NV_PRINTF(LEVEL_INFO,
              "METHOD3: dynamic BAR1 P2P map GPU%u->GPU%u hMem 0x%x size 0x%llx "
"""

info_new = """    NV_PRINTF(LEVEL_ERROR,
              "METHOD3: dynamic BAR1 P2P map GPU%u->GPU%u hMem 0x%x size 0x%llx "
"""

caps_old = """                p2pCapsParams->p2pLink = UVM_LINK_TYPE_PCIE_BAR1;
"""

caps_new = """                p2pCapsParams->p2pLink = UVM_LINK_TYPE_PCIE_BAR1;
                NV_PRINTF(LEVEL_ERROR,
                          "M3DIAG caps: gpu%d<->gpu%d staticLocal=%d staticRemote=%d "
                          "addr0=0x%llx size0=0x%llx addr1=0x%llx size1=0x%llx\\n",
                          device1->gpuId, device2->gpuId,
                          (int)kbusIsStaticBar1Enabled(pLocalGpu,
                                                       GPU_GET_KERNEL_BUS(pLocalGpu)),
                          (int)kbusIsStaticBar1Enabled(pRemoteGpu,
                                                       GPU_GET_KERNEL_BUS(pRemoteGpu)),
                          (unsigned long long)p2pCapsParams->bar1DmaAddress[0],
                          (unsigned long long)p2pCapsParams->bar1DmaSize[0],
                          (unsigned long long)p2pCapsParams->bar1DmaAddress[1],
                          (unsigned long long)p2pCapsParams->bar1DmaSize[1]);
"""


def sub1(text, old, new, tag):
    n = text.count(old)
    if n != 1:
        sys.exit("anchor %s occurs %d times (need 1)" % (tag, n))
    return text.replace(old, new)


src = open(P).read()
if MARK in src:
    print("already patched - nothing to do")
    sys.exit(0)

src = sub1(src, gate_old, gate_new, "pte-gate")
src = sub1(src, mapped_old, mapped_new, "pte-dyn")
src = sub1(src, info_old, info_new, "map-success-level")
src = sub1(src, caps_old, caps_new, "caps")

open(P, "w").write(src)
print("patched:", P)
print("M3DIAG occurrences:", src.count(MARK))
