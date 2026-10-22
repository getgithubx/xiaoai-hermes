# 来源与发布范围

- 语音桥接基于 [yihong0618/xiaogpt](https://github.com/yihong0618/xiaogpt)，固定提交 `4258b0b53cf6af062f1a01af993d47a161382a0f`。全量问题转发补丁见 `all-queries.patch`。原 MIT 许可证保存为 [docs/XIAOGPT-LICENSE.txt](docs/XIAOGPT-LICENSE.txt)。上游代码由安装器从原仓库获取。
- 固件读取与受限写入调用 [bkerler/mtkclient](https://github.com/bkerler/mtkclient)；本仓库不重新分发该工具或其 loader。其许可由原项目提供。
- 逆向分析使用 REA、Ghidra、Capstone、vmlinux-to-elf，构建使用 Google Android SDK 与 OpenJDK。工具需分别从原始来源获取并遵守各自许可。
- 新的屏显应用、桥接扩展与辅助脚本作为项目成果公开；当前未为整个仓库另行指定统一开源许可证，第三方已有许可不受影响。
- 不包含小米原厂镜像、原机身份分区、授权公私钥、账户令牌、个人对话、第三方反编译完整源文件，或 SDK/JDK/逆向工具的二进制副本。

这是社区个人项目，不代表小米、Nous Research 或相关工具作者的官方支持。
