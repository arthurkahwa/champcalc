"""
Called by the GitHub Action only when update_season.py rate-limited a new
notification for a completed race (at most once per race, never more —
see update_season.py's is_new_race_result gate).

Reads the bundled event payload update_season.py wrote (all eliminations
and clinches from that single race, combined into one push) and forwards
it to the one real piece of backend compute in this system: a thin
serverless function that actually holds device tokens and talks to APNs.
This script doesn't implement push delivery itself.
"""

import json
import os
import sys
from pathlib import Path

import requests

PUSH_FUNCTION_URL = os.environ.get('PUSH_FUNCTION_URL')
PUSH_FUNCTION_TOKEN = os.environ.get('PUSH_FUNCTION_TOKEN')
PAYLOAD_PATH = Path(__file__).parent.parent / 'data' / '.notification_payload.json'


def main() -> None:
    if not PUSH_FUNCTION_URL:
        print('PUSH_FUNCTION_URL not set — skipping notification', file=sys.stderr)
        return

    if not PAYLOAD_PATH.exists():
        print('No notification payload found — nothing to send', file=sys.stderr)
        return

    payload = json.loads(PAYLOAD_PATH.read_text())

    resp = requests.post(
        PUSH_FUNCTION_URL,
        headers={'Authorization': f'Bearer {PUSH_FUNCTION_TOKEN}'},
        json=payload,   # { season, round, events: [{entity_id, position, event}, ...] }
        timeout=10,
    )
    resp.raise_for_status()
    print(f'Notification triggered for round {payload["round"]}: {resp.status_code}', file=sys.stderr)

    # Delete so a re-run of this step (e.g. a retried workflow) doesn't resend
    PAYLOAD_PATH.unlink(missing_ok=True)


if __name__ == '__main__':
    main()

