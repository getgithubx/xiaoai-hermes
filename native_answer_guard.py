"""Reattach the reversible LX04 native-answer filter after device/app restarts."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

import frida

ROOT = Path(__file__).resolve().parent
ADB = ROOT / 'tools/platform-tools/adb'
STATE = ROOT / 'state'
SERVER = ROOT / 'tools/frida-server-16.7.19-android-arm'
SERVER_HASH = 'fc60c38a6acb42d9eb2d5c0474bf84fd671c14870b9cab43129b38c5e364bb13'
REMOTE = '/data/local/tmp/hermes-frida-server'
PORT = 27043
STOP = threading.Event()
# 两版现场提取的整个 APK 字节相同，过滤方法与 TTS 入口都未改变。
# 同时锁定版本和 APK，防止同版本应用更新后继续注入不兼容的方法。
LAUNCHER_HASH = '803b6d89ef20df27f563e4cbec109ef7bec1d83c9bdc67990789fc72dd5be71a'
VERIFIED_FIRMWARES = {'2.42.118', '2.42.206'}
LAUNCHER_PATH = '/system/app/MicoLauncher/MicoLauncher.apk'


def adb(*args, timeout=15):
    return subprocess.run([str(ADB), *args], check=True, capture_output=True,
                          text=True, timeout=timeout).stdout.strip()


def transport():
    config = json.loads((ROOT / 'screen-config.json').read_text())
    ready = {row.split()[0] for row in adb('devices').splitlines()[1:]
             if len(row.split()) == 2 and row.split()[1] == 'device'}
    for serial in (config['network'], config['usb_serial']):
        if serial in ready:
            return serial
    adb('connect', config['network'])
    adb('-s', config['network'], 'get-state')
    return config['network']


def status(ready, **extra):
    data = {'ready': ready, 'updated_at': time.time(), **extra}
    temporary = STATE / 'native-answer-status.tmp'
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(STATE / 'native-answer-status.json')


def bounded(call, seconds=20):
    cancel = frida.Cancellable()
    timer = threading.Timer(seconds, cancel.cancel)
    timer.start()
    try:
        with cancel:
            return call()
    finally:
        timer.cancel()


def verify_firmware(shell):
    """写入运行时钩子前核对实机版本和应用；未知组合直接停止本次附着。"""
    firmware = shell('getprop ro.mi.sw_ver')  # 系统版本变化必须经过独立验证。
    if firmware not in VERIFIED_FIRMWARES:
        raise RuntimeError('Firmware version differs from verified builds')
    if shell('pm path com.xiaomi.micolauncher') != 'package:' + LAUNCHER_PATH:
        raise RuntimeError('Launcher package path changed')  # 拒绝未经检查的 data APK 更新。
    digest = shell('sha256sum ' + LAUNCHER_PATH).split()  # 路径是固定常量，无用户输入。
    if not digest or digest[0] != LAUNCHER_HASH:
        raise RuntimeError('Launcher APK differs from verified code')
    return firmware  # 将实际验证的版本记录到状态文件，便于后续诊断。


def run():
    os.umask(0o077)
    STATE.mkdir(exist_ok=True, mode=0o700)
    if hashlib.sha256(SERVER.read_bytes()).hexdigest() != SERVER_HASH:
        raise RuntimeError('Unexpected Frida server checksum')
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    manager = frida.get_device_manager()
    while not STOP.is_set():
        session = None
        device = None
        try:
            serial = transport()
            shell = lambda command: adb('-s', serial, 'shell', command)
            if shell('getprop ro.product.model') != 'LX04':
                raise RuntimeError('This guard is restricted to LX04')
            firmware = verify_firmware(shell)
            if shell('getprop ro.product.cpu.abi') != 'armeabi-v7a':
                raise RuntimeError('Unexpected CPU ABI')
            digest = shell(f'sha256sum {REMOTE} 2>/dev/null || true').split()
            if not digest or digest[0] != SERVER_HASH:
                adb('-s', serial, 'push', str(SERVER), REMOTE, timeout=90)
                shell(f'chmod 700 {REMOTE}')
            # Explicitly redirect all descriptors: daemonize alone leaves the ADB
            # shell channel open on this Android build. Bind device loopback only.
            shell(f'pidof hermes-frida-server >/dev/null || '
                  f'{REMOTE} --listen=127.0.0.1:27042 --daemonize </dev/null >/dev/null 2>&1')
            adb('-s', serial, 'forward', f'tcp:{PORT}', 'tcp:27042')
            pid = int(shell('pidof com.xiaomi.micolauncher'))
            device = manager.add_remote_device(f'127.0.0.1:{PORT}')
            session = bounded(lambda: device.attach(pid))
            detached = threading.Event()
            ready = threading.Event()
            failed = threading.Event()
            session.on('detached', lambda *args: detached.set())
            script = session.create_script((ROOT / 'tools/native-answer-guard.js').read_text())

            def message(message, _data):
                if message['type'] == 'error':
                    failed.set()
                    print('Native answer hook error; reconnecting.', flush=True)
                elif message.get('payload', {}).get('event') == 'ready':
                    ready.set()

            script.on('message', message)
            bounded(script.load)
            if not ready.wait(10) or failed.is_set():
                raise RuntimeError('Native answer hook did not initialize')
            print('Native answers disabled; explicit Hermes TTS remains enabled.', flush=True)
            while not STOP.is_set() and not detached.is_set() and not failed.is_set():
                counters = bounded(script.exports_sync.stats, 10)
                status(True, pid=pid, firmware=firmware, launcher_sha256=LAUNCHER_HASH, counters=counters)
                STOP.wait(3)
        except Exception as exc:
            # Do not log device identities, dialogue contents, or SDK dumps.
            status(False, error=type(exc).__name__)
            print('Native answer guard reconnecting:', type(exc).__name__, flush=True)
        finally:
            if session is not None:
                try:
                    bounded(session.detach, 5)
                except Exception:
                    pass
            if device is not None:
                manager.remove_remote_device(f'127.0.0.1:{PORT}')
        if not STOP.is_set():
            STOP.wait(5)
    status(False, stopped=True)


if __name__ == '__main__':
    run()
