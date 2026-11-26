"""Generate a per-user LaunchAgent with the current checkout's absolute paths."""
import argparse
import os
from pathlib import Path
import plistlib
import platform

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--native-answer', action='store_true', help='安装可选的官方应答过滤服务')
    args = parser.parse_args()
    # 共用路径生成逻辑；两个服务使用独立标签和日志，互不覆盖。
    label = 'local.xiaoai-hermes-native-answer' if args.native_answer else 'local.xiaoai-hermes'
    script = 'native_answer_guard.py' if args.native_answer else 'bridge.py'
    log_name = 'native-answer-guard.log' if args.native_answer else 'background.log'
    if platform.system() != 'Darwin':
        raise SystemExit('LaunchAgents require macOS.')
    state = ROOT/'state'; state.mkdir(mode=0o700,exist_ok=True)
    log = state/log_name; log.touch(mode=0o600,exist_ok=True);log.chmod(0o600)
    target = Path.home()/'Library/LaunchAgents'/(label+'.plist')
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        raise SystemExit('LaunchAgent already exists; inspect it instead of overwriting a running installation: '+str(target))
    data={'Label':label, 'ProgramArguments':[str(ROOT/'.venv/bin/python'),'-u',str(ROOT/script)],
          'WorkingDirectory':str(ROOT),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':60,'ProcessType':'Background',
          'StandardOutPath':str(log),'StandardErrorPath':str(log)}
    target.write_bytes(plistlib.dumps(data));target.chmod(0o600)
    print('Installed', target)
    print('Start with the launchctl bootstrap command in docs/NATIVE_ANSWERS.md.' if args.native_answer else 'Run start-background.command to start.')

if __name__ == '__main__': main()
