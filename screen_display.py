"""Send subtitles through authenticated ADB; display failures never block speech."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ADB = ROOT / 'tools/platform-tools/adb'
COMPONENT = 'local.hermes.screen/.DisplayService'
LOG = logging.getLogger('hermes.screen')


class ScreenDisplay:
    def __init__(self):
        config = json.loads((ROOT / 'screen-config.json').read_text())
        self.network = config['network']
        self.usb = config['usb_serial']
        self.pending = None
        self.changed = asyncio.Event()
        self.worker = None

    def start(self):
        self.worker = asyncio.create_task(self._run())

    async def close(self):
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass

    def publish(self, question: str, answer: str, status: str):
        # Bound Binder/argv size. Keep shell metacharacters out of remote commands.
        if len(answer) > 12000:
            answer = answer[:12000] + '\n（屏幕仅显示前12000字）'
        self.pending = {'question': question[:1000], 'answer': answer, 'status': status}
        self.changed.set()

    async def _adb(self, *args):
        proc = await asyncio.create_subprocess_exec(
            str(ADB), *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=8)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if proc.returncode is None:
                proc.kill()
            await proc.communicate()
            raise
        if proc.returncode:
            raise RuntimeError('ADB transport unavailable')
        return stdout.decode(errors='replace')

    async def send(self, data):
        devices = await self._adb('devices')
        ready = {line.split()[0] for line in devices.splitlines()[1:]
                 if len(line.split()) >= 2 and line.split()[1] == 'device'}
        if self.network in ready:
            serial = self.network
        elif self.usb in ready:
            serial = self.usb
        else:
            await self._adb('connect', self.network)
            serial = self.network
        payload = base64.b64encode(json.dumps(data, ensure_ascii=False).encode()).decode()
        result = await self._adb('-s', serial, 'shell', 'am', 'start-foreground-service',
                                 '-n', COMPONENT, '--es', 'payload', payload)
        if 'Error' in result or 'Exception' in result:
            raise RuntimeError('Display service rejected update')

    async def _run(self):
        while True:
            await self.changed.wait()
            self.changed.clear()
            data = self.pending
            try:
                await self.send(data)
            except Exception as exc:
                LOG.warning('屏显暂不可用（%s），稍后自动重连；语音继续。', type(exc).__name__)
                await asyncio.sleep(10)
                self.changed.set()
