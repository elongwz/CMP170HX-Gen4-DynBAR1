#!/usr/bin/env python3
"""Insert nv_cmp170hx_gen3_post() into kernel-open/nvidia/nv.c (idempotent).
Ported from CMP170HX-Gen3-kit-20261120 (bringup 0002 driver/gen3post/apply.py)."""
import sys, io

FUNC = r"""
int cmp_pcie_gen = 3;
module_param(cmp_pcie_gen, int, 0644);
MODULE_PARM_DESC(cmp_pcie_gen, "CMP 170HX target PCIe generation (3 or 4)");
int cmp_pcie_rate = 0;
module_param(cmp_pcie_rate, int, 0644);

static void
nv_cmp170hx_gen3_post(nv_state_t *nv)
{
    nv_linux_state_t *nvl = NV_GET_NVL_FROM_NV_STATE(nv);
    struct pci_dev *gpu = nvl->pci_dev;
    struct pci_dev *upstream;
    void __iomem *bar0;
    u16 gpu_ctl2, upstream_ctl2, upstream_ctl, link_status = 0;
    u32 v, dlmgr, rate_code, pl_code, tls;
    int ret, attempt, i;

    if ((gpu == NULL) || (gpu->device != 0x20c2))
        return;

    rate_code = (u32)cmp_pcie_gen;
    pl_code   = (u32)cmp_pcie_gen;
    tls       = (u32)cmp_pcie_gen;

    upstream = pci_upstream_bridge(gpu);
    if (upstream == NULL) return;
    bar0 = ioremap(pci_resource_start(gpu, 0), 0x90000);
    if (bar0 == NULL) return;

    /* XP configuration is done at GSP time (apply_gsp.py); here we only retrain. */
    (void)v;
    iounmap(bar0);
    msleep(100);

    ret = pcie_capability_read_word(gpu, PCI_EXP_LNKCTL2, &gpu_ctl2);
    if (ret) goto cap_err;
    ret = pcie_capability_read_word(upstream, PCI_EXP_LNKCTL2, &upstream_ctl2);
    if (ret) goto cap_err;
    gpu_ctl2      = (gpu_ctl2      & ~0x000fU) | tls;
    upstream_ctl2 = (upstream_ctl2 & ~0x000fU) | tls;
    ret = pcie_capability_write_word(gpu, PCI_EXP_LNKCTL2, gpu_ctl2);
    if (ret) goto cap_err;
    ret = pcie_capability_write_word(upstream, PCI_EXP_LNKCTL2, upstream_ctl2);
    if (ret) goto cap_err;
    ret = pcie_capability_read_word(upstream, PCI_EXP_LNKCTL, &upstream_ctl);
    if (ret) goto cap_err;
    ret = pcie_capability_write_word(upstream, PCI_EXP_LNKCTL, upstream_ctl | PCI_EXP_LNKCTL_RL);
    if (ret) goto cap_err;

    for (attempt = 0; attempt < 20; attempt++)
    {
        msleep(50);
        ret = pcie_capability_read_word(gpu, PCI_EXP_LNKSTA, &link_status);
        if (!ret && ((link_status & PCI_EXP_LNKSTA_CLS) >= tls))
        {
            NV_DEV_PRINTF(NV_DBG_ERRORS, nv,
                          "CMP Gen%u: PCIe link trained (LNKSTA=0x%04x)\n",
                          cmp_pcie_gen, link_status);
            return;
        }
    }
    NV_DEV_PRINTF(NV_DBG_ERRORS, nv,
                  "CMP Gen%u: retrain did not reach target (LNKSTA=0x%04x ret=%d)\n",
                  cmp_pcie_gen, link_status, ret);
    return;
cap_err:
    NV_DEV_PRINTF(NV_DBG_ERRORS, nv, "CMP Gen%u: capability access failed (%d)\n",
                  cmp_pcie_gen, ret);
}

"""

def main(path):
    s = io.open(path, encoding="utf-8", errors="surrogateescape").read()
    if "nv_cmp170hx_gen3_post" in s:
        print("gen3post: already applied")
        return 0
    anchor = "static void\nnv_cmp170hx_retrain_gen2(nv_state_t *nv)"
    if anchor not in s:
        print("gen3post: anchor not found"); return 1
    s = s.replace(anchor, FUNC.lstrip("\n") + anchor, 1)
    call = "    nv_cmp170hx_retrain_gen2(nv);"
    if call not in s:
        print("gen3post: call site not found"); return 1
    s = s.replace(call, call + "\n    nv_cmp170hx_gen3_post(nv);", 1)
    io.open(path, "w", encoding="utf-8", errors="surrogateescape").write(s)
    print("gen3post: inserted function + call")
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
