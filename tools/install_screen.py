"""Install the built screen APK to the configured and already-authorized LX04."""
from pathlib import Path
import json
import subprocess

ROOT=Path(__file__).resolve().parents[1]
ADB=ROOT/'tools/platform-tools/adb'
def run(*args):
    return subprocess.check_output([str(ADB),*args],text=True,timeout=60)
def main():
    config=json.loads((ROOT/'screen-config.json').read_text())
    devices=run('devices')
    ready={line.split()[0] for line in devices.splitlines()[1:] if len(line.split())>=2 and line.split()[1]=='device'}
    serial=config['usb_serial'] if config['usb_serial'] in ready else config['network']
    if serial not in ready: print(run('connect',serial))
    if run('-s',serial,'shell','getprop','ro.product.model').strip() != 'LX04':
        raise SystemExit('Expected LX04; refusing installation.')
    print(run('-s',serial,'install','-r',str(ROOT/'screen-app/build/hermes-screen.apk')))
    print(run('-s',serial,'shell','appops','set','local.hermes.screen','SYSTEM_ALERT_WINDOW','allow'))
    print(run('-s',serial,'shell','am','start','-n','local.hermes.screen/.MainActivity'))
if __name__=='__main__':main()
