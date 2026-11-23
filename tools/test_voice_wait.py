"""验证长任务不会自动重发、静默失败或遗留等待提示；不连接真实音箱。"""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge import Config, ScreenMiGPT, VoiceChatGPTBot


class VoiceWaitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.screen = Mock()  # 不调用 ADB，只检查应该发给屏幕的内容。
        self.bridge = ScreenMiGPT(Config(openai_key='test', stream=False), self.screen)
        self.bridge.hermes_session = SimpleNamespace(headers=lambda: {'X-Hermes-Session-Id': 'test'})  # 不触碰真实七天会话。

    async def asyncTearDown(self):
        await self.bridge.close()  # 每次测试释放上游创建的 HTTP 会话。

    async def test_transient_error_is_not_retried(self):
        requests = []  # 直接统计 HTTP 次数，防止配置正确但实际仍重发。
        def handler(request):
            requests.append(request)
            return httpx.Response(503, json={'error': {'message': 'busy'}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as session:
            bot = VoiceChatGPTBot('test', api_base='http://test/v1')
            client = bot._make_openai_client(session)
            with self.assertRaises(Exception):
                await client.chat.completions.create(model='test', messages=[])
        self.assertEqual(len(requests), 1)  # 503 通常触发 SDK 重试，这里必须只发送一次。

    async def test_empty_reply_has_spoken_feedback(self):
        self.bridge.chatbot.ask = AsyncMock(return_value='')  # 上游超时或错误时返回空串。
        chunks = [chunk async for chunk in self.bridge.ask_gpt('测试')]
        self.assertIn('暂未收到', chunks[0])  # 给 TTS 有意义的文字，而非空白音频。
        self.assertEqual(self.screen.publish.call_args.args[2], '未收到回答')

    async def test_wait_notice_is_spoken_once(self):
        self.bridge.do_tts = AsyncMock()  # 不播放真实语音。
        with patch('bridge.asyncio.sleep', AsyncMock(side_effect=[None, None, asyncio.CancelledError()])):
            with self.assertRaises(asyncio.CancelledError):
                await self.bridge._report_waiting('测试')
        self.bridge.do_tts.assert_awaited_once()  # 两次屏幕刷新也只播报一次。
        self.assertIn('40 秒', self.screen.publish.call_args.args[1])

    async def test_final_answer_stops_wait_updates(self):
        started, stopped = asyncio.Event(), asyncio.Event()
        async def waiting(query):
            started.set()
            try:
                await asyncio.Future()  # 模拟尚在运行的等待提示任务。
            finally:
                stopped.set()
        async def answer(*args, **kwargs):
            await started.wait()
            return '测试回答'
        self.bridge._report_waiting = waiting
        self.bridge.chatbot.ask = answer
        chunks = [chunk async for chunk in self.bridge.ask_gpt('测试')]
        self.assertTrue(stopped.is_set())  # 回答返回前已经清理提示任务，不能覆盖最终答案。
        self.assertEqual(chunks, ['测试回答'])
        self.assertEqual(self.screen.publish.call_args.args[2], 'Hermes 回答')


if __name__ == '__main__':
    unittest.main()
