static void
nv_cmp170hx_gen3_post(nv_state_t *nv)
{
    nv_linux_state_t *nvl = NV_GET_NVL_FROM_NV_STATE(nv);
    struct pci_dev *gpu = nvl->pci_dev;
    struct pci_dev *upstream;
    void __iomem *bar0;
    u16 gpu_ctl2, upstream_ctl2, upstream_ctl, link_status = 0;
    u32 tls = (u32)cmp_pcie_gen;
    u32 v, dlmgr, cap0 = 0;
    int ret, attempt, i;

    if ((gpu == NULL) || (gpu->device != 0x20c2))
        return;

    upstream = pci_upstream_bridge(gpu);
    if (upstream == NULL)
    {
        NV_DEV_PRINTF(NV_DBG_ERRORS, nv,
                      "CMP Gen%u: no upstream PCIe bridge; skipping retrain\n",
                      cmp_pcie_gen);
        return;
    }
    if (tls < 3) tls = 3;
    if (tls > 4) tls = 4;

    bar0 = ioremap(pci_resource_start(gpu, 0), 0x90000);
    if (bar0 == NULL)
    {
        NV_DEV_PRINTF(NV_DBG_ERRORS, nv,
                      "CMP Gen%u: cannot map BAR0; skipping retrain\n", cmp_pcie_gen);
        return;
    }

    /* Re-assert the Gen3/4 XP configuration that GSP-RM clamps back to Gen2
     * after its boot, then advertise the target rate in LINK_CAP. */
    v = readl(bar0 + 0x8841c);
    writel((v | ((1U << 11) | (1U << 13))) &
           ~((1U << 12) | (1U << 14) | 0xc0000000U), bar0 + 0x8841c);
    v = readl(bar0 + 0x8c2c0);
    writel(v & ~((1U << 2) | (1U << 3)), bar0 + 0x8c2c0);
    v = readl(bar0 + 0x880a8);
    writel(((v & ~0x0000000fU) | tls) | 0x000f0000U, bar0 + 0x880a8);
    v = readl(bar0 + 0x8c1c0);
    writel((v & ~0x000f0000U) | ((tls >= 4) ? 0x00040000U : 0x00030000U),
           bar0 + 0x8c1c0);
    v = readl(bar0 + 0x88610);
    writel((v | 0x1000U) | 0x1U, bar0 + 0x88610);
    v = readl(bar0 + 0x8c040);
    writel((v & ~0x000c0000U) | ((tls >= 4) ? 0U : (1U << 18)), bar0 + 0x8c040);
    dlmgr = readl(bar0 + 0x8b8c0);
    writel(dlmgr | 4U, bar0 + 0x8b8c0);
    for (i = 0; i < 100000; i++)
    {
        v = readl(bar0 + 0x8c040);
        if (v == 0xffffffffU || (v & 0x1fU) == 0)
            break;
    }
    if (v != 0xffffffffU && (v & 0x1fU) == 0)
    {
        writel((v & ~0xfU) | 1U, bar0 + 0x8c040);
        for (i = 0; i < 100000; i++)
        {
            v = readl(bar0 + 0x8c040);
            if (v == 0xffffffffU || (v & 0x1fU) == 0)
                break;
        }
    }
    if (v != 0xffffffffU)
        writel(dlmgr, bar0 + 0x8b8c0);
    if (v != 0xffffffffU && (v & 0x1fU) == 0)
        writel(0x8U, bar0 + 0x8872c);
    (void)readl(bar0 + 0x8872c);
    cap0 = readl(bar0 + 0x88084);
    iounmap(bar0);
    msleep(50);

    NV_DEV_PRINTF(NV_DBG_ERRORS, nv,
                  "CMP Gen%u: XP LINK_CAP=0x%08x LINK_CONFIG=0x%08x before retrain\n",
                  cmp_pcie_gen, cap0, v);

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
        msleep(100);
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
