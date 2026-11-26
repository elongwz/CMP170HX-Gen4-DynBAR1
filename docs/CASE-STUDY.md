# CASE STUDY：`bar1Sup=0` → 6 行派发 → CUDA init 失败 → 再 4 行

> 这是一次真实的排查记录，把“动态 BAR1 P2P 在 GA100 上不生效”从**现象**追到**根因**再追到**修复**的完整过程。
> 目标不是让你背结论，而是学会在同一类“闭源驱动的能力位 + generated 派发表”问题里怎么下手。
>
> 前置阅读：[README](../README.md) 第 1 章（问题拆解）、[RESULTS](RESULTS.md)（最终数据）。

---

## 0. 起点：一切看起来都对

此时的状态：

- 两张 170HX 已经是 81920 MiB 的 80 G 解锁卡；
- 链路已经是 Gen4 x16（`LnkSta=0x1104`）；
- 我们已经把 duanyll 的 METHOD3（逐分配动态 BAR1）打进了 `610.43.03`；
- conf 已经切到 BAR1 模式：`RMPcieP2PType=1`。

预期：P2P 应该快而且正确。实际拿到的：

```
4 MiB     17.88 GB/s   CORRUPT(1048576)      ← 全部元素错
64 MiB    23.83 GB/s   CORRUPT(16777216)
256 MiB   24.10 GB/s   CORRUPT(67108864)
1024 MiB  24.55 GB/s   CORRUPT(268435456)
1 GiB@70GiB 高地址    25.33 GB/s  CORRUPT(...)
RESULT: FAIL (5 bad transfers)
```

而且 **dmesg 里一条 `METHOD3` 都没有**，也没有任何报错、Xid 是 0。

> 这是最恶心的形态：**速度看起来更快了（比 mailbox 的 20.3 还高），数据全错，日志全静默。**

### 0.1 第一条误导：把“没有日志”当成“分支没进”

METHOD3 打上补丁后，动态映射成功时打印的是：

```c
NV_PRINTF(LEVEL_INFO, "METHOD3: dynamic BAR1 P2P map ...");
```

`LEVEL_INFO` 在默认的 `NVreg_RegistryDwords` 打印级别下**不会输出**。而我们自己加的失败路径是 `LEVEL_ERROR`，也没出现。

于是“没有 METHOD3 行”既不能证明进了、也不能证明没进 —— **静默不是证据**。第一条教训就来自这里：
**要判断分支走向，必须自己插 ERROR 级探针，不能依赖别人的日志。**

---

## 1. 插探针：三个 ERROR 打印

在 `src/nvidia/src/kernel/rmapi/nv_gpu_ops.c` 的 `nvGpuOpsGetExternalAllocPtesOrPhysAddrs()` 里，动态分支的门口本来长这样：

```c
if (isBar1P2PSupported &&
    !kbusIsStaticBar1Enabled(pAdjustedMemDesc->pGpu,
                             GPU_GET_KERNEL_BUS(pAdjustedMemDesc->pGpu))) {
    status = _nvGpuOpsDynBar1GetOrCreate(..., &dynBar1DmaBase);
    ...
    dynBar1Mapped = NV_TRUE;
}
```

插三个探针（`scripts/m3diag.py` 会自动做这件事，改前它会备份成 `*.preM3diag`）：

| 探针 | 位置 | 打印内容 |
|---|---|---|
| `M3DIAG pte-gate` | 上面那个 `if` 之前 | `mapGPU / peerGPU / peerSup / indirect / bar1Sup / staticPeer / staticMap / hMem / size` |
| `M3DIAG pte-dyn` | `dynBar1Mapped = NV_TRUE;` 之后 | 实际拿到的 `windowBase` |
| `M3DIAG caps` | `nvGpuOpsGetP2PCaps()` 里 `p2pLink = UVM_LINK_TYPE_PCIE_BAR1;` 之后 | `addr0/size0/addr1/size1` + static 状态 |

同时把 METHOD3 那条成功日志从 `LEVEL_INFO` 提成 `LEVEL_ERROR`（临时，方便观测）。

写法示例：

```c
NV_PRINTF(LEVEL_ERROR,
          "M3DIAG pte-gate: mapGPU%u peerGPU%u peerSup=%u indirect=%u bar1Sup=%u "
          "staticPeer=%u staticMap=%u hMem=0x%llx size=0x%llx\n",
          pMappingGpu->gpuId, pAdjustedMemDesc->pGpu->gpuId,
          isPeerSupported, isIndirectPeerSupported, isBar1P2PSupported,
          (NvU32)kbusIsStaticBar1Enabled(pAdjustedMemDesc->pGpu, ...),
          (NvU32)kbusIsStaticBar1Enabled(pMappingGpu, ...),
          (unsigned long long)hMemory, (unsigned long long)size);
```

### 1.1 编译与部署（别把机器搞死）

`build.sh` 末尾会自动 `modprobe -r nvidia && modprobe nvidia`。在 BAR1/GSP 状态受损时这会**把整机卡死**
（现象：TCP 22 端口还在、SSH 卡在 banner exchange，只能物理断电）。

所以用 shim 屏蔽掉：

```bash
mkdir -p /tmp/shim
printf '#!/bin/sh\nexit 0\n' > /tmp/shim/modprobe
printf '#!/bin/sh\nexit 0\n' > /tmp/shim/systemctl
chmod +x /tmp/shim/modprobe /tmp/shim/systemctl
printf '%s\n' "$SUDO_PW" > /tmp/.pw; chmod 600 /tmp/.pw
setsid nohup sudo -S env PATH=/tmp/shim:$PATH bash build.sh > /tmp/build.log 2>&1 < /tmp/.pw &
```

（仓库里的 `tools/build_noreload.sh` 就是这个。）

校验探针真的进了二进制：

```bash
strings /lib/modules/$(uname -r)/updates/cmpunlocker/nvidia.ko | grep -c M3DIAG   # 期望 3
```

然后**物理断电冷启**（热重启不清 PHY，且热重载必崩）。

---

## 2. 第一次定位：门根本没进 —— `bar1Sup=0`

冷启后跑 1/4/8 MiB 的最小跨卡拷贝（`scripts/m3_small.py`，别一上来就 2 GiB，见第 7 节），拿到：

```
NVRM: nvGpuOpsGetExternalAllocPtesOrPhysAddrs: M3DIAG pte-gate:
      mapGPU0 peerGPU1 peerSup=1 indirect=0 bar1Sup=0 staticPeer=0 staticMap=0
      hMem=0xcaf00197 size=0x200000
```

配套的断言（说明走了**旧的 static identity 编码**）：

```
NVRM: nvAssertFailedNoLog: Assertion failed: ((base & RM_PAGE_MASK) == 0) @ kern_bus.c:298
NVRM: nvAssertFailedNoLog: Assertion failed: remoteWMBoxLocalAddr != ~0ULL @ kern_bus_gm200.c:89
```

### 读法

动态分支的条件是 `isBar1P2PSupported && !kbusIsStaticBar1Enabled(...)`。
现在 `bar1Sup=0`，所以条件为假 → **METHOD3 的代码一行都没执行** → 驱动仍然用

```c
bar1BusAddr = gpumgrGetGpuPhysFbAddr(pAdjustedMemDesc->pGpu);   // FB 物理基址
peer_addr   = bar1BusAddr + offset;                             // 分配偏移
```

而 BAR1 只有 64 GiB、显存 80 GiB → 偏移超过 64 GiB 就编出**窗口外的总线地址**。
但我们的 4 MiB 拷贝也全错 —— 说明连“窗口内”的部分都不对：这个 `bar1BusAddr` 本身就不是有效的 P2P 窗口基址
（static BAR1 根本没启用，`staticPeer=0 staticMap=0` 也印证了这点）。

> **结论**：`isBar1P2PSupported` 是这一切的总闸。它为什么是 0？

---

## 3. 第二次定位：`bar1Supported` 是从哪儿来的

顺着调用链往上查（`grep -rn "bar1Supported" src/`）：

```c
// src/nvidia/src/kernel/rmapi/nv_gpu_ops.c:3148
p2pCaps->bar1Supported =
    !!REF_VAL(NV0000_CTRL_SYSTEM_GET_P2P_CAPS_PCI_BAR1_SUPPORTED, p2pCapsParams->p2pCaps);
```

这个 RM control 位是在 `client_resource.c` 里根据 **connectivity** 决定的：

```c
// src/nvidia/src/kernel/rmapi/client_resource.c:513 附近
if (connectivity == P2P_CONNECTIVITY_PCIE_BAR1)
    p2pCaps |= NV0000_CTRL_SYSTEM_GET_P2P_CAPS_PCI_BAR1_SUPPORTED;
else if (connectivity == P2P_CONNECTIVITY_PCIE_PROPRIETARY)
    p2pCaps |= NV0000_CTRL_SYSTEM_GET_P2P_CAPS_PCI_PROP_SUPPORTED;
```

而 `connectivity` 来自 `p2pGetCapsStatus(gpuMask, &wr, &rd, &atomic, &connectivity)`。

所以在 `kernel_bus_gh100.c` 的 `kbusIsPcieBar1P2PMappingSupported_GH100()` 里加第二个探针（`scripts/m3diag2.py`）：

```c
KernelBif *pKernelBif = GPU_GET_KERNEL_BIF(pGpu0);
NV_PRINTF(LEVEL_ERROR,
          "M3DIAG2 bar1chk: gpu%u<->gpu%u pcieP2PType=%u byDefault=%u "
          "peerMask(0->1)=0x%x peerMask(1->0)=0x%x static0=%u static1=%u\n",
          ...);
```

再在 `nv_gpu_ops.c` 的 `if (p2pCaps.bar1Supported)` 之前加：

```c
NV_PRINTF(LEVEL_ERROR, "M3DIAG2 capsgate: gpu%u<->gpu%u access=%u nvlink=%u bar1=%u atomic=%u\n", ...);
```

### 关键观察：`bar1chk` **一行都没有**

冷启后日志：

```
M3DIAG2 capsgate: gpu1280<->gpu2048 access=1 nvlink=0 bar1=0 atomic=0
M3DIAG  pte-gate: ... bar1Sup=0 ...
（没有任何 M3DIAG2 bar1chk）
```

`bar1chk` 打在 `kbusIsPcieBar1P2PMappingSupported_GH100()` 的**函数体开头**。它不出现只有一个解释：
**这个函数从来没被调用过。**

```bash
grep -rn "IsPcieBar1P2PMappingSupported" src/ | grep -v generated
# 只有 kern_bus_gh100.c:1449（定义）和 :1783（自己内部调用）
```

也就是说：GA100 走的不是 GH100 的 HAL。

---

## 4. 第三次定位：generated 派发表里的 `// default` 分支

NVIDIA 的 `kbus` 是一套 **HAL 派发**体系，派发关系写在一个自动生成的文件里：

```
src/nvidia/generated/g_kern_bus_nvoc.c
```

里面每个能力都是这种形状：

```c
// kbusCreateP2PMapping -- halified (2 hals) body
if (/* 某个架构条件 */) {
    pThis->__kbusCreateP2PMapping__ = &kbusCreateP2PMapping_GH100;
}
else
{
    pThis->__kbusCreateP2PMapping__ = &kbusCreateP2PMapping_GP100;   // ← GA100 落到这里
}
```

**GA100 落到 `// default` 的 else 分支**，而那个分支把所有 BAR1 P2P 能力指给了 Pascal 时代的实现或“不支持”桩：

```bash
grep -n "__kbus.*P2PMapping" src/nvidia/generated/g_kern_bus_nvoc.c
```

```
789:  pThis->__kbusCreateP2PMapping__            = &kbusCreateP2PMapping_GP100;
800:  pThis->__kbusRemoveP2PMapping__            = &kbusRemoveP2PMapping_GP100;
1117: pThis->__kbusCreateP2PMappingForBar1P2P__  = &kbusCreateP2PMappingForBar1P2P_395e98;
1129: pThis->__kbusRemoveP2PMappingForBar1P2P__  = &kbusRemoveP2PMappingForBar1P2P_395e98;
1141: pThis->__kbusHasPcieBar1P2PMapping__       = &kbusHasPcieBar1P2PMapping_d69453;
1153: pThis->__kbusIsPcieBar1P2PMappingSupported__ = &kbusIsPcieBar1P2PMappingSupported_d69453;
```

`_395e98` / `_d69453` 这种十六进制后缀是生成器给“**stub**”起的名字：去 `src/nvidia/src/kernel/gpu/bus/` 里搜，
会看到它们直接 `return NV_ERR_NOT_SUPPORTED;` 或 `return NV_FALSE;`。

> 所以 GA100 既没有 BAR1 P2P 的能力查询，也没有它的创建路径 —— 尽管同代的 GH100 实现完全通用
> （`kbusCreateP2PMappingForBar1P2P_GH100()` 内部对“static BAR1 未启用”早有守卫，见第 6 节）。

### 修复 #1（2 行）：先把“能力查询”打开

```diff
-        pThis->__kbusHasPcieBar1P2PMapping__ = &kbusHasPcieBar1P2PMapping_d69453;
+        pThis->__kbusHasPcieBar1P2PMapping__ = &kbusHasPcieBar1P2PMapping_GH100;

-        pThis->__kbusIsPcieBar1P2PMappingSupported__ = &kbusIsPcieBar1P2PMappingSupported_d69453;
+        pThis->__kbusIsPcieBar1P2PMappingSupported__ = &kbusIsPcieBar1P2PMappingSupported_GH100;
```

重编 → 冷启 → 日志变了：

```
M3DIAG2 bar1chk: gpu0<->gpu1 pcieP2PType=1 byDefault=0 peerMask(0->1)=0x0 peerMask(1->0)=0x0 static0=0 static1=0
M3DIAG2 capsgate: ... bar1=1
```

`bar1chk` 出现了、`bar1=1` —— 门开了。**但 CUDA 已经起不来了。**

---

## 5. 第四次定位：能力开了，创建路径没开

现象：

```python
RuntimeError: CUDA driver initialization failed, you might not have a CUDA gpu.
```

dmesg：

```
NVRM: kbusCreateP2PMapping_GP100: P2P type 2 is not supported
NVRM: nvCheckOkFailedNoLog: Check failed: Call not supported [NV_ERR_NOT_SUPPORTED]
      (0x00000056) returned from kbusCreateP2PMapping_HAL(pLocalGpu, pLocalKernelBus,
      pRemoteGpu, pRemoteKernelBus, &peer1, &peer2, pP2PApi->attributes) @ p2p_api.c:599
```

### 读法

1. `P2P type 2` = BAR1（0=default、1=NVLINK、2=BAR1、3=mailbox，具体枚举见 `ctrl/ctrl2080/...` 里的 `_P2P_TYPE`）。
2. 一旦 `bar1Supported=1`，UVM/CUDA 初始化阶段就会**主动去建 BAR1 映射**——这一步以前从未发生，所以现在才炸。
3. 建映射走的是 `__kbusCreateP2PMapping__`，而它（第 4 节）还指着 `_GP100`。
   `kern_bus_gp100.c` 的 `kbusCreateP2PMapping_GP100()` 只处理 NVLINK / NVLINK_INDIRECT / PCIE(mailbox)，
   没有 BAR1 分支 → `NOT_SUPPORTED`。

这就是 duanyll 博客里“坑 #1”的同构版本：**能力位与创建路径必须一起打开，否则要么静默走错路（前一阶段），要么直接起不来（这一阶段）。**

### 修复 #2（4 行）：把创建/拆除入口也指向 GH100

```diff
-    pThis->__kbusCreateP2PMapping__ = &kbusCreateP2PMapping_GP100;
+    pThis->__kbusCreateP2PMapping__ = &kbusCreateP2PMapping_GH100;

-    pThis->__kbusRemoveP2PMapping__ = &kbusRemoveP2PMapping_GP100;
+    pThis->__kbusRemoveP2PMapping__ = &kbusRemoveP2PMapping_GH100;

-    pThis->__kbusCreateP2PMappingForBar1P2P__ = &kbusCreateP2PMappingForBar1P2P_395e98;
+    pThis->__kbusCreateP2PMappingForBar1P2P__ = &kbusCreateP2PMappingForBar1P2P_GH100;

-    pThis->__kbusRemoveP2PMappingForBar1P2P__ = &kbusRemoveP2PMappingForBar1P2P_395e98;
+    pThis->__kbusRemoveP2PMappingForBar1P2P__ = &kbusRemoveP2PMappingForBar1P2P_GH100;
```

**为什么这样是安全的**（不是“随便换个更高级的实现”）：

`kbusCreateP2PMappingForBar1P2P_GH100()`（`kern_bus_gh100.c:1765` 起）内部第一件事就是判断 static BAR1：

```c
if (kbusIsStaticBar1Enabled(pLocalGpu, pLocalKernelBus) &&
    kbusIsStaticBar1Enabled(pRemoteGpu, pRemoteKernelBus)) {
    status = _kbusCreateStaticBar1IOMMUMappingForGpuPair(...);
}
/* static BAR1 未启用时：跳过整块 FB 的 identity 映射，只做引用计数 */
```

我们这台机器 `static0=0 static1=0`，所以它**根本不会去建那块 64 GiB/80 GiB 的静态映射**，只是把 peer 通道的引用计数加一，
真正的按分配映射由 METHOD3 在 `nv_gpu_ops.c` 里逐块做（`_nvGpuOpsDynBar1Create` → `kbusMapFbAperture_HAL`）。

重编 → 冷启 → 成功：

```
NVRM: _nvGpuOpsDynBar1Create: METHOD3: dynamic BAR1 P2P map GPU0->GPU1 hMem 0xcaf001a1
      size 0x200000 bar1Off 0x400000 dmaBase 0x12000400000 (total 0x200000)
NVRM: nvGpuOpsGetExternalAllocPtesOrPhysAddrs: M3DIAG pte-gate: ... bar1Sup=1 staticPeer=0 staticMap=0
```

`dmaBase = 0x12000400000` 就是 GPU1 的 BAR1 窗口基址 + 槽位偏移（`0x400000`）——**地址第一次落在窗口内**。

---

## 6. 收尾验证（这一步决定“真快”还是“假快”）

```bash
python3 /home/gis/bench/p2p_gate.py     # 逐档：填值 → 拷贝 → 逐元素比对
```

```
4 MiB    14.02 / 13.56 GB/s   mismatched=0
64 MiB   16.31 / 16.10        mismatched=0
256 MiB  16.44 / 16.39        mismatched=0
1 GiB    16.79 / 16.86        mismatched=0
2 GiB    16.75                mismatched=0     ← 改造前这一档会掉卡
RESULT: PASS
```

再测纯吞吐（`p2p_rep.py`，1 GiB，预热 1 轮后取 3 轮最好值）：

```
0→1  26.32 GB/s      1→0  26.31 GB/s      Xid: 0
```

---

## 7. 复盘：五条可复用的套路

### ① 能力位链要“从使用点往上追”，不要从 API 往下猜

`isBar1P2PSupported`（使用点）→ `bar1Supported`（RM control 位）→ `connectivity`（`p2pGetCapsStatus`）→
`kbusIsPcieBar1P2PMappingSupported_HAL`（HAL）→ `g_kern_bus_nvoc.c`（派发表）。
每一层都用一个 ERROR 探针确认“这层有没有被执行”，比读代码猜快 10 倍。

### ② `generated/*_nvoc.c` 是 HAL 的真相

NVIDIA 的 `swig` 生成文件里，`// default` 的 else 分支就是“这个架构没有专门实现”的兜底。
**换架构移植时第一个该看的文件**。命名规律：`_<hex>` 后缀 = stub（`NV_ERR_NOT_SUPPORTED`）。

### ③ `LEVEL_INFO` 日志是个陷阱

“没有日志”既可能是没执行，也可能是没打印。判断分支必须自己插 `LEVEL_ERROR`，
并且**在函数入口**插（HAL 函数入口没打印 = 函数没被调用，这是强证据）。

### ④ 能力位与创建路径必须成对打开

- 只开能力位：静默走错编码路径（假速度、静默损坏）；
- 只开创建路径：能力位为 0，代码根本不会被调用（白改）；
- 两个都开：才真正生效。

这也是为什么本仓库的 `patches/0002` 是 **6 行**而不是 2 行或 4 行。

### ⑤ 热重载＝炸机，冷启＝唯一真理

- 改完驱动**物理断电 15 s**，`reboot` 不清 PHY；
- 编译用 shim（`tools/build_noreload.sh`），否则 `build.sh` 末尾的 `modprobe` 会把整机卡死；
- 诊断期**只跑 ≤8 MiB 拷贝**：错误的 peer 基址 + 大拷贝 = 把机器写死（我们为此断电三次）。

---

## 8. 附录

### 8.1 一键重现诊断（只读，不会改源码）

```bash
python3 scripts/m3diag.py     # 给 nv_gpu_ops.c 加 3 个 M3DIAG 探针（自动备份 .preM3diag）
python3 scripts/m3diag2.py    # 给 kern_bus_gh100.c / nv_gpu_ops.c 加 2 个 M3DIAG2 探针
bash tools/build_noreload.sh /home/gis/p2p/cmpunlocker-v0.5-oc-80g-gen3
# 物理断电冷启 → python3 scripts/m3_small.py → sudo dmesg | grep M3DIAG
```

### 8.2 关键日志片段存档

| 阶段 | 特征行 | 位置 |
|---|---|---|
| 门没进 | `M3DIAG pte-gate: ... bar1Sup=0` | `logs/dmesg-full.log` |
| HAL 没路由 | 无 `M3DIAG2 bar1chk` | 同上 |
| 能力开了 | `M3DIAG2 capsgate: ... bar1=1` | 同上 |
| 创建路径错 | `kbusCreateP2PMapping_GP100: P2P type 2 is not supported` | 同上 |
| 成功 | `METHOD3: dynamic BAR1 P2P map ... dmaBase 0x...` | 同上 / `logs/boot-evidence.log` |

### 8.3 相关文件

- 补丁：`patches/0001-method3-dynamic-bar1.patch`、`patches/0002-ga100-hal-dispatch.patch`
- 诊断脚本：`scripts/m3diag.py`、`scripts/m3diag2.py`、`scripts/m3_small.py`
- 原始 diff 对照：`pristine/g_kern_bus_nvoc.c.orig`（本地，不入库；可用 `git apply -R patches/0002` 反推）
