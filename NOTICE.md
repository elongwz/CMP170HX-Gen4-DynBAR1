# Third-party notices

本仓库只包含**补丁、配置、脚本与日志**，不包含 NVIDIA 版权源码本体。

## NVIDIA open-gpu-kernel-modules

- 上游：https://github.com/NVIDIA/open-gpu-kernel-modules
- 版本：`610.43.03`（本仓库补丁的目标）
- 许可：MIT
- 说明：`patches/0001..0005` 是以 diff 形式对上述源码的修改，diff 上下文与其余部分版权归 NVIDIA 及其贡献者所有。

## duanyll：动态 BAR1（METHOD3）

- 文章：https://duanyll.com/2026/7/13/4090-48G-P2P/
- 代码：`Duanyll/open-gpu-kernel-modules` 分支 `595.71.05-p2p-48g`
- 许可：上游为 MIT，该分支的补丁同样以 MIT 提供
- 说明：`patches/0001-method3-dynamic-bar1.patch` 是该分支相对官方 `595.71.05` 的完整 diff（3 文件 / 926 行），由本仓库验证可在 `610.43.03` 上直接应用（仅行偏移变化）。

## cmp-unlocker / oc-80g 驱动树

- 用于 80 GB 显存与链路上层门控的既有补丁体系（`build.sh` + 16 个基础补丁）
- 本仓库不重分发其内容；`patches/0002..0005` 只包含在其基础上新增的改动
- `docs/REPRODUCE.md` 假定你已经拥有可用的 cmp-unlocker v0.5 / `oc-80g-gen3` 树

## 其它

- `scripts/bringup-verify.sh`、`scripts/pcie_bandwidth_kit.py` 改编自社区 Gen4 bring-up 工具包（`cmp170hx-gen4-bringup-20261118`）。
- `scripts/` 下其余脚本与 `patches/0002`、`README.md`、`docs/*` 为本仓库原创，MIT。
