# 实测数据与时间线（2026-11-26）

> 全部为同一台机器、同一对卡、同一驱动的连续测量。原始 dmesg 见 `logs/dmesg-full.log`，
> 关键行筛选见 `logs/boot-evidence.log`，链路状态见 `logs/link-state.log`。

## 0. 环境

| 项 | 值 |
|---|---|
| CPU / 内存 | AMD Ryzen 7 9700X（8C16T，单 NUMA）/ 4×32 GB DDR5-5200 |
| 系统 | Ubuntu 24.04，kernel `6.8.0-139-generic` |
| GPU | 2× CMP 170HX（`10de:20c2` rev a1），81920 MiB，SM 124 / 122 |
| 坞站 | PEX88096（两颗 Broadcom PEX880xx Gen4 switch） |
| 拓扑 | `00:01.1` → `01:00.0` → `02:00.0` → `04:10.0` → `05:00.0`；`… → 06:00.0 → 07:00.0 → 08:00.0` |
| 驱动 | `open-gpu-kernel-modules 610.43.03`（cmp-unlocker v0.5 oc-80g-gen3 + 本仓库 5 个补丁） |
| cmdline | `amd_iommu=on iommu=pt pci=disable_acs_redir=0000:00:01.1 gpu_unlock.targets=all` |

## 1. 启动时间线（冷启，成功那一次）

```
[14.928] GPU 0000:05:00.0: CMP Gen2: retrain skipped (cmp_pcie_gen=4)
[14.928] GPU 0000:05:00.0: CMP Gen4: bring-up start LC0=0x80084c00 CYA=0x068731b3 PL=0x00240036 CE4=0x0000003f
[14.983] GPU 0000:05:00.0: CMP Gen4: XP programmed LC0=0x80404c00 CYA=0x060711b2 PL=0x00240036
[15.087] GPU 0000:08:00.0: CMP Gen4: bring-up start ...
[15.143] GPU 0000:08:00.0: CMP Gen4: XP programmed ...
[15.146] GPU 0000:05:00.0: CMP Gen4: PCIe link trained (LNKSTA=0x1104)
[15.304] GPU 0000:08:00.0: CMP Gen4: PCIe link trained (LNKSTA=0x1104)
[24.798] GPU0->GPU1 METHOD3: dynamic BAR1 P2P map  hMem=0xcaf001a1 size=0x200000   bar1Off=0x400000 dmaBase=0x12000400000
[24.806] GPU0->GPU1 METHOD3: dynamic BAR1 P2P map  hMem=0xcaf001a4 size=0x1400000  bar1Off=0x400000 dmaBase=0x12000400000
[48.393] GPU1->GPU0 METHOD3: dynamic BAR1 P2P map  hMem=0xcaf004e3 size=0x20000000 bar1Off=0x400000 dmaBase=0x10000400000
```

`dmaBase` 的差异说明两卡各自用自己的 BAR1 窗口基址（`0x12000000000` / `0x10000000000`）+ `0x400000` 偏移。

## 2. 链路

```
00:01.1  LnkSta: Speed 16GT/s, Width x16
04:10.0  LnkSta: Speed 16GT/s, Width x16
05:00.0  LnkSta: Speed 16GT/s, Width x16
07:00.0  LnkSta: Speed 16GT/s, Width x16
08:00.0  LnkSta: Speed 16GT/s, Width x16
```

`LnkCtl2`（`CAP_EXP+30.w`）：`05:00.0 = 0004`、`08:00.0 = 0004`（服务兜底修正）、`04:10.0 / 07:00.0 = 0004`、`00:01.1 = 0044`。

## 3. 逐位完整性（`scripts/p2p_gate.py`）

| 尺寸 | 0→1 (GB/s) | 1→0 (GB/s) | mismatched |
|---|---|---|---|
| 4 MiB | 14.02 | 13.56 | 0 |
| 64 MiB | 16.31 | 16.10 | 0 |
| 256 MiB | 16.44 | 16.39 | 0 |
| 1 GiB | 16.79 | 16.86 | 0 |
| 2 GiB | 16.75 | — | 0 |

> 该脚本每档都做「填值 → 拷贝 → 逐元素比对」，吞吐包含校验开销，因此低于纯拷贝。
> 改造前同脚本：4 MiB / 64 MiB / 256 MiB / 1 GiB / 2 GiB 全部 `CORRUPT`，2 GiB 还会掉卡。

## 4. 纯拷贝吞吐（`scripts/p2p_rep.py`，1 GiB，预热后 3 轮）

| 轮次 | 单流 | 双流 |
|---|---|---|
| rep0 0→1 | 24.16 | **26.32** |
| rep0 1→0 | 24.41 | **26.32** |
| rep1 / rep2 | — | **26.31–26.32** 双向 |

## 5. 小包延迟（`scripts/p2p_lat2.py`，CUDA event，200 次）

| 尺寸 | 0→1 (µs) | 1→0 (µs) |
|---|---|---|
| 16 B | 17.06 | 20.93 |
| 256 B | 15.42 | 20.95 |
| 4 KiB | 15.48 | 21.02 |
| 64 KiB | 22.47 | 22.92 |
| 同卡（H2D 基线） | 6.14 | — |

固定开销 ~15–20 µs，来自 RM 的 peer 映射查表；对 16 字节粒度的 Lamport 写入场景需要按这个量级评估。

## 6. 单卡 H2D / D2H

| 卡 | H2D (GB/s) | D2H (GB/s) |
|---|---|---|
| `05:00.0` | 26.74 | 26.14 |
| `08:00.0` | 26.79 | 26.10 |

Gen4 x16 理论 31.5 GB/s（128b/130b），26.7 = 85%，属 PCIe 正常效率 → **H2D 已到墙**。

## 7. 路径对比（同一对卡，1 GiB）

| 路径 | 0→1 | 1→0 | 数据 |
|---|---|---|---|
| **BAR1 直连 + 动态映射（本方案）** | **26.32** | **26.31** | 正确 |
| mailbox（`RMPcieP2PType=0`）+ torch `copy_` | 20.29 | 26.27 | 正确（host 中转，全双工叠加） |
| mailbox + `cudaMemcpyPeerAsync`（CE） | 16.9 | 16.9 | 正确 |
| BAR1 + ACS 开 + 无动态映射 | “27” | “26” | **100% 错**（假速度） |
| BAR1 + ACS 关 + 无动态映射 | 16.75 | 16.73 | 仅 <64 GiB 窗口内正确 |

## 8. 稳定性

- `scripts/soak_percard.py`：2 GiB 张量 + 512 MiB pinned 往返 + 8192² matmul，每卡 460–471 轮，
  Xid = **0**，链路仍 16GT/s x16，SM 1425 MHz（`clocks_throttle_reasons=0x4` = SW power cap），温度 51/45 ℃。
- 全程 `dmesg | grep -c Xid` = 0（见 `logs/xid.log`，空文件）。

## 9. 失败模式的日志指纹（排错用）

| 状态 | 关键日志 |
|---|---|
| 动态分支没进（缺 0002） | `M3DIAG pte-gate: ... bar1Sup=0` + `Assertion failed: ((base & RM_PAGE_MASK) == 0) @ kern_bus.c:298` |
| 能力开了但创建路径缺 | `kbusCreateP2PMapping_GP100: P2P type 2 is not supported` / `NV_ERR_NOT_SUPPORTED (0x56) @ p2p_api.c:599` |
| 正常 | `METHOD3: dynamic BAR1 P2P map GPU0->GPU1 ... dmaBase 0x...` + `bar1Sup=1` |
| Gen4 未上 | `CMP Gen4: retrain did not reach target (LNKSTA=0x1102 ret=0)` |
| ACS 未关的后果 | 吞吐虚高但 `mismatched != 0`，或 2 GiB 时 `Xid 45` / `Xid 109` |
