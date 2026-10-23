"""Restricted clothing-color description and OpenCV HSV screening.

Reuses cvtColor/inRange from the official OpenCV tutorial:
https://docs.opencv.org/4.x/df/d9d/tutorial_py_colorspaces.html
ROI and thresholds are heuristics, not person identity or clothing segmentation.
"""
import re
import cv2
import numpy as np

COLORS = {'红': 'red', '橙': 'orange', '橘': 'orange', '黄': 'yellow',
          '绿': 'green', '蓝': 'blue', '紫': 'purple', '黑': 'black',
          '白': 'white', '灰': 'gray'}
LABELS = {value: key for key, value in COLORS.items() if key != '橘'}
BGR = {'red': (30, 30, 225), 'orange': (0, 140, 255), 'yellow': (0, 230, 230),
       'green': (50, 180, 30), 'blue': (220, 70, 30), 'purple': (180, 40, 180),
       'black': (25, 25, 25), 'white': (235, 235, 235), 'gray': (125, 125, 125)}
HUES = {'red': ((0, 9), (170, 179)), 'orange': ((10, 24),), 'yellow': ((25, 34),),
        'green': ((35, 84),), 'blue': ((85, 129),), 'purple': ((130, 169),)}
_DESCRIPTION = re.compile(r'(?:请)?(?:帮我)?(?:寻找|搜索|查找|找(?:一下|到)?)?'
    r'(?:一个|那个|个)?(?:穿着|身穿|穿)?(?P<color>[红橙橘黄绿蓝紫黑白灰])(?:色)?(?:的)?'
    r'(?:上衣|衣服|衣|T恤|t恤|衬衫|外套)(?:的)?(?:人|人物|行人)?')


def parse_description(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('请输入单色上衣描述，例如：找穿红色上衣的人。')
    compact = re.sub(r'[\s，。！？,.!?]', '', text)
    if any(word in compact for word in ('不', '没', '非', '无', '除', '排除')):
        raise ValueError('暂不支持否定描述，只支持单色上衣。')
    mentions = [color for color in COLORS if color in compact]
    if len(mentions) != 1:
        raise ValueError('只支持一种上衣颜色：红、橙、黄、绿、蓝、紫、黑、白、灰。')
    match = _DESCRIPTION.fullmatch(compact)
    if not match:
        raise ValueError('暂不支持其他属性，请描述单色上衣，例如：找穿红色上衣的人。')
    color = COLORS[match.group('color')]
    return {'description': text.strip(), 'color': color, 'color_label': LABELS[color],
            'score_kind': 'color_fraction'}


def color_fraction(image, bbox, color):
    if color not in BGR:
        raise ValueError('Unsupported clothing color')
    height, width = image.shape[:2]
    x1, y1, x2, y2 = map(int, bbox)
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
    w, h = x2-x1, y2-y1
    if w < 12 or h < 24:
        raise ValueError('人物过小，无法筛选上衣颜色。')
    # Central upper torso reduces head/legs/background contamination.
    region = [x1+round(w*.20), y1+round(h*.18), x1+round(w*.80), y1+round(h*.55)]
    left, top, right, bottom = region
    hsv = cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_BGR2HSV)
    if color == 'black':
        mask = cv2.inRange(hsv, (0, 0, 0), (179, 255, 55))
    elif color == 'white':
        mask = cv2.inRange(hsv, (0, 0, 180), (179, 45, 255))
    elif color == 'gray':
        mask = cv2.inRange(hsv, (0, 0, 56), (179, 45, 179))
    else:
        mask = np.zeros(hsv.shape[:2], np.uint8)
        for low, high in HUES[color]:
            mask = cv2.bitwise_or(mask, cv2.inRange(hsv, (low, 65, 56), (high, 255, 255)))
    return float(cv2.countNonZero(mask) / mask.size), region


def description_image(color):
    """Plain swatch and text only; never invent a reference photograph."""
    image = np.full((240, 480, 3), 242, np.uint8)
    cv2.rectangle(image, (24, 28), (124, 128), BGR[color], -1)
    cv2.putText(image, 'UPPER CLOTHING: ' + color.upper(), (24, 164),
                cv2.FONT_HERSHEY_SIMPLEX, .65, (40, 40, 40), 2)
    cv2.putText(image, 'Color description only', (24, 208),
                cv2.FONT_HERSHEY_SIMPLEX, .6, (70, 70, 70), 1)
    return image
