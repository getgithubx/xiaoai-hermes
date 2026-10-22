#!/bin/zsh
set -e
cd -- "${0:A:h}"
exec ./.venv/bin/python - <<'PY'
import asyncio
from screen_display import ScreenDisplay
async def main():
    screen = ScreenDisplay()
    devices = await screen._adb('devices')
    ready = {line.split()[0] for line in devices.splitlines()[1:]
             if len(line.split()) >= 2 and line.split()[1] == 'device'}
    serial = screen.usb if screen.usb in ready else screen.network
    if serial not in ready:
        await screen._adb('connect', serial)
    print(await screen._adb('-s', serial, 'shell', 'am', 'start', '-n', 'local.hermes.screen/.MainActivity'))
asyncio.run(main())
PY
