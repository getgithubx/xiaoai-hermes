"""Generate a per-user LaunchAgent with the current checkout's absolute paths."""
import os
from pathlib import Path
import plistlib
import platform

ROOT = Path(__file__).resolve().parents[1]

def main():
    if platform.system() != 'Darwin':
        raise SystemExit('LaunchAgents require macOS.')
    state = ROOT/'state'; state.mkdir(mode=0o700,exist_ok=True)
    log = state/'background.log'; log.touch(mode=0o600,exist_ok=True);log.chmod(0o600)
    target = Path.home()/'Library/LaunchAgents/local.xiaoai-hermes.plist'
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        raise SystemExit('LaunchAgent already exists; inspect it instead of overwriting a running installation: '+str(target))
    data={'Label':'local.xiaoai-hermes', 'ProgramArguments':[str(ROOT/'.venv/bin/python'),'-u',str(ROOT/'bridge.py')],
          'WorkingDirectory':str(ROOT),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':60,'ProcessType':'Background',
          'StandardOutPath':str(log),'StandardErrorPath':str(log)}
    target.write_bytes(plistlib.dumps(data));target.chmod(0o600)
    print('Installed',target,'\nRun start-background.command to start.')

if __name__ == '__main__': main()
