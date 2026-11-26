# CMP170HX-Gen4-DynBAR1

**在 2× NVIDIA CMP 170HX（GA100，80 GB）上同时打通 PCIe 4.0 x16 与真实 BAR1 P2P：双向 26.3 GB/s、逐位无误、Xid 0。**

> 一句话：静态 BAR1 identity 映射要求「BAR1 窗口 ≥ 显存」，而 170HX 是 64 GiB 窗口 / 80 GiB 显存。
> 本方案把 GH100 的**逐分配动态 BAR1 映射（METHOD3）**移植到 610.43.03 驱动，并补上 GA100 缺失的 **6 行 HAL 派发**。

英文摘要见文末 [English TL;DR](#english-tldr)。

---

## 结果

| 项目 | 结果 |
|---|---|
| 链路（五跳全链） | `Speed 16GT/s, Width x16`，GPU 端 `LnkSta=0x1104`、坞站下行口 `0xe104` |
| 显存 / SM | 81920 MiB ×2；SM 124（`05:00.0`）/ 122（`08:00.0`） |
| H2D / D2H | **26.74 / 26.14 GB/s**（Gen4 x16 理论 31.5，效率 85%） |
| P2P（BAR1 直连，1 GiB） | **26.31–26.32 GB/s 双向对称**（改造前 0→1 仅 22.7，且 ≥1 GiB 会掉卡） |
| P2P 完整性 | 4 MiB / 64 MiB / 256 MiB / 1 GiB / **2 GiB** 全部 `mismatched=0` |
| 小包延迟 | 16 B 17.06 µs、256 B 15.42 µs、4 KiB 15.48 µs、64 KiB 22.47 µs（同卡基线 6.14 µs） |
| 稳定性 | 25 s 双卡压测 Xid = 0 |
| `nvidia-smi` gen 显示 | 仍报 2/3 —— **GSP 固有误报**，只看 `lspci` 与实测 |

实测环境：Ryzen 7 9700X / 4×32 GB DDR5-5200 / Ubuntu 24.04 / kernel 6.8.0-139 / **PEX88096 显卡坞**（两颗 PEX880xx Gen4 switch）/ 驱动 `610.43.03`。

---

## 1. 问题拆成三块

### 1.1 链路被三层门压住（Gen2 → Gen4）

| 门 | 内容 | 处理 |
|---|---|---|
| 熔丝门 | `DISABLE_GEN3_SPEED` / `BOOT_GEN23_DIS` / `GEN3_DIS` | 由既有的 80 G 解锁流程烧掉（本仓库不涉及） |
| 注册表门 | `RMPcieLinkSpeed` 必须是 `0xF`（官方 `0x5` → `SET_PCIE_SPEED` 恒返回 `0x56`） | `cmp-gen3.conf` |
| PHY 门 | VBIOS 删掉了 Gen4 DevInit，**每次上电都要重放 A100 的 E1 序列**，且**必须在 Gen3 状态下重放** | `nv_cmp170hx_gen3_post()`（`patches/0003`） |

`nv_cmp170hx_gen3_post()` 三段式：

1. **Phase A**：写 XP 序列（`0x8841c`、`0x8c2c0`、`0x880a8`、`0x8c1c0`、`0x88610`、`0x8c040`、`0x8b8c0`、`0x8872c=8`），并对 GPU 与上游口各做一次 **Perform Equalization**（SECPCI 扩展能力 `+0x4 |= 0x2`）；
2. **Phase B**：重放 E1（`0x88ce4=0x14`、`0x88ce0=(old&~0x3F)|0x06`、`0x8c2c0=0x060711b2`、`0x8c1c0=0x00040036`、`0x8c040` rate 字段=0、`0x88610=0x00001001`）；
3. **Phase C**：config-space 重训（两端 `LNKCTL2` 目标=4，上游口 `LNKCTL |= RL`，40×50 ms 轮询）。

> **反向编码坑**：`LnkCap` 半字节 ↔ `LINK_CONFIG_0(0x8C040)[19:18]` 是反的（字段 0=Gen4、1=Gen3、2=Gen2、3=Gen1）。驱动原有的 gen2 late-clamp 会把字段写成 2，所以必须**先抬字段再重训**。

### 1.2 P2P 的“27 GB/s 假速度”与掉卡

社区给消费卡/计算卡解锁 P2P 用的是 GH100 的 **static BAR1 identity 映射**：

```c
peer_bus_addr = bar1BusAddr + fbPhysOffset;
```

它硬依赖 **`BAR1 窗口 ≥ 显存`**。本机 **BAR1 = 64 GiB**（`lspci` `Region 1: ... [size=64G]`）、**显存 80 GiB**，于是：

- 偏移落在 64–80 GiB 的分配 → 编出**窗口外的总线地址** → 数据静默损坏；
- “27 GB/s” 就是这个垃圾地址下的假速度（1 GiB 拷贝 100% 不匹配）；
- 2 GiB 拷贝触发 `Xid 45` + `Xid 109 CTX SWITCH TIMEOUT`，GPU1 掉出总线（`COMMAND=0000`），只能物理断电。

同一台机器上的 48G 魔改 4090 是 32 GiB BAR1 / 48 GiB FB —— 完全同构。

### 1.3 显卡坞（PEX88096）的 ACS 会吃掉真实 P2P

`PEX880xx` 下游口 `ACSCtl=0x0011`，bit0=SrcValid、**bit4=Upstream Forwarding**。UpstreamFwd 把 GPU↔GPU 的 P2P TLP 强行上送 root complex：BAR1 P2P 写**无处落地却照常报成功**。

处理：`config/cmp-gen4-acs-off.sh` + `cmp-gen4-acs-off.service`，在 `sysinit.target` 之前把所有 PEX880xx 端口 `ECAP_ACS+0x6.w` 清 0（每次开机都要做，固件会重置）。

---

## 2. 解法

### 2.1 逐分配动态 BAR1（METHOD3，`patches/0001`）

出自 [duanyll 的 48G 4090 方案](https://duanyll.com/2026/7/13/4090-48G-P2P/)（分支 `Duanyll/open-gpu-kernel-modules@595.71.05-p2p-48g`），本仓库收录其相对官方 595.71.05 的完整 diff（3 文件 / 926 行），在 610.43.03 上 dry-run 全中。

原理：不再整块 identity 映射，而是**按分配**把这块显存映进对端 BAR1 的一个 VA 槽（`kbusMapFbAperture_HAL`），偏移永远在窗口内，再由 GMMU PTE 翻译到高地址显存；peer PTE 编码用 `windowDmaBase + offset`。开销极小：2 卡 ring 只需 4 条 / 24 MB 级。

### 2.2 GA100 缺的 6 行 HAL 派发（`patches/0002`，本项目原创）

打完 METHOD3 后，GA100 上动态分支**仍然不生效**。诊断日志还原了完整因果链：

1. `src/nvidia/generated/g_kern_bus_nvoc.c` 的 **default 分支**把 GA100 指向 GP100 版 / 桩：
   - `__kbusCreateP2PMapping__` / `__kbusRemoveP2PMapping__` → `_GP100`
   - `__kbusCreateP2PMappingForBar1P2P__` / `__kbusRemoveP2PMappingForBar1P2P__` → `_395e98`（返回 `NV_ERR_NOT_SUPPORTED` 的桩）
   - `__kbusHasPcieBar1P2PMapping__` / `__kbusIsPcieBar1P2PMappingSupported__` → `_d69453`（恒 false）
2. 于是 `bar1Supported = 0`，METHOD3 的门 `isBar1P2PSupported && !kbusIsStaticBar1Enabled(...)` 永远进不去，退回 static identity 编码 → 1.2 的静默损坏。
   （日志：`M3DIAG pte-gate: ... bar1Sup=0 staticPeer=0 staticMap=0`）
3. 把前两个改指 `_GH100` 后 `bar1Supported = 1`，但 CUDA 初始化立刻失败：
   `kbusCreateP2PMapping_GP100: P2P type 2 is not supported` + `NV_ERR_NOT_SUPPORTED (0x56) @ p2p_api.c:599`
   —— 能力开了，**创建路径**还是 GP100（只认 NVLINK / NVLINK_INDIRECT / PCIE-mailbox）。
4. 再把 BAR1 的两个创建/拆除入口也改指 `_GH100`：`kbusCreateP2PMappingForBar1P2P_GH100()` 内部自带守卫
   `if (kbusIsStaticBar1Enabled(both)) _kbusCreateStaticBar1IOMMUMappingForGpuPair(...)`，static 关闭时跳过、只做引用计数 → 成功。

最终补丁只有 6 行（`patches/0002`，1834 B）：

```c
// src/nvidia/generated/g_kern_bus_nvoc.c，每个 "// default" else 分支
pThis->__kbusCreateP2PMapping__              = &kbusCreateP2PMapping_GH100;
pThis->__kbusRemoveP2PMapping__              = &kbusRemoveP2PMapping_GH100;
pThis->__kbusCreateP2PMappingForBar1P2P__    = &kbusCreateP2PMappingForBar1P2P_GH100;
pThis->__kbusRemoveP2PMappingForBar1P2P__    = &kbusRemoveP2PMappingForBar1P2P_GH100;
pThis->__kbusHasPcieBar1P2PMapping__         = &kbusHasPcieBar1P2PMapping_GH100;
pThis->__kbusIsPcieBar1P2PMappingSupported__ = &kbusIsPcieBar1P2PMappingSupported_GH100;
```

生效证据（冷启后首次跨卡拷贝）：

```
NVRM: _nvGpuOpsDynBar1Create: METHOD3: dynamic BAR1 P2P map GPU0->GPU1 hMem 0xcaf001a1 size 0x200000 bar1Off 0x400000 dmaBase 0x12000400000
NVRM: nvGpuOpsGetExternalAllocPtesOrPhysAddrs: M3DIAG pte-gate: ... bar1Sup=1 staticPeer=0 staticMap=0
```

### 2.3 配套（`patches/0004`、`0005`）

- `0004`：GSP 侧 **capforce**（把 `_kgspEncodeSystemInfo` 上报的 `linkCap` MaxSpeed 强推为 3），外加把 gen2 收尾跳过（`cmp_pcie_gen >= 3` 时 `retrain skipped`）；
- `0005`：`kernel_gsp_tu102.c` 的 GSP 阶段 Gen3 序列。

---

## 3. 快速开始

```bash
git clone https://github.com/elongwz/CMP170HX-Gen4-DynBAR1.git
cat docs/REPRODUCE.md      # 从零复现：打补丁 → 安全重编 → 系统配置 → 物理断电冷启 → 6 项验收
```

三条铁律（否则一定翻车）：

1. **不要改 `build.sh` / `patches/`**：cmp-unlocker 的 `BUILD_STAMP` 会变 → 源码树被 `rm -rf` 重新解压，手工补丁全丢；
2. **不要热重载驱动**：用 `tools/build_noreload.sh`（把 `modprobe`/`systemctl` 用空壳 shim 屏蔽），改完**物理断电 15 s** 冷启；
3. **验收必须看逐位比对**：只看吞吐会被“假 27 GB/s”骗，`scripts/p2p_gate.py` 必须 `mismatched=0`。

---

## 4. 仓库结构

```
.
├── README.md                 本文件（原理 + 结论 + 坑）
├── docs/
│   ├── REPRODUCE.md          从零复现步骤（可照抄）
│   └── RESULTS.md            全部实测原始数据与时间线
├── patches/
│   ├── 0001-method3-dynamic-bar1.patch    duanyll 595 fork 的 diff（3 文件 / 926 行）
│   ├── 0002-ga100-hal-dispatch.patch       GA100 → GH100 的 6 行派发（原创）
│   ├── 0003-ga100-gen4-bringup.patch       nv.c 三段式 Gen4 bring-up
│   ├── 0004-gsp-capforce-and-gen2.patch    GSP capforce + gen2 收尾 guard
│   └── 0005-tu102-gen3seq.patch            GSP 阶段 Gen3 序列
├── config/
│   ├── cmp-gen3.conf                     modprobe 配置（Gen4 + BAR1 + ForceP2P）
│   ├── cmp-gen4-acs-off.sh               关 PEX880xx ACS
│   ├── cmp-gen4-acs-off.service
│   └── cmp-gen4-lnkctl2.service          兜底把两卡 LnkCtl2 写回 4
├── scripts/                  验收/诊断脚本（见 docs/REPRODUCE.md 第 6-7 节）
├── tools/build_noreload.sh   安全重编（屏蔽自动热重载）
├── logs/                     冷启到验收的 dmesg 与链路证据
└── SHA256SUMS.txt
```

> **关于源码**：完整打完补丁的 `nv.c` / `nv_gpu_ops.c` 等 NVIDIA 源文件不入库（体积 + 版权），请按 `docs/REPRODUCE.md` 从 `open-gpu-kernel-modules-610.43.03` 官方包复现；`patches/` 足以精确还原。

---

## 5. 关键配置

`/etc/modprobe.d/cmp-gen3.conf`：

```
options nvidia cmp_pcie_gen=4 NVreg_RegistryDwords="RmForceEnableGen2=1;RMPcieLinkSpeed=0xF;CmpGen3=1;ForceP2P=0x11;RMPcieP2PType=1"
```

| 字段 | 作用 |
|---|---|
| `cmp_pcie_gen=4` | 目标代数（4=Gen4）；驱动内部先到 Gen3 再上 Gen4 |
| `RMPcieLinkSpeed=0xF` | 放开 RM 的速率写权限（`0x5` → 恒 `0x56`） |
| `CmpGen3=1` | 打开 nv.c 的 Gen4/Gen3 bring-up 与 GSP 侧序列 |
| `ForceP2P=0x11` | bits[1:0]=1 强制 P2P read、bits[5:4]=1 强制 P2P write（实测不抢 BAR1） |
| `RMPcieP2PType=1` | **BAR1 直连**；设 0 = mailbox 回落（P2P 变 20.3/26.3 非对称） |

必须启用的两个 systemd 单元：`cmp-gen4-acs-off.service`（sysinit 阶段清 ACS）、`cmp-gen4-lnkctl2.service`（nvidia 起来后修 GPU1 的 LnkCtl2）。

---

## 6. 已知限制 / 坑（血泪版）

1. **`nvidia-smi` 的 `pcie.link.gen.current` 永远报 2/3** —— GSP 固有认知，无法修，只看 `lspci -vv` 的 `LnkSta` + 实测带宽。
2. **不要热重载驱动**：`rmmod`/`modprobe` 在 BAR1/GSP 状态受损时会把整机卡死（SSH banner timeout，只能断电）。
3. **热重启不清 PHY**：改 conf/驱动后必须物理断电 15 s+。
4. **`M3DIAG` / `M3DIAG2` 诊断打印**仍编在驱动里（`LEVEL_ERROR`，每次跨卡映射刷几行）；不要就去 `nv_gpu_ops.c` / `kern_bus_gh100.c` 搜 `M3DIAG` 删掉重编。
5. **`08:00.0` 的 `LnkCtl2` 会被 gen2 收尾写回 2** → 必须有 `cmp-gen4-lnkctl2.service`。
6. **不要用 `CmpGen4=1`**（早期走法）：GSP 起不来（`No devices`，写 `0x881C0` 被拒 `0xbadf5040`）。
7. **`cmp-pcie-speed` 工具在本方案里没用**：`SET_PCIE_SPEED(3)` 依旧 `0x56`，Gen4 靠 nv.c 的 bring-up。
8. **显卡坞 ACS 每次开机被重置** → 必须 service 化，不能手工一次。

---

## 7. 相关项目与致谢

- [duanyll：给 48G 魔改 4090 解锁 PCIe P2P（动态 BAR1 映射）](https://duanyll.com/2026/7/13/4090-48G-P2P/) —— METHOD3 的来源；
- `Duanyll/open-gpu-kernel-modules@595.71.05-p2p-48g` —— 补丁母本；
- cmp-unlocker v0.5 / oc-80g-gen3 驱动树、`CMP170HX-Gen3-kit` 与 Gen4 实操手册 —— 80 G 解锁与链路上层门控；
- [canoziia/vllm-sm80](https://github.com/canoziia/vllm-sm80) —— 本项目动机场景（DeepSeek V4.1 Flash on SM80/170HX，Lamport P2P 需要 PXB 拓扑 + 真 P2P）。

---

## 8. 免责声明

本仓库仅包含**补丁与配置**，不含 NVIDIA 版权源码。修改内核驱动、操作熔丝、物理断电等动作有变砖风险，请自行评估；作者不对任何硬件损坏或数据丢失负责。

---

## English TL;DR

**CMP 170HX (GA100, 80 GB) ×2 — PCIe 4.0 x16 + real BAR1 P2P at 26.3 GB/s in both directions, bit-exact, zero Xid.**

The stock community P2P unlock relies on GH100's *static* BAR1 identity mapping, which requires `BAR1 aperture ≥ FB size`. On 170HX the aperture is 64 GiB while FB is 80 GiB, so any allocation above 64 GiB gets an out-of-window peer address: silent corruption at a fake "27 GB/s", and a hard hang (`Xid 45/109`) at 2 GiB copies.

This repo ports the *per-allocation dynamic BAR1* approach (METHOD3, from the 48 GB RTX 4090 work) to the 610.43.03 kernel modules and adds the missing piece on GA100: six HAL dispatch lines in `g_kern_bus_nvoc.c` that point the BAR1-P2P entry points at the `_GH100` implementations instead of `_GP100` / not-supported stubs. Without them `bar1Supported` stays 0 and the dynamic path is never taken.

Also included: the three-stage Gen4 bring-up (`nv_cmp170hx_gen3_post()`), GSP `capforce`, and an ACS-off service for PEX88096 docks (Upstream Forwarding silently drops BAR1 P2P writes).

See `docs/REPRODUCE.md` for the full recipe.
