from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hermes_session import WeeklySession, WEEK_SECONDS
from bridge import ScreenMiGPT


class WeeklySessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'session.json'
        self.db = Path(self.tmp.name) / 'state.db'
        self.session = WeeklySession(self.path, 'speaker-key', self.db)

    def test_restart_and_exact_seven_day_boundary(self):
        first = self.session.current(1000)
        restarted = WeeklySession(self.path, 'speaker-key', self.db)
        self.assertEqual(restarted.current(1000 + WEEK_SECONDS - 1), first)
        rotated = restarted.current(1000 + WEEK_SECONDS)
        self.assertNotEqual(rotated['session_id'], first['session_id'])
        self.assertEqual(rotated['started_at'], 1000 + WEEK_SECONDS)
        self.assertEqual(restarted.current(1000 + WEEK_SECONDS + 30), rotated)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_migrate_only_this_device_latest_transcript(self):
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE sessions(id TEXT, started_at REAL, session_key TEXT)')
            db.executemany('INSERT INTO sessions VALUES(?,?,?)',
                           [('older', 900, 'speaker-key'), ('current', 1000, 'speaker-key'),
                            ('other-device', 1100, 'other-key')])
        self.assertEqual(self.session.current(1200)['session_id'], 'current')
        self.assertEqual(self.session.current(1200)['started_at'], 1000)

    def test_inactive_weeks_create_only_one_new_conversation(self):
        first = self.session.current(1000)
        later = self.session.current(1000 + 4 * WEEK_SECONDS)
        self.assertNotEqual(first['session_id'], later['session_id'])
        self.assertEqual(later['started_at'], 1000 + 4 * WEEK_SECONDS)


class BridgeSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_turn_refreshes_id_and_drops_client_history(self):
        bridge = ScreenMiGPT.__new__(ScreenMiGPT)
        bridge.config = SimpleNamespace(prompt='voice instructions', stream=False, bot='chatgptapi',
                                       gpt_options={'extra_headers':{'X-Hermes-Session-Id':'old'}})
        bridge.screen = SimpleNamespace(publish=lambda *args: None)
        calls = []
        class Bot:
            history = [['old question', 'old reply']]
            async def ask(self, query, **options):
                calls.append((query, dict(options['extra_headers']), list(self.history)))
                return 'reply'
        bridge.chatbot = Bot()
        ids = iter(['old','new'])
        bridge.hermes_session = SimpleNamespace(headers=lambda: {'X-Hermes-Session-Id':next(ids)})
        for _ in range(2):
            self.assertEqual([x async for x in bridge.ask_gpt('question')], ['reply'])
        self.assertEqual([x[1]['X-Hermes-Session-Id'] for x in calls], ['old','new'])
        self.assertEqual([x[2] for x in calls], [[],[]])
        self.assertEqual(calls[1][0], 'question,voice instructions')


if __name__ == '__main__': unittest.main()
