"""验证未知固件或更新后的 Launcher 都不能绕过注入前检查。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_answer_guard import LAUNCHER_HASH, LAUNCHER_PATH, verify_firmware


class FirmwareChecks(unittest.TestCase):
    def check(self, version='2.42.206', digest=LAUNCHER_HASH, path=LAUNCHER_PATH):
        replies = {'getprop ro.mi.sw_ver': version,
                   'pm path com.xiaomi.micolauncher': 'package:' + path,
                   'sha256sum ' + LAUNCHER_PATH: digest + '  ' + LAUNCHER_PATH}
        return verify_firmware(replies.__getitem__)

    def test_both_verified_versions(self):
        for version in ('2.42.118', '2.42.206'):
            self.assertEqual(self.check(version=version), version)

    def test_unknown_firmware_refused(self):
        with self.assertRaises(RuntimeError):
            self.check(version='2.42.207')

    def test_changed_apk_refused(self):
        with self.assertRaises(RuntimeError):
            self.check(digest='0' * 64)

    def test_updated_package_path_refused(self):
        with self.assertRaises(RuntimeError):
            self.check(path='/data/app/new-version/base.apk')


if __name__ == '__main__':
    unittest.main()
