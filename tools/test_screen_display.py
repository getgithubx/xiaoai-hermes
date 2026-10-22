import asyncio
import base64
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge import ScreenMiGPT
from screen_display import ScreenDisplay


class ScreenTests(unittest.IsolatedAsyncioTestCase):
    async def test_display_preserves_raw_text_and_hides_system_prompt(self):
        display = ScreenDisplay()
        bridge = ScreenMiGPT.__new__(ScreenMiGPT)
        bridge.config = SimpleNamespace(prompt='system hint', stream=False, bot='chatgptapi', gpt_options={})
        bridge.screen = display
        bridge.hermes_session = SimpleNamespace(headers=lambda: {'X-Hermes-Session-Id': 'test'})
        bridge.chatbot = SimpleNamespace(history=[], ask=AsyncMock(return_value='Hello 中文\n第二行 "引号"'))
        voice = [x async for x in bridge.ask_gpt('问题,system hint')]
        self.assertEqual(display.pending['question'], '问题')
        self.assertEqual(display.pending['answer'], 'Hello 中文\n第二行 "引号"')
        self.assertEqual(voice, ['Hello--中文，第二行--，引号，'])

    async def test_adb_falls_back_to_usb_and_encodes_literal_text(self):
        display = ScreenDisplay()
        display._adb = AsyncMock(side_effect=[f'List of devices attached\n{display.usb}\tdevice\n', 'Starting service'])
        data = {'question': '你好', 'answer': '$(literal) `text`\n"&<>', 'status':'回答'}
        await display.send(data)
        args = display._adb.call_args.args
        self.assertEqual(args[:2], ('-s',display.usb))
        self.assertEqual(json.loads(base64.b64decode(args[-1])), data)

    async def test_display_failure_does_not_block_producer(self):
        display = ScreenDisplay()
        display.send = AsyncMock(side_effect=RuntimeError('offline'))
        display.start()
        display.publish('问题', '回答', '完成')
        await asyncio.sleep(.02)
        self.assertEqual(display.pending['answer'], '回答')
        self.assertFalse(display.worker.done())
        await display.close()


if __name__ == '__main__': unittest.main()
