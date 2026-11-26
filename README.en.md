# CMP170HX-Gen4-DynBAR1

**PCIe 4.0 x16 *and* real BAR1 P2P on dual NVIDIA CMP 170HX (GA100, 80 GB): 26.3 GB/s in both directions, bit-exact, zero Xid.**

> TL;DR — the community P2P unlock uses GH100's *static* BAR1 identity mapping, which requires `BAR1 aperture ≥ FB size`.
> On the 170HX the aperture is 64 GiB while FB is 80 GiB. This repo ports the **per-allocation dynamic BAR1 (METHOD3)**
> scheme to the 610.43.03 kernel modules and adds the **six HAL dispatch lines** that are missing on GA100.

---

## Results

| Item | Result |
|---|---|
| Link (all five hops) | `Speed 16GT/s, Width x16`; GPU `LnkSta=0x1104`, dock downstream port `0xe104` |
| Memory / SM | 81920 MiB × 2; SM 124 (`05:00.0`) / 122 (`08:00.0`) |
| H2D / D2H | **26.74 / 26.14 GB/s** (Gen4 x16 raw is 31.5 GB/s → 85 %, i.e. at the wall) |
| P2P (BAR1 direct, 1 GiB) | **26.31–26.32 GB/s, symmetric** (before the fix: 22.7 for 0→1, and ≥1 GiB would wedge the GPU) |
| P2P integrity | 4 MiB / 64 MiB / 256 MiB / 1 GiB / **2 GiB** all `mismatched=0` |
| Latency | 16 B 17.06 µs, 256 B 15.42 µs, 4 KiB 15.48 µs, 64 KiB 22.47 µs (same-GPU baseline 6.14 µs) |
| Stability | 25 s dual-GPU soak, Xid = 0 |
| `nvidia-smi` gen display | still reports 2/3 — **a GSP artifact**; trust `lspci` and measured bandwidth |

Test rig: Ryzen 7 9700X / 4×32 GB DDR5-5200 / Ubuntu 24.04 / kernel 6.8.0-139 / **PEX88096 dock**
(two PEX880xx Gen4 switches) / driver `610.43.03`.

---

## 1. The problem in three pieces

### 1.1 Link rate is gated three times (Gen2 → Gen4)

| Gate | What | Handling |
|---|---|---|
| Fuse gate | `DISABLE_GEN3_SPEED` / `BOOT_GEN23_DIS` / `GEN3_DIS` | Burned away by the existing 80 GB unlock (out of scope here) |
| Registry gate | `RMPcieLinkSpeed` must be `0xF` (the official `0x5` makes `SET_PCIE_SPEED` return `0x56` forever) | `cmp-gen3.conf` |
| PHY gate | the VBIOS has the Gen4 DevInit stripped, so **A100's E1 sequence must be replayed on every power-on**, and **only while the link is at Gen3** | `nv_cmp170hx_gen3_post()` (`patches/0003`) |

`nv_cmp170hx_gen3_post()` runs in three phases:

1. **Phase A** — program the XP block (`0x8841c`, `0x8c2c0`, `0x880a8`, `0x8c1c0`, `0x88610`, `0x8c040`, `0x8b8c0`, `0x8872c=8`) and run **Perform Equalization** on both the GPU and the upstream port (SECPCI extended capability `+0x4 |= 0x2`);
2. **Phase B** — replay E1 (`0x88ce4=0x14`, `0x88ce0=(old&~0x3F)|0x06`, `0x8c2c0=0x060711b2`, `0x8c1c0=0x00040036`, `0x8c040` rate field = 0, `0x88610=0x00001001`);
3. **Phase C** — config-space retrain (both `LNKCTL2` targets = 4, upstream `LNKCTL |= RL`, 40 × 50 ms polling).

> **Reversed encoding trap**: the `LnkCap` nibble and `LINK_CONFIG_0(0x8C040)[19:18]` are inverted (field 0 = Gen4, 1 = Gen3, 2 = Gen2, 3 = Gen1). The stock gen2 late-clamp writes field 2, so you must **raise the field first, then retrain**.

### 1.2 The "27 GB/s" fake speed and the hang

The community P2P unlock uses GH100's **static BAR1 identity mapping**:

```c
peer_bus_addr = bar1BusAddr + fbPhysOffset;
```

It hard-depends on **`BAR1 aperture ≥ FB size`**. Here **BAR1 = 64 GiB** (`lscpi Region 1: ... [size=64G]`) and **FB = 80 GiB**, therefore:

- allocations with a physical offset in the 64–80 GiB range encode a **bus address outside the window** → silent data corruption;
- the "27 GB/s" number is that garbage address running at full speed (1 GiB copy: 100 % mismatch);
- a 2 GiB copy triggers `Xid 45` + `Xid 109 CTX SWITCH TIMEOUT` and drops GPU1 off the bus (`COMMAND=0000`); only a physical power cycle recovers it.

The 48 GB modded RTX 4090 is 32 GiB BAR1 / 48 GiB FB — structurally identical.

### 1.3 The dock (PEX88096) eats real P2P via ACS

`PEX880xx` downstream ports have `ACSCtl = 0x0011`: bit0 = SrcValid, **bit4 = Upstream Forwarding**. UpstreamFwd pushes GPU↔GPU P2P TLPs up to the root complex, so BAR1 P2P writes **land nowhere but still report success**.

Fix: `config/cmp-gen4-acs-off.sh` + `cmp-gen4-acs-off.service`, clearing `ECAP_ACS+0x6.w` on every PEX880xx port before `sysinit.target` (firmware resets it on every boot).

---

## 2. The fix

### 2.1 Per-allocation dynamic BAR1 (METHOD3, `patches/0001`)

From [duanyll's 48 GB 4090 work](https://duanyll.com/2026/7/13/4090-48G-P2P/) (branch `Duanyll/open-gpu-kernel-modules@595.71.05-p2p-48g`). The patch here is that branch's full diff against stock 595.71.05 (3 files / 926 lines) and applies to **610.43.03 after uild.sh has applied its 16 base patches** with offsets only (89–97 lines). Apply it to that prepared tree, not to the bare upstream source: one hunk conflicts there because a base patch touches the same region.

Idea: instead of one identity mapping of the whole FB, map **each allocation** into a VA slot of the peer's BAR1 (`kbusMapFbAperture_HAL`), so the offset is always inside the window; GMMU PTEs then translate to the high FB address, and the peer PTE encodes `windowDmaBase + offset`. Overhead is tiny: a 2-GPU ring needs ~4 mappings / ~24 MB.

### 2.2 The six HAL dispatch lines GA100 is missing (`patches/0002`, original work here)

After porting METHOD3 the dynamic branch still never ran on GA100. The diagnostic chain:

1. `src/nvidia/generated/g_kern_bus_nvoc.c` routes GA100's **`// default` else branch** to GP100 code or stubs:
   - `__kbusCreateP2PMapping__` / `__kbusRemoveP2PMapping__` → `_GP100`
   - `__kbusCreateP2PMappingForBar1P2P__` / `__kbusRemoveP2PMappingForBar1P2P__` → `_395e98` (a stub returning `NV_ERR_NOT_SUPPORTED`)
   - `__kbusHasPcieBar1P2PMapping__` / `__kbusIsPcieBar1P2PMappingSupported__` → `_d69453` (always false)
2. So `bar1Supported = 0` and METHOD3's gate `isBar1P2PSupported && !kbusIsStaticBar1Enabled(...)` is never true → the driver falls back to static identity encoding → §1.2 corruption. (`M3DIAG pte-gate: ... bar1Sup=0`)
3. Pointing the first two at `_GH100` makes `bar1Supported = 1`, but CUDA init then fails:
   `kbusCreateP2PMapping_GP100: P2P type 2 is not supported` + `NV_ERR_NOT_SUPPORTED (0x56) @ p2p_api.c:599` — capability open, **creation path** still Pascal (NVLINK / NVLINK_INDIRECT / PCIE-mailbox only).
4. Pointing the BAR1 create/remove entry points at `_GH100` fixes it: `kbusCreateP2PMappingForBar1P2P_GH100()` already guards with `if (kbusIsStaticBar1Enabled(both)) _kbusCreateStaticBar1IOMMUMappingForGpuPair(...)`, so with static BAR1 off it only bumps the refcount → success.

The final patch is exactly six lines (`patches/0002`, 1834 B):

```c
// src/nvidia/generated/g_kern_bus_nvoc.c, every "// default" else branch
pThis->__kbusCreateP2PMapping__              = &kbusCreateP2PMapping_GH100;
pThis->__kbusRemoveP2PMapping__              = &kbusRemoveP2PMapping_GH100;
pThis->__kbusCreateP2PMappingForBar1P2P__    = &kbusCreateP2PMappingForBar1P2P_GH100;
pThis->__kbusRemoveP2PMappingForBar1P2P__    = &kbusRemoveP2PMappingForBar1P2P_GH100;
pThis->__kbusHasPcieBar1P2PMapping__         = &kbusHasPcieBar1P2PMapping_GH100;
pThis->__kbusIsPcieBar1P2PMappingSupported__ = &kbusIsPcieBar1P2PMappingSupported_GH100;
```

Proof, from the first cross-GPU copy after a cold boot (the `METHOD3` line is `LEVEL_INFO` in the
patch, so we raised it to `LEVEL_ERROR` with `scripts/m3diag*.py` for the run below — nothing else changed):

```
NVRM: _nvGpuOpsDynBar1Create: METHOD3: dynamic BAR1 P2P map GPU0->GPU1 hMem 0xcaf001a1 size 0x200000 bar1Off 0x400000 dmaBase 0x12000400000
NVRM: nvGpuOpsGetExternalAllocPtesOrPhysAddrs: M3DIAG pte-gate: ... bar1Sup=1 staticPeer=0 staticMap=0
```

> To decide whether the dynamic mapping is really live, trust the bit-exact check (`scripts/p2p_gate.py`,
> every size `mismatched=0` and 2 GiB not crashing), **not** these log lines.

The full investigation is written up as a tutorial in [docs/CASE-STUDY.md](docs/CASE-STUDY.md).

### 2.3 Supporting patches (`0004`, `0005`)

- `0004` — GSP-side **capforce**: force the `linkCap` MaxSpeed reported by `_kgspEncodeSystemInfo` to 3, plus skip the gen2 late clamp when `cmp_pcie_gen >= 3`;
- `0005` — the GSP-time Gen3 sequence in `kernel_gsp_tu102.c`.

---

## 3. Quick start

```bash
git clone https://github.com/elongwz/CMP170HX-Gen4-DynBAR1.git
cat docs/REPRODUCE.md      # apply patches → safe rebuild → system config → cold boot → acceptance checks (link / memory / Gen4 log / bit-exact / throughput)
```

Three rules you must not break:

1. **Never edit `build.sh` or `patches/`** of the cmp-unlocker tree — `BUILD_STAMP` changes, the source tree is `rm -rf`'d and re-extracted, and your manual patches are gone;
2. **Never hot-reload the driver** — use `tools/build_noreload.sh` (shims `modprobe`/`systemctl`), then **physically power off for 15 s**;
3. **Always verify bit-exactness**, not just throughput — `scripts/p2p_gate.py` must report `mismatched=0`; otherwise the "27 GB/s" will fool you.

---

## 4. Repository layout

```
.
├── README.md / README.en.md   this file (Chinese / English)
├── docs/
│   ├── REPRODUCE.md           step-by-step recipe (copy-paste ready, Chinese)
│   ├── CASE-STUDY.md          the debugging tutorial: bar1Sup=0 → 6 lines → CUDA init → 4 more lines
│   └── RESULTS.md             raw measurements, boot timeline, failure fingerprints
├── patches/
│   ├── 0001-method3-dynamic-bar1.patch   duanyll 595 fork diff (3 files / 926 lines)
│   ├── 0002-ga100-hal-dispatch.patch     the six GA100 → GH100 dispatch lines (original)
│   ├── 0003-ga100-gen4-bringup.patch     nv.c three-stage Gen4 bring-up
│   ├── 0004-gsp-capforce-and-gen2.patch  GSP capforce + gen2 clamp guard
│   └── 0005-tu102-gen3seq.patch          GSP-time Gen3 sequence
├── config/                    modprobe recipe; ACS-off script+service; LnkCtl2 script+service
├── scripts/                   verification and diagnostic tools
├── tools/build_noreload.sh    safe rebuild (no auto reload)
├── logs/                      dmesg and link evidence
└── SHA256SUMS.txt
```

> **About sources**: the patched NVIDIA source files (`nv.c`, `nv_gpu_ops.c`, …) are *not* committed (size + copyright). Reproduce them from the official `open-gpu-kernel-modules-610.43.03` package as described in `docs/REPRODUCE.md`; `patches/` is sufficient and exact.

---

## 5. Configuration

`/etc/modprobe.d/cmp-gen3.conf`:

```
options nvidia cmp_pcie_gen=4 NVreg_RegistryDwords="RmForceEnableGen2=1;RMPcieLinkSpeed=0xF;CmpGen3=1;ForceP2P=0x11;RMPcieP2PType=1"
```

| Key | Meaning |
|---|---|
| `cmp_pcie_gen=4` | target generation (4 = Gen4); the driver steps through Gen3 first |
| `RMPcieLinkSpeed=0xF` | unlocks RM's link-speed write (with `0x5` you always get `0x56`) |
| `CmpGen3=1` | enables the nv.c bring-up and the GSP-side sequence |
| `ForceP2P=0x11` | bits[1:0]=1 force P2P read, bits[5:4]=1 force P2P write (does not steal BAR1) |
| `RMPcieP2PType=1` | **BAR1 direct**; `0` = mailbox fallback (asymmetric 20.3/26.3) |

Two systemd units must be enabled: `cmp-gen4-acs-off.service` (clear ACS at sysinit) and `cmp-gen4-lnkctl2.service` (fix GPU1's `LnkCtl2` back to 4 after the driver loads).

---

## 6. Caveats

1. **`nvidia-smi` will always report `pcie.link.gen.current = 2/3`** — a GSP belief, not reality. Use `lspci -vv` (`LnkSta`) and measured bandwidth.
2. **Never hot-reload**: `rmmod`/`modprobe` while BAR1/GSP state is broken wedges the whole machine (SSH banner timeout → power cycle).
3. **A warm reboot does not reset the PHY** — power off for 15 s+ after changing the driver or conf.
4. **Diagnostics are optional**: `patches/0001..0005` produce a clean build (no `M3DIAG`, and the `METHOD3` line stays at `LEVEL_INFO` so dmesg hides it). The measurements here came from a build with `scripts/m3diag*.py` applied; use those only when you need the logs.
5. `08:00.0`'s `LnkCtl2` gets rewritten to 2 by the gen2 late clamp → the fallback service is mandatory.
6. **Do not use `CmpGen4=1`** (an early approach): GSP fails to boot (`No devices`, writes to `0x881C0` are rejected with `0xbadf5040`).
7. **`cmp-pcie-speed` is useless here**: `SET_PCIE_SPEED(3)` still returns `0x56`; Gen4 comes from the nv.c bring-up.
8. **PEX88096 ACS is reset by firmware on every boot** — hence the service, not a one-off command.

---

## 7. Credits

- [duanyll — Unlocking PCIe P2P on a 48 GB modded RTX 4090 (dynamic BAR1 mapping)](https://duanyll.com/2026/7/13/4090-48G-P2P/) — where METHOD3 comes from;
- `Duanyll/open-gpu-kernel-modules@595.71.05-p2p-48g` — the patch base;
- cmp-unlocker v0.5 / oc-80g-gen3 driver tree, `CMP170HX-Gen3-kit` and the Gen4 playbook — 80 GB unlock and the upper link gates;
- [canoziia/vllm-sm80](https://github.com/canoziia/vllm-sm80) — the motivating workload (DeepSeek V4.1 Flash on SM80/170HX; CuTe Lamport P2P needs PXB topology and real BAR1 P2P).

---

## 8. Disclaimer

This repository contains **patches and configuration only** — no NVIDIA copyrighted source. Modifying kernel drivers, burning fuses and power-cycling hardware all carry risk of bricking; evaluate it yourself. The authors take no responsibility for hardware damage or data loss.

---

## 中文版

完整中文文档见 [README.md](README.md)。
