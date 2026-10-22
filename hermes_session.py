"""Persist a speaker's Hermes conversation; rotate on the first turn after seven days."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

WEEK_SECONDS = 7 * 24 * 60 * 60


class WeeklySession:
    def __init__(self, path: Path, session_key: str, database: Path | None = None):
        self.path = path
        self.key = session_key
        self.database = database or Path.home() / '.hermes/state.db'

    def _existing(self):
        if not self.database.exists():
            return None
        # Read only. Hermes owns its transcript database.
        with sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True) as db:
            row = db.execute(
                'SELECT id, started_at FROM sessions WHERE session_key=? '
                'ORDER BY started_at DESC LIMIT 1', (self.key,)).fetchone()
        return {'session_id': row[0], 'started_at': row[1], 'session_key': self.key} if row else None

    def current(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        state = json.loads(self.path.read_text()) if self.path.exists() else self._existing()
        if (state is None or state['session_key'] != self.key
                or now >= state['started_at'] + WEEK_SECONDS):
            state = {'session_id': 'xiaoai-' + uuid.uuid4().hex,
                     'started_at': now, 'session_key': self.key}
        # Atomic replace; the bridge process lock serializes production callers.
        if not self.path.exists() or json.loads(self.path.read_text()) != state:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            with open(temporary, 'w', opener=lambda p, f: os.open(p, f, 0o600)) as file:
                json.dump(state, file, ensure_ascii=False, indent=2)
                file.write('\n')
            temporary.chmod(0o600)
            temporary.replace(self.path)
        return state

    def headers(self) -> dict[str, str]:
        return {'X-Hermes-Session-Key': self.key,
                'X-Hermes-Session-Id': self.current()['session_id']}
