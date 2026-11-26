#!/usr/bin/env python3
"""METHOD3 diagnostics, round 2: why is isBar1P2PSupported FALSE?

Round-1 finding (dmesg):  M3DIAG pte-gate ... bar1Sup=0 staticPeer=0 staticMap=0
so the dynamic-BAR1 branch was never entered; the legacy static-identity encode
was used with a BAR1 that is smaller than FB -> silent corruption.

kbusIsPcieBar1P2PMappingSupported_GH100 can return FALSE at three places:
  a) pcieP2PType != BAR1  and  PDB_PROP_KBUS_SUPPORT_BAR1_P2P_BY_DEFAULT == 0
  b) a mailbox P2P peer already exists (peerNumberMask != 0)
  c) mixed static/dynamic pair
Print all of them at LEVEL_ERROR so one cold boot tells us which one fires.
"""
import sys

GH = "/home/gis/p2p/cmpunlocker-v0.5-oc-80g-gen3/driver/.build/open-gpu-kernel-modules-610.43.03/src/nvidia/src/kernel/gpu/bus/arch/hopper/kern_bus_gh100.c"
OPS = "/home/gis/p2p/cmpunlocker-v0.5-oc-80g-gen3/driver/.build/open-gpu-kernel-modules-610.43.03/src/nvidia/src/kernel/rmapi/nv_gpu_ops.c"

MARK = "M3DIAG2"

gh_old = """    KernelBif *pKernelBif = GPU_GET_KERNEL_BIF(pGpu0);

    // Only support if available by default, or if forced by regkey.
"""

gh_new = """    KernelBif *pKernelBif = GPU_GET_KERNEL_BIF(pGpu0);

    NV_PRINTF(LEVEL_ERROR,
              "M3DIAG2 bar1chk: gpu%u<->gpu%u pcieP2PType=%u byDefault=%u "
              "peerMask(%u->%u)=0x%x peerMask(%u->%u)=0x%x static0=%u static1=%u\\n",
              gpuInst0, gpuInst1,
              pKernelBif->pcieP2PType,
              (NvU32)pKernelBus0->getProperty(pKernelBus0,
                                              PDB_PROP_KBUS_SUPPORT_BAR1_P2P_BY_DEFAULT),
              gpuInst0, gpuInst1, (NvU32)pKernelBus0->p2pPcie.peerNumberMask[gpuInst1],
              gpuInst1, gpuInst0, (NvU32)pKernelBus1->p2pPcie.peerNumberMask[gpuInst0],
              (NvU32)kbusIsStaticBar1Enabled(pGpu0, pKernelBus0),
              (NvU32)kbusIsStaticBar1Enabled(pGpu1, pKernelBus1));

    // Only support if available by default, or if forced by regkey.
"""

ops_old = """            if (p2pCaps.bar1Supported)
            {
                OBJGPU *pLocalGpu    = gpumgrGetGpuFromId(device1->gpuId);
"""

ops_new = """            NV_PRINTF(LEVEL_ERROR,
                      "M3DIAG2 capsgate: gpu%d<->gpu%d access=%u nvlink=%u bar1=%u atomic=%u\\n",
                      device1->gpuId, device2->gpuId,
                      (NvU32)p2pCaps.accessSupported, (NvU32)p2pCaps.nvlinkSupported,
                      (NvU32)p2pCaps.bar1Supported, (NvU32)p2pCaps.atomicSupported);

            if (p2pCaps.bar1Supported)
            {
                OBJGPU *pLocalGpu    = gpumgrGetGpuFromId(device1->gpuId);
"""


def sub1(path, old, new, tag):
    src = open(path).read()
    n = src.count(old)
    if n != 1:
        sys.exit("anchor %s occurs %d times (need 1)" % (tag, n))
    open(path, "w").write(src.replace(old, new))
    return tag


for f in (GH, OPS):
    if MARK in open(f).read():
        print("already patched:", f)
        sys.exit(0)

print("patched:", sub1(GH, gh_old, gh_new, "bar1chk"))
print("patched:", sub1(OPS, ops_old, ops_new, "capsgate"))
