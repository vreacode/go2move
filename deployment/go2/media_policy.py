"""Persistent local pause after the observed loss of stance; no device writes."""
import json
from pathlib import Path

POLICY = Path(__file__).resolve().parent/'config/media_policy.json'


def voice_policy():
    try:
        value = json.loads(POLICY.read_text())
        return {'enabled': value.get('voice_enabled') is True,
                'reason': str(value.get('reason', '语音暂时停用。'))}
    except (OSError, ValueError, AttributeError):
        return {'enabled': False, 'reason': '语音暂停状态不可读取，保持停用。'}


if __name__ == '__main__':
    raise SystemExit(0 if voice_policy()['enabled'] else 1)
