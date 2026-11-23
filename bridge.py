"""LX04 -> upstream XiaoGPT -> the existing local Hermes Agent API."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
import fcntl
import getpass
import json
import logging
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
TOKEN = STATE / ".mi.token"
DEVICE = STATE / "device.json"
sys.path.insert(0, str(ROOT / "vendor/xiaogpt"))

import httpx
from aiohttp import ClientSession
from dotenv import dotenv_values
from miservice import MiNAService
from xiaogpt.bot.chatgptapi_bot import ChatGPTBot
from xiaogpt.config import Config
from xiaogpt.xiaogpt import MiAccount, MiGPT
from screen_display import ScreenDisplay
from hermes_session import WeeklySession


class VoiceChatGPTBot(ChatGPTBot):  # 仅调整音箱请求，不影响 Hermes 的其他入口。
    def _make_openai_client(self, sess):  # 沿用上游模型地址、认证和 HTTP 会话。
        client = super()._make_openai_client(sess)  # 不复制认证配置，避免密钥进入日志。
        client.max_retries = 0  # 超时并不代表服务端停止，重发会重复执行用户任务。
        return client  # 保留配置中的单次请求超时，不进行隐式补发。


class LX04MiNAService(MiNAService):  # 只适配这台 LX04 的播报，其他小米云接口继续继承原实现。
    async def text_to_speech(self, device_id, text):  # 同时供等待提示和 Hermes 正式回答调用，避免两条播报路径不一致。
        return await self.ubus_request(  # 复用现有 micoapi 登录；不经过当前报错的 xiaomiio/MIoT 接口。
            device_id, 'ai_tts_request', 'mibrain',  # 此方法已由原机 2.42.118 代码和实际音频验证；其他固件需重新核对。
            {'tts_text': text, 'show_ui': False})  # 保留原始文本、交给 JSON 编码；不弹出官方字幕，继续使用 Hermes 屏显。


class ScreenMiGPT(MiGPT):
    async def login_miboy(self, refresh_token=False):  # 初次登录及令牌刷新都走这里，确保刷新后不会丢失 LX04 适配。
        await super().login_miboy(refresh_token)  # 先沿用上游账号和令牌处理，避免复制或改变现有认证逻辑。
        self.mina_service = LX04MiNAService(self.mina_service.account)  # 在初始化设备和 TTS 对象前替换服务，保留同一账号会话。

    def __init__(self, config, screen):
        super().__init__(config)
        self.chatbot = VoiceChatGPTBot.from_config(config)  # 当前桥接固定使用 ChatGPT 兼容 API。
        self.screen = screen
        self.hermes_session = WeeklySession(
            STATE / 'hermes-session.json', f'agent:main:xiaoai:dm:{config.mi_did}')

    def _normalize(self, message):
        # Preserve paragraphs and punctuation on screen before Xiaomi TTS cleanup.
        self.screen_answer += message
        return super()._normalize(message)

    async def _report_waiting(self, query):  # 等待反馈与模型请求并行，不再次提交问题。
        elapsed = 0  # 只用于显示等待时间，不推测模型实际进度。
        while True:  # 最终回答或异常到达后，由 ask_gpt 取消此任务。
            await asyncio.sleep(20)  # 简单问题不增加提示；长任务每二十秒刷新屏幕。
            elapsed += 20  # 使用累计等待值，不显示系统时区或个人数据。
            self.screen.publish(query, f'Hermes 仍在处理，已等待约 {elapsed} 秒。', '等待回答')  # 仍是等待状态，不冒充答案。
            if elapsed == 20:  # 仅播报一次，避免不断打扰用户。
                try:
                    await asyncio.wait_for(self.do_tts('赫尔墨斯还在处理，请稍候。'), timeout=8)  # 播报故障也不能卡住等待任务。
                except Exception:
                    logging.getLogger(__name__).warning('等待提示暂未播出')  # 不输出可能包含账号信息的异常正文。

    async def ask_gpt(self, query):
        # Refresh for every turn, even if the daemon runs across the seven-day boundary.
        headers = self.hermes_session.headers()
        previous_id = self.config.gpt_options.get('extra_headers', {}).get('X-Hermes-Session-Id')
        self.config.gpt_options['extra_headers'] = headers
        # Hermes loads authoritative history by ID. Never replay old local history
        # into a new weekly conversation, or let a process restart lose continuity.
        self.chatbot.history.clear()
        suffix = ',' + self.config.prompt
        if previous_id != headers['X-Hermes-Session-Id'] and self.config.prompt and not query.endswith(suffix):
            query += suffix
        visible_query = query
        suffix = ',' + self.config.prompt
        if self.config.prompt and visible_query.endswith(suffix):
            visible_query = visible_query[:-len(suffix)]
        self.screen.publish(visible_query, '正在思考，请稍候…', '思考中')
        self.screen_answer = ''
        waiting = asyncio.create_task(self._report_waiting(visible_query))  # 独立提示任务不改变七天会话。
        try:
            async for chunk in super().ask_gpt(query):
                waiting.cancel()  # 在显示答案前停止刷新，避免等待文字覆盖答案。
                with suppress(asyncio.CancelledError):
                    await waiting  # 确认提示任务已退出，避免遗留后台任务。
                if not chunk:  # 上游将请求异常转换为空字符串，必须给用户可听见的反馈。
                    self.screen.publish(visible_query, '暂未收到 Hermes 回答；任务可能仍在处理，请稍后再试。', '未收到回答')
                    yield '暂未收到赫尔墨斯的回答，任务可能仍在处理，请稍后再试。'  # 明确等待失败，不声称已取消服务端任务。
                    continue
                answer = self.screen_answer
                self.screen.publish(visible_query, answer or '暂未收到回答，请稍后重试。',
                                    'Hermes 回答' if answer else '未收到回答')
                yield chunk
        except Exception:
            self.screen.publish(visible_query, '这次请求没有完成，请稍后再试。', '连接异常')
            raise
        finally:
            waiting.cancel()  # 请求异常或调用者取消时，也必须回收等待提示。
            with suppress(asyncio.CancelledError):
                await waiting  # 不留下会覆盖下一轮屏幕的任务。


def load_config() -> Config:
    # Keep the existing Hermes key in its original file, never in YAML/argv.
    key = dotenv_values(Path.home() / ".hermes/.env").get("API_SERVER_KEY")
    if not key:
        raise RuntimeError("现有 Hermes .env 中没有 API_SERVER_KEY。")
    settings = Config.read_from_file(str(ROOT / "config.yaml"))
    settings["openai_key"] = key
    config = Config(**settings)
    if DEVICE.exists():
        device = json.loads(DEVICE.read_text())
        config.account = device.get("account", "")
        config.mi_did = device["miotDID"]
        config.gpt_options["extra_headers"] = {
            "X-Hermes-Session-Key": f"agent:main:xiaoai:dm:{config.mi_did}"
        }
    return config


async def check_hermes(config: Config, chat: bool = False) -> None:
    async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
        response = await client.get(
            f"{config.api_base}/models",
            headers={"Authorization": f"Bearer {config.openai_key}"},
        )
        if response.status_code != 200:
            raise RuntimeError(f"Hermes API 鉴权检查失败，HTTP {response.status_code}。")
        models = [item["id"] for item in response.json()["data"]]
        if config.gpt_options["model"] not in models:
            raise RuntimeError("Hermes API 模型名称已变化，请检查配置。")
    print("Hermes Agent API 连接和鉴权正常。")
    if chat:
        config.gpt_options['extra_headers'] = WeeklySession(
            STATE / 'hermes-session.json', f'agent:main:xiaoai:dm:{config.mi_did}').headers()
        bot = ChatGPTBot.from_config(config)
        reply = await bot.ask(
            "这是语音桥接连通性测试。不要使用任何工具，只回复：Hermes语音接口已就绪。",
            **config.gpt_options,
        )
        if not reply or not reply.strip():
            raise RuntimeError("Hermes 未返回有效文字。")
        print("XiaoGPT → Hermes Agent → 文本回复测试通过。")


async def login() -> None:
    print("登录绑定这台 LX04 的小米账号。密码仅用于本次登录，不保存到配置文件。")
    if TOKEN.exists():
        account_name = json.loads(DEVICE.read_text()).get("account", "") if DEVICE.exists() else ""
        password = ""
        print("尝试复用本机保存的登录令牌；若失效，请运行 connect.command --login。")
    else:
        account_name = input("小米账号（手机号或账号ID）：").strip()
        if not account_name:
            raise RuntimeError("未输入小米账号。")
        password = getpass.getpass("小米密码（输入不显示）：")
    async with ClientSession() as session:
        account = MiAccount(session, account_name, password, str(TOKEN))
        devices = await MiNAService(account).device_list() or []
        candidates = [d for d in devices if d.get("hardware", "").upper() == "LX04"]
        if not candidates:
            raise RuntimeError("该账号未找到 LX04，请确认音箱绑定账号和设备联网状态。")
        for index, device in enumerate(candidates, 1):
            print(f"{index}. {device.get('name', '小爱触屏音箱')}  LX04  DID={device.get('miotDID', '')}")
        index = 0 if len(candidates) == 1 else int(input("选择音箱编号：")) - 1
        if not 0 <= index < len(candidates):
            raise RuntimeError("无效设备编号。")
        device = candidates[index]
        if not device.get("miotDID") or not device.get("deviceID"):
            raise RuntimeError("小米返回的设备信息不完整。")
        saved = {"account": account_name, "miotDID": str(device["miotDID"]),
                 "deviceID": device["deviceID"], "hardware": "LX04"}
        DEVICE.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n")
    if TOKEN.exists():
        TOKEN.chmod(0o600)
    DEVICE.chmod(0o600)
    print("LX04 已选定，登录令牌已保存到本机私有 state 目录。")


async def run() -> None:
    config = load_config()
    await check_hermes(config)
    if not (TOKEN.exists() and DEVICE.exists()):
        await login()
        config = load_config()
    screen = ScreenDisplay()
    screen.start()
    bridge = ScreenMiGPT(config, screen)
    bridge.chatbot.name = "Hermes"
    try:
        print("开始接收所有音箱提问。请说：小爱同学，你是谁？")
        print("按 Control+C 停止；此窗口需保持运行。")
        await bridge.run_forever()
    finally:
        await screen.close()
        await bridge.close()


def main() -> None:
    os.umask(0o077)
    # MiService's exception logs may contain login response tokens.
    logging.getLogger("miservice").disabled = True
    STATE.mkdir(mode=0o700, exist_ok=True)
    STATE.chmod(0o700)
    os.environ["XIAOGPT_MI_TOKEN_PATH"] = str(TOKEN)
    for name in ("NO_PROXY", "no_proxy"):
        current = os.environ.get(name, "")
        os.environ[name] = ",".join(filter(None, (current, "127.0.0.1", "localhost")))
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="测试实际 Hermes 文本问答，不连接小米")
    parser.add_argument("--login", action="store_true", help="重新输入账号并登录")
    args = parser.parse_args()
    # Prevent two listeners from replying to the same spoken question.
    with (STATE / "bridge.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("桥接已在另一个窗口运行，请勿重复启动。")
        if args.check:
            asyncio.run(check_hermes(load_config(), chat=True))
        else:
            if args.login and TOKEN.exists():
                TOKEN.rename(STATE / ".mi.token.previous")
            asyncio.run(run())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n桥接已停止。")
    except Exception as exc:
        # SDK exception bodies may include account cookies; don't dump them.
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print(f"未能完成：{message}", file=sys.stderr)
        sys.exit(1)
