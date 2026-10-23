"""Pure decoder for Go2 telemetry. Fresh state never grants movement permission.

Official Go2 fault table and receive-only motor error example:
https://support.unitree.com/home/en/developer/Fault_service
https://support.unitree.com/home/en/developer/Read_motor_errors
Official Markdown: https://doc-cdn.unitree.com/6/56/en/6_56_en
                   https://doc-cdn.unitree.com/6/198/en/6_198_en
"""
import math

STALE_SECONDS = 2.0
JOINT_NAMES = tuple(leg + '_' + str(axis) for leg in ('FR', 'FL', 'RR', 'RL') for axis in range(3))
MOTOR_FLAGS = ((0x01, '过流'), (0x02, '过压'), (0x04, '驱动过热'),
               (0x08, '母线欠压'), (0x10, '绕组过热'), (0x20, '编码器异常'),
               (0x100, '电机通信中断'))
# Fault_service source 100 = LowState.bit_flag. Deprecated bits are not inferred.
SYSTEM_FLAGS = ((0x40, 'MCU通信异常'), (0x80, '电机通信异常'),
                (0x10, '电池通信异常'), (0x02, '配电开关异常'))
FAULT_SOURCE_URL = 'https://support.unitree.com/home/en/developer/Fault_service'


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _array(value, length, integer=False):
    if not isinstance(value, (list, tuple)) or len(value) != length:
        return None
    result = [_number(item) for item in value]
    if any(item is None for item in result):
        return None
    if integer and any(not isinstance(item, int) or item < 0 or item > 0xffffffff for item in result):
        return None
    return result


def summarize_health(raw, now):
    """Decode a JSON snapshot using host receipt times, failing closed on stale data."""
    raw = raw if isinstance(raw, dict) else {}
    now = _number(now)
    stamps = [_number(raw.get(key)) for key in ('sampled_at', 'low_at', 'sport_at')]
    ages = [now - stamp if now is not None and stamp is not None else None for stamp in stamps]
    age_s = max(ages) if all(age is not None and age >= 0 for age in ages) else None
    temperatures = _array(raw.get('temperatures'), 12)
    if temperatures is not None and any(temp < 0 or temp > 255 for temp in temperatures):
        temperatures = None
    codes = _array(raw.get('motor_faults'), 12, integer=True)
    height, soc = _number(raw.get('height')), _number(raw.get('soc'))
    if soc is not None and not 0 <= soc <= 100:
        soc = None
    connected = (raw.get('state_available') is True and raw.get('running', True) is True
                 and age_s is not None and age_s <= STALE_SECONDS
                 and temperatures is not None and codes is not None
                 and height is not None and soc is not None)
    faults = []
    known_mask = sum(flag for flag, _ in MOTOR_FLAGS)
    for index, code in enumerate(codes or []):
        if not code:
            continue
        messages = [message for flag, message in MOTOR_FLAGS if code & flag]
        unknown = code & ~known_mask
        if unknown:
            messages.append('未定义故障位 0x%X' % unknown)
        faults.append({'index': index, 'joint': JOINT_NAMES[index],
                       'code_hex': '0x%02X' % code, 'messages': messages})
    bit_flag = raw.get('bit_flag', 0)
    if isinstance(bit_flag, bool) or not isinstance(bit_flag, int) or not 0 <= bit_flag <= 255:
        bit_flag = None
        connected = False
    system_faults = [message for flag, message in SYSTEM_FLAGS if bit_flag is not None and bit_flag & flag]
    thermal = [JOINT_NAMES[index] for index, code in enumerate(codes or []) if code & 0x14]
    communication = any(code & 0x100 for code in codes or []) or (bit_flag is not None and bool(bit_flag & 0xd0))
    if not connected:
        reason = '遥测离线或陈旧，保持停止。'
    elif thermal:
        reason = '过热报警（%s），保持停止。' % '、'.join(thermal)
    elif communication:
        reason = '通信异常报警，保持停止。'
    elif faults or system_faults:
        reason = '设备故障报警，保持停止。'
    else:
        reason = '无当前故障报警；仅只读监测，动作未授权。'
    return {'connected': bool(connected), 'sampled_at': stamps[0], 'age_s': age_s,
            'max_temperature_c': max(temperatures) if temperatures is not None else None,
            'motor_temperatures_c': temperatures,
            'rear_left_hip_temperature_c': temperatures[9] if temperatures is not None else None,
            'rear_right_hip_temperature_c': temperatures[6] if temperatures is not None else None,
            'body_height_m': height, 'soc': soc, 'power_v': _number(raw.get('power_v')),
            'rpy': _array(raw.get('rpy'), 3),
            'remote': raw.get('remote') if isinstance(raw.get('remote'), dict) else None,
            'faults': faults, 'system_faults': system_faults, 'low_state_bit_flag': bit_flag,
            'motion_allowed': False, 'reason': reason, 'fault_source_url': FAULT_SOURCE_URL}
