"""Download official macOS Android tools; verify hashes published by Google."""
from pathlib import Path
import hashlib
import platform
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://dl.google.com/android/repository/'

def main():
    if platform.system() != 'Darwin':
        raise SystemExit('This helper is for macOS; install equivalent Android SDK tools on other platforms.')
    sdk = ROOT/'tools/android-sdk'; sdk.mkdir(parents=True,exist_ok=True)
    with urllib.request.urlopen(BASE+'repository2-1.xml', timeout=60) as response:
        index = ET.fromstring(response.read())
    for package in ['platforms;android-27','build-tools;34.0.0','platform-tools']:
        item = next(p for p in index.findall('remotePackage') if p.get('path') == package)
        archive = next(a for a in item.findall('./archives/archive') if a.findtext('host-os') in (None,'macosx'))
        name = archive.findtext('complete/url'); checksum = archive.findtext('complete/checksum')
        target = sdk/name
        if not target.exists() or hashlib.sha1(target.read_bytes()).hexdigest() != checksum:
            urllib.request.urlretrieve(BASE+name,target)
        if hashlib.sha1(target.read_bytes()).hexdigest() != checksum:
            raise RuntimeError('Official archive checksum mismatch: '+name)
        destination = ROOT/'tools' if package == 'platform-tools' else sdk
        with zipfile.ZipFile(target) as z:
            z.extractall(destination)
        print('Verified and extracted',package)
    for name in ['aapt','zipalign','apksigner','d8']:
        (sdk/'android-14'/name).chmod(0o755)
    (ROOT/'tools/platform-tools/adb').chmod(0o755)

if __name__ == '__main__': main()
