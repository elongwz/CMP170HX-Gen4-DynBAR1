#!/usr/bin/env python3
"""Rewrite the kgspBootstrap_TU102 late clamp into the full Gen3 bring-up sequence.
Ported from CMP170HX-Gen3-kit-20261120 (bringup 0002 driver/gen3post/apply_gsp.py)."""
import sys

OLD = '''                NvU32 cyaLate = GPU_REG_RD32(pGpu, 0x0008c2c0U);
                cyaLate = cyaLate & ~(1U << 2);
                GPU_REG_WR32(pGpu, 0x0008c2c0U, cyaLate);
                NV_PRINTF(LEVEL_ERROR,
                          "SEC2_DEBUG: PCIe CYA_0 late clear DIS_G2: 0x%08x (bit2=%u)\\n",
                          GPU_REG_RD32(pGpu, 0x0008c2c0U),
                          (GPU_REG_RD32(pGpu, 0x0008c2c0U) >> 2) & 1U);
                NvU32 linkCfgLate = GPU_REG_RD32(pGpu, 0x0008c040U);
                linkCfgLate = (linkCfgLate & ~0x000C0000U) | (0x2U << 18);
                GPU_REG_WR32(pGpu, 0x0008c040U, linkCfgLate);
                NV_PRINTF(LEVEL_ERROR,
                          "SEC2_DEBUG: PCIe LINK_CONFIG_0 late MAX_RATE=2: 0x%08x\\n",
                          GPU_REG_RD32(pGpu, 0x0008c040U));
                GPU_REG_WR32(pGpu, 0x0008872cU, 0x00000006U);
                NV_PRINTF(LEVEL_ERROR,
                          "SEC2_DEBUG: PCIe XVE_OVR late=0x%08x\\n",
                          GPU_REG_RD32(pGpu, 0x0008872cU));'''

NEW = '''                NvU32 vG3, dlmgrG3, iG3;
                NvU32 tlsG3  = (cmp_pcie_gen >= 4) ? 4U : 3U;
                NvU32 plG3   = tlsG3;
                NvU32 rateG3 = (cmp_pcie_rate > 0) ? (NvU32)cmp_pcie_rate
                                                   : ((cmp_pcie_gen >= 4) ? 2U : 1U);
                NvU32 misc1G3 = GPU_REG_RD32(pGpu, 0x0008841cU);
                GPU_REG_WR32(pGpu, 0x0008841cU,
                             (misc1G3 | ((1U << 11) | (1U << 13))) &
                             ~((1U << 12) | (1U << 14) | 0xc0000000U));
                GPU_REG_WR32(pGpu, 0x0008c2c0U,
                             GPU_REG_RD32(pGpu, 0x0008c2c0U) & ~((1U << 2) | (1U << 3)));
                vG3 = GPU_REG_RD32(pGpu, 0x000880a8U);
                GPU_REG_WR32(pGpu, 0x000880a8U, (vG3 & ~0xfU) | tlsG3);
                vG3 = GPU_REG_RD32(pGpu, 0x0008c1c0U);
                GPU_REG_WR32(pGpu, 0x0008c1c0U, (vG3 & ~0x000f0000U) | (plG3 << 16));
                vG3 = GPU_REG_RD32(pGpu, 0x00088610U);
                GPU_REG_WR32(pGpu, 0x00088610U, vG3 | 0x1000U);
                vG3 = GPU_REG_RD32(pGpu, 0x0008c040U);
                GPU_REG_WR32(pGpu, 0x0008c040U, (vG3 & ~0x000c0000U) | (rateG3 << 18));
                dlmgrG3 = GPU_REG_RD32(pGpu, 0x0008b8c0U);
                GPU_REG_WR32(pGpu, 0x0008b8c0U, dlmgrG3 | 4U);
                vG3 = 0xffffffffU;
                for (iG3 = 0; iG3 < 100000; iG3++)
                {
                    vG3 = GPU_REG_RD32(pGpu, 0x0008c040U);
                    if (vG3 == 0xffffffffU || (vG3 & 0x1fU) == 0) break;
                }
                if (vG3 != 0xffffffffU && (vG3 & 0x1fU) == 0)
                {
                    GPU_REG_WR32(pGpu, 0x0008c040U, (vG3 & ~0xfU) | 1U);
                    for (iG3 = 0; iG3 < 100000; iG3++)
                    {
                        vG3 = GPU_REG_RD32(pGpu, 0x0008c040U);
                        if (vG3 == 0xffffffffU || (vG3 & 0x1fU) == 0) break;
                    }
                }
                if (vG3 != 0xffffffffU)
                    GPU_REG_WR32(pGpu, 0x0008b8c0U, dlmgrG3);
                if (vG3 != 0xffffffffU && (vG3 & 0x1fU) == 0)
                    GPU_REG_WR32(pGpu, 0x0008872cU, 0x00000008U);
                NV_PRINTF(LEVEL_ERROR,
                          "SEC2_DEBUG: PCIe GSP-time Gen%u seq done LINK_CONFIG=0x%08x LTSSM=0x%08x\\n",
                          cmp_pcie_gen,
                          GPU_REG_RD32(pGpu, 0x0008c040U), GPU_REG_RD32(pGpu, 0x0008872cU));'''

def main(path):
    s = open(path, encoding="utf-8", errors="surrogateescape").read()
    if "GSP-time Gen" in s:
        print("gsp-gen3: already applied"); return 0
    if "extern int cmp_pcie_gen;" not in s:
        s = s.replace("NV_STATUS\nkgspBootstrap_TU102",
                      "extern int cmp_pcie_gen;\nextern int cmp_pcie_rate;\n\nNV_STATUS\nkgspBootstrap_TU102", 1)
    if OLD not in s:
        print("gsp-gen3: late-clamp anchor not found"); return 1
    s = s.replace(OLD, NEW, 1)
    open(path, "w", encoding="utf-8", errors="surrogateescape").write(s)
    print("gsp-gen3: late clamp replaced with Gen3 sequence")
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
