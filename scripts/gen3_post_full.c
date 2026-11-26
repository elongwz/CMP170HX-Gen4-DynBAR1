/*
 * CMP 170HX PCIe bring-up (ported from cmp170hx-gen4-bringup-20261118 0002+0005):
 *   Phase A: Gen3 step  -> endpoint/LNK_CONFIG declare 8 GT/s and link trains Gen3
 *   Phase B: E1 replay  -> resend the A100 Gen4 PHY-init block the CMP VBIOS drops
 *   Phase C: retrain    -> LNKCTL2 target on both ends + Retrain Link, poll LNKSTA
 *
 * LINK_CONFIG_0[19:18] is REVERSE-encoded: 0=Gen4 1=Gen3 2=Gen2 3=Gen1.
 * LNKCTL2 / PL_LINK_RATE gen fields are FORWARD-encoded: 1=Gen1 .. 4=Gen4.
 */
int cmp_pcie_gen = 4;
module_param(cmp_pcie_gen, int, 0644);
MODULE_PARM_DESC(cmp_pcie_gen, "CMP 170HX target PCIe generation (3 or 4)");
int cmp_pcie_rate = 0;
module_param(cmp_pcie_rate, int, 0644);
MODULE_PARM_DESC(cmp_pcie_rate, "Override LINK_CONFIG rate field (0=Gen4 1=Gen3 2=Gen2 3=Gen1)");
int cmp_pcie_e1 = 1;
module_param(cmp_pcie_e1, int, 0644);
MODULE_PARM_DESC(cmp_pcie_e1, "Replay the A100 Gen4 PHY-init block (E1) before retraining");

static void
nv_cmp170hx_gen3_post(nv_state_t *nv)
{
    nv_linux_state_t *nvl = NV_GET_NVL_FROM_NV_STATE(nv);
    struct pci_dev *gpu = nvl->pci_dev;
    struct pci_dev *upstream;
    void __iomem *bar0;
    u16 gpu_ctl2, upstream_ctl2, upstream_ctl, link_status = 0;
    u32 v, dlmgr, rate_code, pl_code, tls;
    int ret, attempt, i, pos;

    if ((gpu == NULL) || (gpu->device != 0x20c2))
        return;

    upstream = pci_upstream_bridge(gpu);
    if (upstream == NULL)
        return;

    rate_code = (cmp_pcie_rate > 0) ? (u32)cmp_pcie_rate
                                    : ((cmp_pcie_gen >= 4) ? 0U : 1U);
    pl_code   = (u32)cmp_pcie_gen;
    tls       = (u32)cmp_pcie_gen;

    bar0 = ioremap(pci_resource_start(gpu, 0), 0x90000);
    if (bar0 == NULL)
        return;

    NV_DEV_PRINTF(NV_DBG_ERRORS, nv,
                  "CMP Gen%u: bring-up start LC0=0x%08x CYA=0x%08x PL=0x%08x CE4=0x%08x\n",
                  cmp_pcie_gen, readl(bar0 + 0x8c040), readl(bar0 + 0x8c2c0),
                  readl(bar0 + 0x8c1c0), readl(bar0 + 0x88ce4));

    if (cmp_pcie_e1)
    {
        /* ---- Phase A: Gen3 step (a 5 GT/s link cannot retrain straight to 16) ---- */
        v = readl(bar0 + 0x8841c);
        writel((v | 0x2800U) & ~(0x1000U | 0x4000U | 0xc0000000U), bar0 + 0x8841c);
        v = readl(bar0 + 0x8c2c0);
        writel(v & ~0x0cU, bar0 + 0x8c2c0);
        v = readl(bar0 + 0x880a8);
        writel((v & ~0xfU) | 3U, bar0 + 0x880a8);
        v = readl(bar0 + 0x8c1c0);
        writel((v & ~0x000f0000U) | (3U << 16), bar0 + 0x8c1c0);
        v = readl(bar0 + 0x88610);
        writel(v | 0x1000U, bar0 + 0x88610);
        v = readl(bar0 + 0x8c040);
        writel((v & ~(0x000c0000U | 0x00100000U)) | (1U << 18) | 0x00400000U,
               bar0 + 0x8c040);
        dlmgr = readl(bar0 + 0x8b8c0);
        writel(dlmgr | 4U, bar0 + 0x8b8c0);
        for (i = 0; i < 200; i++)
        {
            v = readl(bar0 + 0x8c040);
            if ((v & 0x1fU) == 0 || v == 0xffffffffU)
                break;
            msleep(1);
        }
        if (v != 0xffffffffU && (v & 0x1fU) == 0)
        {
            writel((v & ~0xfU) | 1U, bar0 + 0x8c040);
            for (i = 0; i < 200; i++)
            {
                v = readl(bar0 + 0x8c040);
                if ((v & 0x1fU) == 0 || v == 0xffffffffU)
                    break;
                msleep(1);
            }
        }
        if (v != 0xffffffffU)
            writel(dlmgr, bar0 + 0x8b8c0);
        if (v != 0xffffffffU && (v & 0x1fU) == 0)
            writel(0x8U, bar0 + 0x8872c);

        /* Perform Equalization is required for 8/16 GT/s on both ends */
        pos = pci_find_ext_capability(gpu, PCI_EXT_CAP_ID_SECPCI);
        if (pos)
        {
            u32 lc3 = 0;
            pci_read_config_dword(gpu, pos + 4, &lc3);
            pci_write_config_dword(gpu, pos + 4, lc3 | 0x2U);
        }
        pos = pci_find_ext_capability(upstream, PCI_EXT_CAP_ID_SECPCI);
        if (pos)
        {
            u32 lc3 = 0;
            pci_read_config_dword(upstream, pos + 4, &lc3);
            pci_write_config_dword(upstream, pos + 4, lc3 | 0x2U);
        }

        msleep(50);

        /* ---- Phase B: E1 replay of the suppressed A100 Gen4 PHY-init ---- */
        writel(0x00000014U, bar0 + 0x88ce4);
        v = readl(bar0 + 0x88ce0);
        writel((v & ~0x3fU) | 0x06U, bar0 + 0x88ce0);
        writel(0x060711b2U, bar0 + 0x8c2c0);      /* CYA_0 = A100 value */
        writel(0x00040036U, bar0 + 0x8c1c0);      /* PL_LINK_RATE gen = 4 */
        v = readl(bar0 + 0x8c040);
        writel(v & ~0x000c0000U, bar0 + 0x8c040); /* rate = 0 -> Gen4 */
        writel(0x00001001U, bar0 + 0x88610);
    }
    else
    {
        v = readl(bar0 + 0x8c040);
        writel((v & ~0x000c0000U) | (rate_code << 18), bar0 + 0x8c040);
    }

    NV_DEV_PRINTF(NV_DBG_ERRORS, nv,
                  "CMP Gen%u: XP programmed LC0=0x%08x CYA=0x%08x PL=0x%08x\n",
                  cmp_pcie_gen, readl(bar0 + 0x8c040), readl(bar0 + 0x8c2c0),
                  readl(bar0 + 0x8c1c0));
    (void)pl_code;
    iounmap(bar0);
    msleep(100);

    /* ---- Phase C: config-space retrain (bypasses RM/GSP) ---- */
    ret = pcie_capability_read_word(gpu, PCI_EXP_LNKCTL2, &gpu_ctl2);
    if (ret) goto cap_err;
    ret = pcie_capability_read_word(upstream, PCI_EXP_LNKCTL2, &upstream_ctl2);
    if (ret) goto cap_err;

    gpu_ctl2 = (gpu_ctl2 & ~0xfU) | (u16)tls;
    upstream_ctl2 = (upstream_ctl2 & ~0xfU) | (u16)tls;

    ret = pcie_capability_write_word(gpu, PCI_EXP_LNKCTL2, gpu_ctl2);
    if (ret) goto cap_err;
    ret = pcie_capability_write_word(upstream, PCI_EXP_LNKCTL2, upstream_ctl2);
    if (ret) goto cap_err;

    ret = pcie_capability_read_word(upstream, PCI_EXP_LNKCTL, &upstream_ctl);
    if (ret) goto cap_err;
    ret = pcie_capability_write_word(upstream, PCI_EXP_LNKCTL,
                                     upstream_ctl | PCI_EXP_LNKCTL_RL);
    if (ret) goto cap_err;

    for (attempt = 0; attempt < 40; attempt++)
    {
        msleep(50);
        ret = pcie_capability_read_word(gpu, PCI_EXP_LNKSTA, &link_status);
        if (ret)
            goto cap_err;
        if ((link_status & PCI_EXP_LNKSTA_CLS) >= tls)
            break;
    }

    if ((link_status & PCI_EXP_LNKSTA_CLS) >= tls)
        NV_DEV_PRINTF(NV_DBG_ERRORS, nv,
                      "CMP Gen%u: PCIe link trained (LNKSTA=0x%04x)\n",
                      cmp_pcie_gen, link_status);
    else
        NV_DEV_PRINTF(NV_DBG_ERRORS, nv,
                      "CMP Gen%u: retrain did not reach target (LNKSTA=0x%04x ret=%d)\n",
                      cmp_pcie_gen, link_status, ret);
    return;

cap_err:
    NV_DEV_PRINTF(NV_DBG_ERRORS, nv,
                  "CMP Gen%u: config-space access failed ret=%d\n",
                  cmp_pcie_gen, ret);
}

