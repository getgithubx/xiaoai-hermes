# LX04 原版 2.42.118：ADB 开启研究

这是一次单机实测的研究记录及源码，**不是可直接跨设备刷入的通用 ROM**。所有输入必须来自使用者自己的完整备份，原始镜像、身份分区、公私钥和修改后的固件均不分发。

## 实机结果

2026-10-23，一台 MT8167 / LX04 保留原版 2.42.118、Android 8.1、原小爱启动器与用户数据，在普通断电启动及随后普通 ADB 重启后正常工作。

- 正常系统 `adb_enabled=1`，`adbd=running`，USB 与局域网 ADB 均经本机公钥认证。
- `ro.secure=1`、`ro.adb.secure=1` 保持原值；原厂 adbd 本身提供 uid=0 shell。
- 实际启动槽为 `_a`，启动验证状态为 `orange`。
- 原 vendor 已有 `service.adb.tcp.port=5555`；启动 adbd 后该配置生效，并非仅开启 USB。没有修改 vendor。

实际仅修改 seccfg 的首个 512 字节扇区，以及 system_a 的 9 个 4096 字节块（36 KiB）。没有写 B 槽、GPT、preloader、LK、boot、vendor、userdata，没有执行擦除或格式化。B 槽候选只在本机离线生成。

## 原厂 Recovery 会默认清数据

通过 REA 6.7.0 + Ghidra 12.1.4 的实际反编译，并用 Capstone 校对原始指令：

- Recovery `main=0xc140`；无 `update_package` 时，`0xd148 cmp sb,0`、`0xd14e beq 0xd278`，在 `0xd27a` 调用 `wipe_data@0xf1a4`。
- `wipe_data` 调用 `erase_volume("/data")`，按存在条件处理 `/cache`、`/vendor/nvdata`。
- 实际 `PreWipeData` 虚函数回调 `0xbb78` 返回 1，允许擦除。
- LK 入口与 A/B 共四份 DTB 一致：同时按音量加、音量减进入 Recovery。**不要将双键进入 Recovery 当作无损 ADB 开启步骤。**

Recovery 内虽然含 adbd，但它使用工厂公钥认证。修改恢复环境属性不等于开启正常系统 ADB。

## 启动槽、安全配置与 dm-verity

LK 有 512 字节 MTK 头，payload 基址 `0x56000000`。`0x56029b88` 读取 `para+0x800` 的 8 字节控制结构，magic 为 `0x19191100`，比较 byte5/6 低 3 位优先级；本次备份首选 A，后来由在线 `_a` 独立确认。

`FUN_56007980` 对锁状态 3 走解锁策略；表 `0x56040560` 步长 28，boot 条目 index4 的解锁验证策略为 0。`0x56007ab4` 取 bit1，控制共享加载路径是否调用验签。**不能裸改 seccfg 的 1→3**：纯软件 AES 校验与本机原配置不匹配，实际须使用硬件 SEJ V3 验证原配置并生成、再次验证新配置。

原 kernel 4.4.95 解压后通过 vmlinux-to-elf 恢复 42825 个符号。实际反编译 `android_verity_ctr@0xc06e88b4`，确认无效 verity metadata 且 `verifiedbootstate=orange` 时调用 `create_linear_device`。当前 user 构建利用这个分支；没有修改 kernel。

原 system_a/b、vendor_a/b 的 verity RSA 签名、全数据根哈希、完整哈希树、FEC 头部及区域摘要均验证通过；未重新计算 FEC Reed–Solomon 纠错码。原 verity 公钥与 AOSP 8.1.0_r1 公开测试证书不匹配，不能用 test-keys 指纹推定可重签。

## 最小系统改动

`tools/build_adb_firmware.py` 在备份副本中完成：

1. `/init.rc` 两处 `load_persist_props` 后、三处 `class_start main` 前插入 `setprop persist.sys.usb.config mtp,adb`。
2. `/adb_keys` 换为当前使用者的 `~/.android/adbkey.pub`，不复制私钥。
3. verity metadata magic 改为 `0x46464f56`。

按原数据块映射直接修改，文件长度、inode、权限、owner、SELinux 属性不变。原版与修改版 e2fsck 都存在同一个预先已有的 inode bitmap padding 警告，没有新增错误；不能将结果称为“完全 clean”。

## 源码输入及执行边界

| 工具 | 必要输入 / 行为 |
|---|---|
| `inspect_lx04_keys.py` | `backups/original/user.bin` 中的设备树，只读解析 |
| `inspect_lk_bootctrl.py` | `state/recovery-analysis/lk_a.bin` 与 `state/firmware-analysis/para.img`，只读 |
| `verify_offline_verity.py` | `state/firmware-analysis/` 中四个分区镜像、对应 `.verity-table` / `.verity-signature`、`original-verity-key`、`aosp-verity.x509.pem` |
| `build_adb_firmware.py` | 上述验证 JSON、system_a/b.img、本人的 ADB 公钥与 Homebrew e2fsprogs；只生成离线候选 |
| `apply_adb_firmware.py` | 自己的 `backups/original/`、原 boot/LK、候选镜像、manifest、经过独立审核的 offline-validation.json、同版 mtkclient 及硬件 SEJ |

发布版将本机特定备份目录泛化为 `backups/original/`。镜像提取、备份校验和离线验收证据需独立生成，未包装成无人值守刷机流程。**不要为了绕过门槛手写 `passed=true`；缺少可信验证输入时不应运行写入器。**

本次 mtkclient 基线为 `7cb40e727727dfe3faa065eefb29b16ed29bbdf1`。CLI 依赖记录在 `tools/mtk-requirements.lock`，分析还使用 cryptography、capstone；下载的 mtkclient、REA、Ghidra、内核和固件不随仓库分发。Python 至少 3.11，不以 `-O` 禁用脚本的检查断言。

写入器核对现场 GPT、设备身份分区、原 boot/LK、空 BCB、所有预映像；硬件验证原 seccfg 和生成配置后，先解锁、再修改系统块，每块及全体写后读回。事务失败反向回退，先恢复系统再恢复锁。`--restore` 检查两个槽，避免槽位回退后锁住仍被修改的旧槽。

原机 user 区备份 0xe2000000 字节，boot1/boot2 各 2 MiB；关键 8 分区独立复读相同，主备 GPT 校验及边界检查共 86 项通过。RPMB 未备份，也未做完整恢复演练，因此不承诺任何设备都能恢复。

## 已排除的路线

DA `shutdown(bootmode=2)` 曾返回成功，但实际没有进入 Fastboot，只出现黑屏，普通重新供电后正常启动。不要继续重复此路线，也不要把 DA 确认等同于实际 Fastboot 成功。

未使用第三方 SP5.5 固件，也未转成国际版 X04G；这些固件不在本项目的验证范围内。

## 研究来源

- [mtkclient](https://github.com/bkerler/mtkclient)
- [REA](https://github.com/morluto/rea)
- [Ghidra](https://github.com/NationalSecurityAgency/ghidra)
- [xiaomi-x04g-research](https://github.com/Informatic/xiaomi-x04g-research)
- [xiaomi-x04g-lx04](https://github.com/AyraHikari/xiaomi-x04g-lx04)

上述项目提供工具或背景资料；本记录中的地址和行为取自被测 LX04 自己的备份，不将 X04G 教程直接套用为 LX04 的实测结果。

## 2.42.206 升级后的授权恢复（2026-11-27）

本次设备升级后，完整备份确认 A 槽仍是修改过的 2.42.118，而 B 槽为原厂 2.42.206；MTK 槽优先级由 B 胜出。B 槽 `/adb_keys` 没有本机公钥，所以 USB 和网络 ADB 都变成 `unauthorized`。这次授权丢失源于系统切槽，不能归因于桥接崩溃。

恢复前重新读取并核对两槽的 system、vendor、boot、LK，以及 para、seccfg。另补读完整主备 GPT：当时的 mtkclient `gpt` 导出主表少一个扇区，所谓备份文件只是主表切片。必须按 GPT 声明的实际边界读取两份表，并重新校验头 CRC、完整条目数组 CRC 和主备一致性；不能补零冒充完整备份。所用 CLI 的 `rs` 不接受文件名，最终用经过实际参数解析验证的 `ro` 完成读取。不同工具版本应先核对其真实参数。

新 B system/vendor 的 verity 签名、数据根哈希、完整哈希树和 FEC 区域摘要通过；未重新计算 FEC 纠错码。新版内核整体有变化，但相关 verity 与线性挂载函数在相同偏移的字节保持一致。此离线证据并不能单独证明能够启动。

单机修复仅修改 B 槽的 `/adb_keys`、`/init.rc` 和 verity 标记，仍为 9 个 4096 字节块（36 KiB）。文件大小、inode、权限和 SELinux 标签不变，整镜像其余字节完全一致。原版及候选 `e2fsck -fn` 输出一致，均有原厂 inode bitmap padding 告警且退出码为 4，未擅自修复。

写入前实际核对了主备 GPT、原机身份、空恢复命令、B 槽优先级、完整 B 槽 LK/boot/vendor/system 哈希，以及硬件 SEJ V3 认证的已有解锁状态 3；没有重新写 seccfg。9 块即时及最终读回全部通过。普通断电启动后确认固件 2.42.206、实际槽 `_b`、`sys.boot_completed=1`、启动状态 `orange`，USB/网络 root ADB 恢复，屏显 App 保留并自启，真实 Hermes 回复可播报和显示。

本仓库中的旧 `build_adb_firmware.py` / `apply_adb_firmware.py` 仍仅用于 2.42.118 研究输入。**不能直接用于新 B 槽，也不能仅修改版本字符串、硬编码地址或验收标志后运行。** 新版单机写入器绑定了该设备备份和特定布局，未作为通用刷机工具发布。恢复研究应保留原块与逐块回退；本次 B 槽回退只恢复 B 原块，不能重新锁住仍被修改的 A 槽。

正常启动时不要按音量键；只读/写入工具需要 PreLoader 时才使用音量＋，不要同时按音量减。升级后的完整恢复演练未执行，后续 OTA 仍可能替换系统内公钥，需要重新检查实际固件和授权状态。
