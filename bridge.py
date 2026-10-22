"""LX04 -> upstream XiaoGPT -> the existing local Hermes Agent API."""
from __future__ import annotations

import argparse
import asyncio
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


class ScreenMiGPT(MiGPT):
    def __init__(self, config, screen):
        super().__init__(config)
        self.screen = screen
        self.hermes_session = WeeklySession(
            STATE / 'hermes-session.json', f'agent:main:xiaoai:dm:{config.mi_did}')

    def _normalize(self, message):
        # Preserve paragraphs and punctuation on screen before Xiaomi TTS cleanup.
        self.screen_answer += message
        return super()._normalize(message)

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
        try:
            async for chunk in super().ask_gpt(query):
                answer = self.screen_answer
                self.screen.publish(visible_query, answer or '暂未收到回答，请稍后重试。',
                                    'Hermes 回答' if answer else '未收到回答')
                yield chunk
        except Exception:
            self.screen.publish(visible_query, '这次请求没有完成，请稍后再试。', '连接异常')
            raise


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
