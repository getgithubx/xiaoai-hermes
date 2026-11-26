# 官方应答过滤

此可选模块让已验证的 LX04 保留唤醒、识别和 Hermes 显式 TTS，抑制小爱自动语音回答。它通过 Frida 在正在运行的 MicoLauncher 中附着；退出或断连后过滤可能失效，Mac 与 ADB 需要保持在线。它不阻止小米云执行设备控制等指令，也不替代云端语音识别。

## 已验证范围

- LX04、ARMv7，固件 `2.42.118` 与 `2.42.206`。
- 启动器必须位于 `/system/app/MicoLauncher/MicoLauncher.apk`。
- 两版实机 APK 完全相同，SHA-256 为 `803b6d89ef20df27f563e4cbec109ef7bec1d83c9bdc67990789fc72dd5be71a`。
- `verify_firmware()` 在附着前检查以上条件。应用更新到 `/data/app`、APK 字节改变或未知固件都会拒绝附着；不要仅为消除错误增加版本白名单。

过滤给识别/NLP 请求附加禁止 TTS 的上下文，并按请求 ID 处理防御性 PCM 与 URL 音频过滤；`SpeechSynthesizer.Synthesize` 显式播报继续执行。过滤脚本卸载后原方法恢复。

## 安装和前台验证

先按主 README 安装桥接及 Android 工具，填写自己的 `screen-config.json`。需要已授权的 root ADB；该模块不会开启 ADB 或解锁固件。

```sh
uv pip install --python .venv/bin/python frida==16.7.19
```

从 [Frida 16.7.19 官方发布](https://github.com/frida/frida/releases/tag/16.7.19) 下载 `frida-server-16.7.19-android-arm.xz`，解压后的文件放到 `tools/frida-server-16.7.19-android-arm`。其 SHA-256 必须为 `fc60c38a6acb42d9eb2d5c0474bf84fd671c14870b9cab43129b38c5e364bb13`；程序会验证，不能用 arm64 或其他版本替代。

```sh
./.venv/bin/python native_answer_guard.py
```

设备服务端只监听设备回环地址，Mac 经认证 ADB 转发本机端口 `27043`。成功后 `state/native-answer-status.json` 包含 `ready: true`、实际固件版本、APK 哈希和计数。状态仅表示过滤已附着，应继续实际提问并检查 Hermes 播报与屏显。

## macOS 登录后自动启动

前台验证后先按 Ctrl+C 停止前台过滤，再执行：

```sh
./.venv/bin/python tools/install_launchagent.py --native-answer
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/local.xiaoai-hermes-native-answer.plist"
```

安装器使用当前仓库路径生成独立服务，不覆盖已有 plist。已安装的服务更新代码后，执行：

```sh
launchctl kickstart -k "gui/$(id -u)/local.xiaoai-hermes-native-answer"
```

停止自动过滤：

```sh
launchctl bootout "gui/$(id -u)/local.xiaoai-hermes-native-answer"
```

日志为私有 `state/native-answer-guard.log`。设备或启动器重启后，服务会重新连接并再次核对固件与 APK；这不保证网络断连期间仍能保持过滤。

## 升级与验证边界

2026-11-27，新版实测原生 NLP 请求完成且 `nativeSpeakInstructions` 没有增加，生产桥接收到同一问题并让 Hermes 回答；另一个真实 Hermes 请求返回文字、生成显式 TTS 音频且显示在屏幕。

计数由正在运行的生产桥共享：测试原生请求时，Hermes 可能同时生成正常音频，不能把 `allowedPcmBytes` 非零当成过滤失败。要结合请求类型、原生 Speak 指令、桥接结果及实际听感判断。此次升级后的真实麦克风复测仍待用户确认。

如果 OTA 后 ADB 变为 `unauthorized`，先按 [固件研究](FIRMWARE.md) 区分系统槽内公钥丢失与网络断连；重启过滤服务不会修复固件授权。
