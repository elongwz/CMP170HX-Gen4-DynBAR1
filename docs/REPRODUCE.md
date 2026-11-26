# 复现步骤（从零到 26.3 GB/s 双向 P2P）

> 目标读者：另一台同型号机器（2×CMP 170HX 80G）或自己重装系统后重来。
> 全程**不要热重载驱动**，每次改驱动后必须**物理断电冷启**（热重启不清 PHY 状态）。
> 预计耗时：编译 15–25 分钟 + 2 次冷启。

---

## 0. 把仓库送到目标机

```bash
git clone https://github.com/elongwz/CMP170HX-Gen4-DynBAR1.git /home/gis/p2p/Gen4-DynBAR1
```

（离线机器也可以直接 `scp -r` 整个目录过去，路径保持一致即可。）
下文所有 `/home/gis/p2p/Gen4-DynBAR1/...` 均指这个目录。

---

## 0.1 前置条件检查

```bash
uname -r                                   # 期望 6.8.0-139-generic（其它版本也能编，改 KVER 即可）
ls /lib/modules/$(uname -r)/build >/dev/null && echo headers-ok
gcc --version | head -1
nvidia-smi --query-gpu=name,memory.total,pci.bus_id --format=csv   # 期望 81920 MiB ×2
lspci -d 10de:20c2 -nn                     # 期望两条 10de:20c2 rev a1
```

确认现在的 P2P 状态（应能预期到“假速度 + 掉卡”或“20/26 非对称”）：

```bash
ls /home/gis/bench/p2p_gate.py || { mkdir -p /home/gis/bench && cp /home/gis/p2p/Gen4-DynBAR1/scripts/p2p_gate.py /home/gis/bench/; }
python3 /home/gis/bench/p2p_gate.py        # 旧状态下会看到 CORRUPT() 或速度异常
```

驱动树（前提：已按 cmp-unlocker v0.5 的流程把 80G 解锁版驱动跑起来过）：

```bash
cd /home/gis/p2p/cmpunlocker-v0.5-oc-80g-gen3/driver
ls build.sh patches/ | head        # 应有 16 个基础补丁 + build.sh
cat .cmpunlocker-stamp             # 记录当前 stamp（改 build.sh/patches 会触发重新解压！）
```

> ⚠️ **最重要的一条**：`build.sh` 的 `BUILD_STAMP` 由 `VERSION:KVER:PROFILE:MCLK:PATCH_HASH:sha256(build.sh)` 组成。
> 只要 `build.sh` 或 `patches/` 有任何字节变化，脚本会 `rm -rf` 源码树重新解压重打补丁 —— 我们手工打进
> `.build/` 的补丁就会丢。因此**不要修改 `build.sh` 和 `patches/`**，只改 `.build/` 里的源码。

---

## 1. 先跑一次基础构建，让源码树落地

```bash
cd /home/gis/p2p/cmpunlocker-v0.5-oc-80g-gen3/driver
sudo bash build.sh 2>&1 | tee /tmp/build_base.log | tail -40
```

它会：解压 `open-gpu-kernel-modules-610.43.03` → 应用 16 个基础补丁 → geometry rewrite（80G 参数）→ 编译 → 安装 → 尝试热重载。
第一次构建时允许它热重载（此时还是原厂兼容配置，风险低）。

确认源码树位置：

```bash
SRC=/home/gis/p2p/cmpunlocker-v0.5-oc-80g-gen3/driver/.build/open-gpu-kernel-modules-610.43.03
ls $SRC/kernel-open/nvidia/nv.c $SRC/src/nvidia/generated/g_kern_bus_nvoc.c
```

---

## 2. 打我们的 5 个补丁

用仓库里的 `patches/`（已 clone 到 `/home/gis/p2p/Gen4-DynBAR1/patches/`），然后在源码根目录按顺序打：

> ⚠️ 必须打在 **`build.sh` 已经跑过、16 个基础补丁已就位**的树上（就是 §1 那一步）。
> 直接对官方原始 610.43.03 源码打，`0001` 更会冲突（基础补丁动过同一区域）。
>
> `0001` 是用 `--fuzz=3` 打的（下面命令已带）：它来自 595 源码树，其中
> `nvGpuOpsGetExternalAllocPtesOrPhysAddrs` 的原型改动 hunk 的 3 行上下文在 610 上漂了，
> 用默认 fuzz 会报 1/20 失败，加 `--fuzz=3` 后 20/20 干净落盘（已在本机 610 树上验证）。

```bash
SRC=/home/gis/p2p/cmpunlocker-v0.5-oc-80g-gen3/driver/.build/open-gpu-kernel-modules-610.43.03
P=/home/gis/p2p/Gen4-DynBAR1/patches
cd $SRC

# 1) 逐分配动态 BAR1（duanyll 595 fork 相对官方 595 的 diff，直接适用于 610）
sudo patch -p1 --fuzz=3 --dry-run < $P/0001-method3-dynamic-bar1.patch
sudo patch -p1 --fuzz=3 < $P/0001-method3-dynamic-bar1.patch

# 2) GA100 → GH100 的 6 行 HAL 派发（本项目原创，必须有，否则 bar1Supported 恒 0）
sudo patch -p1 --fuzz=3 --dry-run < $P/0002-ga100-hal-dispatch.patch
sudo patch -p1 --fuzz=3 < $P/0002-ga100-hal-dispatch.patch

# 3) Gen4 bring-up（nv.c）
sudo patch -p1 --fuzz=3 < $P/0003-ga100-gen4-bringup.patch

# 4) capforce + gen2 收尾 guard（kernel_gsp.c）
sudo patch -p1 --fuzz=3 < $P/0004-gsp-capforce-and-gen2.patch

# 5) GSP 阶段 Gen3 序列（kernel_gsp_tu102.c）
sudo patch -p1 --fuzz=3 < $P/0005-tu102-gen3seq.patch
```

校验（三条都应成立）：

```bash
grep -c "kbusCreateP2PMapping_GH100" $SRC/src/nvidia/generated/g_kern_bus_nvoc.c   # 期望 2（if 分支原有 + else 分支被改）
grep -c "kbusCreateP2PMappingForBar1P2P_GH100" $SRC/src/nvidia/generated/g_kern_bus_nvoc.c  # 期望 2
grep -c "kbusIsPcieBar1P2PMappingSupported_GH100" $SRC/src/nvidia/generated/g_kern_bus_nvoc.c # 期望 2
grep -c "METHOD3" $SRC/src/nvidia/src/kernel/gpu/bus/p2p_api.c                     # 1
grep -c "nv_cmp170hx_gen3_post" $SRC/kernel-open/nvidia/nv.c                        # 3（定义 + 注释 + 调用）
```

> 若某补丁 `--dry-run` 仍报失败，对照 `src/` 下我们最终的 5 个文件手工合（那 5 个文件是打过全部补丁的成品）。
> `0001` 的路径前缀是 `a/src/...`、`0003` 是 `a/kernel-open/...`，都在源码根用 `-p1` 即可。

> **关于诊断打印**：`0001..0005` 打出来的是**干净构建**（只有 `METHOD3: dynamic BAR1 P2P map ...`
> 这一条成功日志）。本文实测所用的驱动额外用 `scripts/m3diag.py` / `scripts/m3diag2.py` 加了
> `M3DIAG` / `M3DIAG2` 探针（并把那条成功日志的级别从 `LEVEL_INFO` 提到 `LEVEL_ERROR`），
> 它们**只影响日志，不影响功能**。要复现我们的 dmesg 才需要装，见 §9。

---

## 3. 安全重编（屏蔽自动热重载）

`build.sh` 末尾会自动 `modprobe -r nvidia` + `modprobe nvidia`。在 BAR1/GSP 状态受损时这会**把整机卡死**
（SSH banner timeout，只能物理断电）。用仓库自带的 `tools/build_noreload.sh`：

```bash
export SUDO_PW='你的sudo密码'
sudo -v   # 先刷新 sudo 时间戳，避免脚本后台拿不到密码
bash /home/gis/p2p/Gen4-DynBAR1/tools/build_noreload.sh /home/gis/p2p/cmpunlocker-v0.5-oc-80g-gen3
tail -f /tmp/build.log
```

成功判据（`/tmp/build.log` 末尾应有）：

```
Source tree already extracted and patched ... reusing it     ← stamp 未变，复用了打好补丁的树
...
[WARN] Could not unload nvidia modules                        ← 预期（被 shim 屏蔽）
Modules installed but running driver is still stock
nvidia.ko 已安装：/lib/modules/$(uname -r)/updates/cmpunlocker/nvidia.ko
```

二进制校验：

```bash
KO=/lib/modules/$(uname -r)/updates/cmpunlocker/nvidia.ko
ls -l $KO                                  # 期望约 35 MB，不能是 0 字节
nm $KO 2>/dev/null | grep -c kbusCreateP2PMapping_GH100    # ≥1
nm $KO 2>/dev/null | grep -c kbusCreateP2PMapping_GP100    # 0（已无派发指向它，通常会被裁掉）
strings $KO | grep -c "dynamic BAR1 P2P map"              # ≥1
strings $KO | grep -c M3DIAG                              # 0（干净构建；装了诊断才是 3，见 §9）
```

重建 initramfs：

```bash
sudo update-initramfs -u -k $(uname -r)
```

---

## 4. 系统配置

### 4.1 modprobe 配置

```bash
sudo cp /etc/modprobe.d/cmp-gen3.conf /home/gis/cmp-gen3.conf.bak-$(date +%Y%m%d-%H%M%S)
sudo tee /etc/modprobe.d/cmp-gen3.conf >/dev/null <<'EOF'
# CMP170HX Gen4 + 动态 BAR1 P2P
# RMPcieLinkSpeed=0xF：0x5 会让 SET_PCIE_SPEED 永远 0x56
# ForceP2P=0x11：bits[1:0]=1 强制 P2P read，bits[5:4]=1 强制 P2P write
# RMPcieP2PType=1：BAR1 直连（BAR1 64G < FB 80G，靠 nv_gpu_ops.c 的逐分配动态窗口）
options nvidia cmp_pcie_gen=4 NVreg_RegistryDwords="RmForceEnableGen2=1;RMPcieLinkSpeed=0xF;CmpGen3=1;ForceP2P=0x11;RMPcieP2PType=1"
EOF
```

### 4.2 关显卡坞 ACS（PEX880xx）

```bash
sudo install -m 755 /home/gis/p2p/Gen4-DynBAR1/config/cmp-gen4-acs-off.sh /usr/local/sbin/cmp-gen4-acs-off.sh
sudo install -m 644 /home/gis/p2p/Gen4-DynBAR1/config/cmp-gen4-acs-off.service /etc/systemd/system/cmp-gen4-acs-off.service
sudo systemctl daemon-reload
sudo systemctl enable cmp-gen4-acs-off.service
```

服务内容要点（不要改成 `multi-user.target` 之后触发，必须在 nvidia 之前）：

```
DefaultDependencies=no
After=sysinit.target
Before=basic.target nvidia-persistenced.service
WantedBy=sysinit.target
Type=oneshot / RemainAfterExit=yes
```

### 4.3 LnkCtl2 兜底

`08:00.0` 的 `LnkCtl2` 会被 gen2 收尾逻辑又写回 2，用服务在 nvidia 起来后改回 4：

```bash
sudo install -m 755 /home/gis/p2p/Gen4-DynBAR1/config/cmp-gen4-lnkctl2.sh /usr/local/sbin/cmp-gen4-lnkctl2.sh
sudo install -m 644 /home/gis/p2p/Gen4-DynBAR1/config/cmp-gen4-lnkctl2.service /etc/systemd/system/cmp-gen4-lnkctl2.service
sudo systemctl daemon-reload
sudo systemctl enable cmp-gen4-lnkctl2.service
```

### 4.4 （可选）IOMMU

本机 cmdline 原本就有 `amd_iommu=on iommu=pt`；实验中额外加过 `iommu.passthrough=1`（与 `iommu=pt` 等价），
**不是必需项**。保持原样即可。

---

## 5. 物理断电冷启

```bash
sudo poweroff
# 等完全断电后，拔掉电源线 15 秒以上（清 PHY 状态），再上电
```

> 热重启（`reboot`）不清 PHY，测出来的结果不可信。

---

## 6. 验收（逐项，缺一不可）

### 6.1 链路

```bash
for b in 00:01.1 01:00.0 04:10.0 05:00.0 07:00.0 08:00.0; do
  printf "%-8s " $b; lspci -v -s $b 2>/dev/null | grep -m1 LnkSta
done
```

期望：所有跳 `Speed 16GT/s, Width x16`（GPU 端 `0x1104`，坞站下行口 `0xe104`）。

### 6.2 显存 / 卡

```bash
nvidia-smi --query-gpu=index,name,memory.total,pci.bus_id --format=csv
```

期望：`81920 MiB` ×2。（`pcie.link.gen.current` 显示 2/3 是 GSP 误报，忽略。）

### 6.3 动态 BAR1 是否真的接管

```bash
# Gen4 三段（NV_DBG_ERRORS 级，干净构建里默认可见）
sudo dmesg | grep -E "CMP Gen4: (bring-up start|XP programmed|PCIe link trained)" | tail -10
# 动态窗口日志（见下方说明：干净构建里看不到，判据以 6.4 为准）
sudo dmesg | grep -E "METHOD3|M3DIAG" | tail -20
```

Gen4 期望（每卡三段）：

```
CMP Gen4: bring-up start LC0=0x80084c00 ...
CMP Gen4: XP programmed LC0=0x80404c00 ...
CMP Gen4: PCIe link trained (LNKSTA=0x1104)
```

> ⚠️ **干净构建下 `METHOD3` 那行不会出现在 dmesg**：它在补丁里是 `NV_PRINTF(LEVEL_INFO, ...)`，
> 而 NVIDIA 驱动默认只输出 warning 及以上。我们在实测时用 `scripts/m3diag.py` 把它提到了
> `LEVEL_ERROR`，所以下面的日志只在那次构建里可见：
>
> ```
> _nvGpuOpsDynBar1Create: METHOD3: dynamic BAR1 P2P map GPU0->GPU1 hMem 0x... bar1Off 0x400000 dmaBase 0x12000400000
> M3DIAG pte-gate: ... bar1Sup=1 staticPeer=0 staticMap=0        # 可选
> ```
>
> 因此「动态映射是否生效」的**权威判据是 6.4 的逐位校验**（4 MiB 起全 `mismatched=0` 且 2 GiB 不崩），
> 不是日志。要在干净构建里也能看到日志，把该行级别改成 `LEVEL_ERROR` 重编即可（一行改动）。

### 6.4 逐位完整性（关键，别只看速度）

```bash
mkdir -p /home/gis/bench
cp /home/gis/p2p/Gen4-DynBAR1/scripts/p2p_gate.py /home/gis/bench/ 2>/dev/null || true
python3 /home/gis/bench/p2p_gate.py
```

期望：`RESULT: PASS`，每一档都 `mismatched=0`，2 GiB 不崩。**任何一档 CORRUPT 都说明动态映射没生效**。

### 6.5 纯拷贝吞吐

```bash
python3 /home/gis/bench/p2p_rep.py        # 1 GiB × 3 轮
python3 /home/gis/bench/p2p_lat2.py       # 小包延迟
python3 /home/gis/bench/soak_percard.py   # 25 s 双卡压测
sudo dmesg | grep -c Xid                  # 期望 0
```

期望：`26.3 GB/s` 双向（±0.1），Xid = 0。

---

## 7. 故障对照表

| 现象 | 原因 | 处理 |
|---|---|---|
| `nvidia-smi: No devices were found` | `CmpGen4=1` 或 capforce 参数错 | 去掉 `CmpGen4=1`；确认 `CmpGen3=1` + `RMPcieLinkSpeed=0xF` |
| `kbusCreateP2PMapping_GP100: P2P type 2 is not supported` | 只打了 0001+2 行派发，缺 BAR1 创建入口的 4 行 | 补 `0002` 的 `CreateP2PMappingForBar1P2P` / `Remove...` 两处 |
| `M3DIAG pte-gate: bar1Sup=0` | 0002 没打，或打在了别的文件上 | 确认改的是 `.build/.../src/nvidia/generated/g_kern_bus_nvoc.c` |
| 速度“27 GB/s”但逐位全错 | 走的是 static identity 编码（窗口越界） | 查 `METHOD3: dynamic BAR1 P2P map` 是否出现 |
| 速度 16.7 GB/s 且数据对 | 动态映射生效但只走了单向/未预热 | 用 `p2p_rep.py`（预热 + 双流）测；`p2p_gate.py` 的数字本身偏低是正常的 |
| 2 GiB 拷贝掉卡（`Xid 45/109`，`COMMAND=0000`） | 动态映射没生效 | 同上，通常是 0002 未生效；物理断电恢复 |
| `LnkSta` 只有 8GT/s | root/switch 端 `LnkCtl2` 仍是 3，或 E1 在 Gen2 下做过 | 两端 `LnkCtl2=4`，物理断电重来 |
| `PL_RATE=0x00240036` 而非 `0x00140036` | 在 Gen2 状态下做了 E1 | 确保先到 Gen3；清 PHY 后重试 |
| `build.sh` 把树重新解压了 | 动过 `build.sh`/`patches/`（stamp 变） | 重新打 5 个补丁再编 |
| `nvidia.ko` 变 0 字节 | 编译被打断（断电/超时） | 重跑 `build_noreload.sh`（ccache 命中，1 分钟） |
| 热重载后整机挂死 | `modprobe -r/insmod` 在受损状态 | 只能物理断电；以后一律用 `build_noreload.sh` |

---

## 8. 回退

```bash
# 只回退 P2P 到 mailbox（仍保留 Gen4）
sudo sed -i 's/;RMPcieP2PType=1//' /etc/modprobe.d/cmp-gen3.conf
sudo update-initramfs -u
sudo poweroff     # 物理断电 15s 后上电

# 彻底回退到“Gen4 可用、无动态 BAR1”的驱动与配置
# （rollback.sh 只存在本实验机上；其它机器请用备份的 nvidia.ko + 原 conf 手工还原）
sudo bash /home/gis/p2p/bak-gen4-good-20261126-1025/rollback.sh
sudo poweroff
```

---

## 9. 附：诊断脚本怎么用（怀疑没生效时）

```bash
# 最小跨卡拷贝（1/4/8 MiB，逐位比对，不会把机器写死）
python3 /home/gis/p2p/Gen4-DynBAR1/scripts/m3_small.py

# 想重新加诊断打印：改源码 → 重编
python3 /home/gis/p2p/Gen4-DynBAR1/scripts/m3diag.py     # 在 nv_gpu_ops.c 加 M3DIAG pte-gate / pte-dyn / caps
python3 /home/gis/p2p/Gen4-DynBAR1/scripts/m3diag2.py    # 在 kern_bus_gh100.c 加 M3DIAG2 bar1chk / capsgate
```

脚本里 `SUDO_PW`/`/tmp/.pw` 需要按你的机器改密码；`/tmp` 冷启会被清空，重启后重新放置。
