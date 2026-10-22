"""Small dependency-free Android SDK build. Signing material stays in private state/."""
from pathlib import Path
import os, secrets, subprocess, zipfile, shutil

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'screen-app'
OUT = APP / 'build'
SDK = ROOT / 'tools/android-sdk'
BT = SDK / 'android-14'
JAVA = Path(os.environ['JAVA_HOME']) / 'bin' if os.environ.get('JAVA_HOME') else Path(shutil.which('javac') or '/opt/homebrew/opt/openjdk@21/bin/javac').resolve().parent
JAR = SDK / 'android-8.1.0/android.jar'
STATE = ROOT / 'state'
STATE.mkdir(mode=0o700, exist_ok=True)
os.environ['JAVA_HOME'] = str(JAVA.parent)
os.environ['PATH'] = str(JAVA) + ':' + os.environ['PATH']
def run(*args):
    subprocess.run([str(a) for a in args], check=True)
OUT.mkdir(exist_ok=True)
(OUT / 'classes').mkdir(exist_ok=True)
(OUT / 'dex').mkdir(exist_ok=True)
run(JAVA / 'javac', '--release', '8', '-classpath', JAR, '-d', OUT / 'classes', *APP.glob('src/**/*.java'))
run(JAVA / 'jar', 'cf', OUT / 'classes.jar', '-C', OUT / 'classes', '.')
run(BT / 'd8', '--lib', JAR, '--min-api', '26', '--output', OUT / 'dex', OUT / 'classes.jar')
run(BT / 'aapt', 'package', '-f', '-M', APP / 'AndroidManifest.xml', '-I', JAR, '-F', OUT / 'unsigned.apk')
with zipfile.ZipFile(OUT / 'unsigned.apk', 'a', compression=zipfile.ZIP_DEFLATED) as apk:
    apk.write(OUT / 'dex/classes.dex', 'classes.dex')
run(BT / 'zipalign', '-f', '4', OUT / 'unsigned.apk', OUT / 'aligned.apk')
password = STATE / 'screen-signing-password'
keystore = STATE / 'screen-signing.p12'
if not password.exists():
    password.write_text(secrets.token_hex(24)); password.chmod(0o600)
if not keystore.exists():
    run(JAVA / 'keytool', '-genkeypair', '-keystore', keystore, '-storepass:file', password,
        '-alias', 'hermes-screen', '-keyalg', 'RSA', '-keysize', '2048', '-validity', '10000',
        '-dname', 'CN=Hermes Local Screen')
    keystore.chmod(0o600)
run(BT / 'apksigner', 'sign', '--ks', keystore, '--ks-pass', 'file:' + str(password),
    '--out', OUT / 'hermes-screen.apk', OUT / 'aligned.apk')
run(BT / 'apksigner', 'verify', '--verbose', OUT / 'hermes-screen.apk')
print(OUT / 'hermes-screen.apk')
