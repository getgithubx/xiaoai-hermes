"""Install the pinned upstream bridge into this checkout (requires uv and git)."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / 'vendor/xiaogpt'
REVISION = '4258b0b53cf6af062f1a01af993d47a161382a0f'

def run(*args):
    subprocess.run([str(x) for x in args], cwd=ROOT, check=True)

def main():
    if not shutil.which('uv'):
        raise SystemExit('Install uv first: https://docs.astral.sh/uv/getting-started/installation/')
    if not UPSTREAM.exists():
        UPSTREAM.parent.mkdir(exist_ok=True)
        run('git', 'clone', 'https://github.com/yihong0618/xiaogpt.git', UPSTREAM)
        run('git', '-C', UPSTREAM, 'checkout', '--detach', REVISION)
    actual = subprocess.check_output(['git','-C',str(UPSTREAM),'rev-parse','HEAD'], text=True).strip()
    if actual != REVISION:
        raise SystemExit('Existing vendor checkout differs from pinned revision; inspect it first.')
    reverse = subprocess.run(['git','-C',str(UPSTREAM),'apply','--reverse','--check',str(ROOT/'all-queries.patch')],capture_output=True)
    if reverse.returncode:
        run('git','-C',UPSTREAM,'apply','--check',ROOT/'all-queries.patch')
        run('git','-C',UPSTREAM,'apply',ROOT/'all-queries.patch')
    if not (ROOT/'.venv').exists():
        run('uv','venv','--python','3.12',ROOT/'.venv')
    run('uv','pip','install','--python',ROOT/'.venv/bin/python','-r',UPSTREAM/'requirements.txt')
    (ROOT/'state').mkdir(mode=0o700, exist_ok=True)
    target = ROOT/'screen-config.json'
    if not target.exists():
        shutil.copyfile(ROOT/'screen-config.example.json',target)
    print('Bridge installed. Configure screen-config.json and your local Hermes API, then run connect.command.')

if __name__ == '__main__': main()
