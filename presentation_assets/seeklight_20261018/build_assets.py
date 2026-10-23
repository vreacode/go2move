#!/usr/bin/env python3
"""Build local, synthetic PPT/UI design assets. No network or robot interfaces."""
import base64
import html
import io
import math
import os
from pathlib import Path
import sys
import tempfile
import re
import zipfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import QByteArray, QRectF, QSizeF, QMarginsF
from PyQt5.QtGui import QGuiApplication, QImage, QPainter, QPdfWriter
from PyQt5.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, '/home/unitree/go2move/deployment/go2')
from go2_platform_write import backup, write

C = dict(ink='#12223D', muted='#536882', blue='#2563EB', cyan='#0284C7',
         teal='#0F766E', pale='#EAF2FF', paper='#F5F8FD', line='#DCE5F1',
         red='#DC4053', amber='#B77918', white='#FFFFFF')
FONT = 'Noto Sans CJK SC'
slides = []
app = QGuiApplication([])


def rect(x, y, w, h, fill='white', radius=18, stroke=None, sw=1, dash=None):
    attrs = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ''
    if dash:
        attrs += f' stroke-dasharray="{dash}"'
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}"{attrs}/>'


def text(x, y, content, size=24, color=None, weight=400, anchor='start'):
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" text-anchor="{anchor}" fill="{color or C["ink"]}">'
            f'{html.escape(str(content))}</text>')


def lines(x, y, content, size=23, gap=36, color=None, weight=400):
    return ''.join(text(x, y+i*gap, line, size, color, weight) for i, line in enumerate(content))


def line(x1, y1, x2, y2, color=None, width=2, dash=None):
    extra = f' stroke-dasharray="{dash}"' if dash else ''
    return f'<path d="M {x1} {y1} L {x2} {y2}" fill="none" stroke="{color or C["line"]}" stroke-width="{width}" stroke-linecap="round"{extra}/>'


def circle(x, y, r, fill, stroke=None, sw=1):
    extra = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ''
    return f'<circle cx="{x}" cy="{y}" r="{r}" fill="{fill}"{extra}/>'


def arrow(points, color=None, width=3, dash=None):
    color = color or C['blue']
    extra = f' stroke-dasharray="{dash}"' if dash else ''
    d = 'M '+' L '.join(f'{x} {y}' for x, y in points)
    x, y = points[-1]
    a, b = points[-2]
    angle = math.atan2(y-b, x-a)
    head = [(x, y), (x-12*math.cos(angle-.45), y-12*math.sin(angle-.45)),
            (x-12*math.cos(angle+.45), y-12*math.sin(angle+.45))]
    return (f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}"{extra}/>'
            f'<polygon points="{" ".join(f"{u},{v}" for u,v in head)}" fill="{color}"/>')


def pill(x, y, label, w=None, color=None, bg=None, size=19):
    w = w or len(label)*size+34
    return rect(x, y, w, 37, bg or C['pale'], 18)+text(x+w/2, y+25, label, size, color or C['blue'], 500, 'middle')


def button(x, y, w, label, fill=None, fg='white', h=48, size=20):
    return rect(x, y, w, h, fill or C['blue'], 13)+text(x+w/2, y+h*.65, label, size, fg, 600, 'middle')


def person(x, y, scale=1, shirt='#DE654E'):
    return (f'<g transform="translate({x} {y}) scale({scale})">'
            + circle(0, 0, 18, '#EDCDB7')
            + '<path d="M-18 -3 Q-19 -27 3 -23 Q21 -22 18 -1 L13 -9 Q-3 -7 -10 -14 Z" fill="#26354D"/>'
            + '<path d="M-20 25 Q0 13 20 25 L32 84 L17 88 L9 55 L12 106 L-12 106 L-9 55 L-17 88 L-32 84 Z" fill="'+shirt+'"/>'
            + '<path d="M-12 102 L-14 165 L-2 165 L0 116 L4 165 L17 165 L12 102 Z" fill="#334155"/>'
            + rect(-17, 161, 18, 7, '#1E293B', 3)+rect(3, 161, 18, 7, '#1E293B', 3)+'</g>')


def robot(x, y, scale=1):
    return (f'<g transform="translate({x} {y}) scale({scale})">'
            + '<ellipse cx="5" cy="130" rx="145" ry="15" fill="#DFE7F1"/>'
            + '<path d="M-85 25 L-108 70 L-68 100 L-85 127 M65 25 L92 70 L58 100 L72 127" fill="none" stroke="#8395AC" stroke-width="15" stroke-linecap="round" stroke-linejoin="round"/>'
            + rect(-109, -19, 213, 66, '#D5DDE8', 27)
            + rect(-94, -13, 180, 37, '#EDF2F7', 19)
            + rect(71, -12, 53, 44, '#27364C', 15)
            + rect(100, -4, 29, 24, '#152238', 8)
            + circle(114, 8, 7, '#5DC2F5')
            + rect(-24, -42, 44, 24, '#334155', 7)
            + '<ellipse cx="-2" cy="-43" rx="22" ry="7" fill="#52637B"/>'
            + '<path d="M-61 28 L-84 76 L-38 104 L-47 133 M42 29 L70 77 L27 104 L36 133" fill="none" stroke="#34445D" stroke-width="16" stroke-linecap="round" stroke-linejoin="round"/>'
            + circle(-61, 28, 12, '#5E718C')+circle(42, 29, 12, '#5E718C')
            + text(-27, 18, 'Go2', 22, '#566981', 600)+'</g>')


def camera_scene(x, y, w, h, boxed=False):
    s = rect(x, y, w, h, '#E8EEF5', 14)
    s += f'<path d="M{x} {y+h} L{x+w*.33} {y+h*.3} L{x+w*.68} {y+h*.3} L{x+w} {y+h} Z" fill="#CCD8E6"/>'
    s += rect(x+14, y+18, w*.22, h*.56, '#D6E0EC', 5)
    s += rect(x+w*.76, y+20, w*.19, h*.56, '#D4DFEC', 5)
    ps = h/230
    s += person(x+w*.54, y+h*.19, ps)
    if boxed:
        s += rect(x+w*.54-38*ps, y+h*.19-27*ps, 76*ps, 205*ps,
                  'none', 7, C['teal'], 2)
    return s


def map_diagram(x, y, w, h, compact=False):
    s = rect(x, y, w, h, '#F1F5FA', 16)
    sx, sy = w/340, h/230
    s += f'<g transform="translate({x} {y}) scale({sx} {sy})">'
    for xx, yy, ww, hh in [(18, 18, 110, 72), (202, 18, 118, 72),
                            (18, 153, 110, 59), (202, 153, 118, 59)]:
        s += rect(xx, yy, ww, hh, '#D5DFEB', 6)
        s += line(xx+12, yy+14, xx+ww-12, yy+14, '#B4C3D6', 3)
    s += '<path d="M46 126 L164 126 L164 114 L276 114" stroke="#7EA4EF" stroke-width="18" stroke-linecap="round" fill="none" opacity=".22"/>'
    s += '<path d="M46 126 L164 126 L164 114 L276 114" stroke="#2563EB" stroke-width="4" stroke-linecap="round" fill="none" stroke-dasharray="7 5"/>'
    for i, (xx, yy) in enumerate([(48,126),(164,126),(276,114)]):
        s += circle(xx, yy, 10, 'white', C['blue'], 3)
        s += text(xx, yy+4, i+1, 11, C['blue'], 700, 'middle')
    s += circle(147, 125, 9, C['teal'])
    s += '<path d="M147 108 L141 122 L153 122 Z" fill="#0F766E"/>'
    if not compact:
        s += text(22, 39, 'A 区', 13, '#566981', 600)+text(211, 39, 'B 区', 13, '#566981', 600)
    return s+'</g>'


def phone_shell(title, subtitle, body, tab=0):
    s = rect(6, 6, 408, 858, '#D7E1EF', 48)
    s += rect(1, 1, 408, 858, '#11213A', 48)
    s += rect(11, 11, 388, 838, C['paper'], 39)
    s += rect(156, 21, 98, 21, '#11213A', 11)
    s += text(34, 45, '9:41', 14, C['ink'], 600)
    s += rect(346, 31, 24, 11, '#64748B', 3)+rect(373, 34, 3, 5, '#64748B', 1)
    s += text(32, 91, title, 25, C['ink'], 700)
    s += text(32, 118, subtitle, 15, C['muted'])
    s += body
    s += rect(12, 770, 386, 75, 'white', 0)
    for i, label in enumerate(['找人','地图','记录','设备']):
        xx=60+i*96
        s += circle(xx, 789, 7, C['blue'] if i==tab else '#B7C4D5')
        s += text(xx, 818, label, 15, C['blue'] if i==tab else C['muted'], 500, 'middle')
    s += rect(151, 837, 109, 4, '#34445D', 2)
    return s


def task_phone():
    s=rect(30, 141, 350, 44, '#E7EEF8', 13)
    s+=rect(34, 145, 169, 36, 'white', 10)
    s+=text(117, 170, '拍照选人', 17, C['blue'], 600, 'middle')
    s+=text(291, 170, '描述找人', 17, C['muted'], 500, 'middle')
    s+=rect(30, 202, 350, 282, 'white', 19, C['line'])
    s+=text(48, 235, '目标人物', 19, weight=600)
    s+=camera_scene(48, 250, 314, 163, True)
    s+=text(205, 440, '从机身画面拍照并选定一个人', 14, C['muted'], anchor='middle')
    s+=text(205, 467, '重新拍照', 16, C['blue'], 600, 'middle')
    s+=rect(30, 499, 350, 80, 'white', 17, C['line'])
    s+=text(48, 529, '搜索区域', 16, C['muted'])
    s+=text(48, 558, 'A 区 · 实验室走廊', 20, weight=600)
    s+=text(351, 549, '›', 28, C['muted'])
    s+=rect(30, 595, 350, 67, '#ECF4FF', 16)
    s+=circle(58, 628, 15, '#D8E7FF')+rect(54,616,8,16,'none',4,C['blue'],2)
    s+='<path d="M50 626 L50 629 Q58 640 66 629 L66 626 M58 636 L58 642 M54 642 L62 642" fill="none" stroke="#2563EB" stroke-width="2" stroke-linecap="round"/>'
    s+=text(85, 622, '也可以说一句', 14, C['muted'])+text(85, 647, '“去 A 区找这个人”', 18, C['ink'], 500)
    s+=button(30, 682, 350, '生成寻人计划', h=51)
    s+=text(205, 754, '执行移动前确认现场条件', 13, C['muted'], anchor='middle')
    return phone_shell('寻光搭子', '拍张照片，让它帮你找人', s)


def search_phone():
    s=pill(30, 143, '正在搜索 A 区', 190, C['teal'], '#E3F3ED', 17)
    s+=text(376, 168, '01:24', 17, C['muted'], anchor='end')
    s+=map_diagram(30, 195, 350, 225)
    s+=rect(42, 207, 151, 29, 'white', 10)+text(117, 227, '已检查 1 / 3 个点', 13, C['ink'], anchor='middle')
    s+=text(30, 454, '当前观察', 20, weight=600)
    s+=camera_scene(30, 471, 350, 166, True)
    s+=rect(42, 482, 139, 27, '#12223D', 8)+text(112, 501, 'A-02 观察点', 13, 'white', anchor='middle')
    s+=circle(43, 666, 5, C['teal'])+text(57, 672, '停稳后观察，发现候选再复核', 15, C['muted'])
    s+=button(30, 695, 155, '暂停任务', '#E6EDF7', C['ink'])
    s+=button(197, 695, 183, '停止任务', '#FFF0F2', C['red'])
    return phone_shell('搜索过程', '地图、画面和任务进度在一起', s, 1)


def review_phone():
    s=pill(30, 143, '发现疑似目标', 183, C['amber'], '#FFF1D9', 17)
    s+=text(30, 208, '请确认，是要找的这个人吗？', 20, weight=600)
    s+=text(30, 235, '外观相似不等于身份确认', 15, C['muted'])
    s+=rect(30, 255, 350, 264, 'white', 17, C['line'])
    for xx,label in [(44,'参考目标'),(218,'当前候选')]:
        s+=camera_scene(xx, 269, 148, 190, label=='当前候选')
        s+=text(xx+74, 491, label, 16, C['muted'], anchor='middle')
    s+=rect(30, 535, 350, 79, '#ECF4FF', 16)
    s+=text(48, 566, '发现于 A-02 观察点', 19, C['ink'], 600)
    s+=text(48, 593, '14:32 · 已保存候选证据', 15, C['muted'])
    s+=button(30, 640, 350, '确认是目标', h=51)
    s+=button(30, 704, 350, '不是这个人，继续寻找', '#E6EDF7', C['ink'])
    return phone_shell('候选复核', '机器提供证据，由你完成确认', s)


def history_phone():
    s=rect(30, 143, 350, 83, '#EAF2FF', 18)
    s+=text(49, 177, '今天的发现', 16, C['muted'])
    s+=text(49, 210, '2 条记录', 26, C['blue'], 700)
    for yy, status, color, bg, point, time_ in [(245,'已确认',C['teal'],'#E3F3ED','A-02','14:32'),(443,'待确认',C['amber'],'#FFF1D9','A-01','14:29')]:
        s+=rect(30, yy, 350, 181, 'white', 17, C['line'])
        s+=camera_scene(45, yy+16, 105, 112, True)
        s+=text(167, yy+44, '目标人物', 20, weight=600)
        s+=pill(167, yy+58, status, 101, color, bg, 15)
        s+=text(167, yy+124, point+' · '+time_, 16, C['muted'])
        s+=line(47, yy+140, 362, yy+140)
        s+=text(47, yy+166, '查看证据照片与确认记录', 15, C['blue'])
    s+=button(30, 667, 350, '导出本次任务记录', '#E6EDF7', C['ink'], 51)
    s+=text(205, 747, '任务结束，发现与复核记录保留', 13, C['muted'], anchor='middle')
    return phone_shell('发现记录', '每一次找到，都有据可查', s, 2)


PHONES=[task_phone(), search_phone(), review_phone(), history_phone()]


def wrap(body, w=1920, h=1080):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<title>寻光搭子：Go2 寻人系统设计示意</title>{body}</svg>')


def board(title, subtitle, number):
    s=rect(0,0,1920,1080,C['paper'],0)
    s+=rect(0,0,1920,12,C['blue'],0)
    s+=text(80,68,'SEEKLIGHT  /  GO2',20,C['blue'],700)
    s+=text(80,130,title,44,weight=700)+text(80,177,subtitle,23,C['muted'])
    s+=line(80,1017,1840,1017)
    s+=text(80,1050,'方案与界面设计示意 · 含待联调及待实机验收功能',18,C['muted'])
    s+=text(1838,1050,f'{number:02d}',20,C['blue'],600,'end')
    return s


def save_asset(name, body, w=1920, h=1080, deck=False):
    svg=wrap(body,w,h)
    path=ROOT/'svg'/(name+'.svg');write(path,svg)
    renderer=QSvgRenderer(QByteArray(svg.encode('utf-8')))
    if not renderer.isValid():raise RuntimeError('Invalid SVG: '+name)
    factor=2 if w<600 else 1.5
    image=QImage(int(w*factor),int(h*factor),QImage.Format_ARGB32)
    image.fill(0)
    painter=QPainter(image);painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter);painter.end()
    png=ROOT/'png'/(name+'.png');backup(png);png.parent.mkdir(parents=True,exist_ok=True)
    if not image.save(str(png)):raise RuntimeError('PNG save failed')
    if deck:slides.append((name,svg,png))


def make_cover():
    s=board('寻光搭子','语言驱动的校园主动寻人机器狗',1)
    s+=text(85,320,'一句话，',75,weight=700)+text(85,422,'帮你找到 TA。',75,C['blue'],700)
    s+=lines(88,494,['拍照指定目标，前往区域搜索。','停步观察、复核候选、播报并记录。'],28,44,C['muted'])
    for xx,label,ww in [(88,'多模态任务',173),(278,'地图导航',151),(446,'视觉寻人',151),(614,'证据反馈',151)]:
        s+=pill(xx,612,label,ww,size=21)
    s+=rect(87,714,708,216,'white',24,C['line'])
    s+=text(116,757,'一个完整任务',21,C['muted'],600)
    s+=lines(116,808,['“去 A 区找照片里的人，','  找到后告诉我。”'],32,48,C['ink'],600)
    s+=rect(898,244,936,687,'white',32,C['line'])
    s+=map_diagram(930,278,870,362)
    s+=robot(1353,636,1.62)
    s+=pill(1221,880,'Go2 · 场景化演示方案',274,size=20)
    save_asset('01_项目总览',s,deck=True)


def make_mobile():
    s=board('一个手机入口，完成一次找人任务','复用现有前端，设计适合手机使用的任务、地图、复核与记录界面',2)
    labels=[('01','任务设置','拍照选人，指定搜索区域'),('02','搜索过程','看地图、看画面、随时停止'),('03','候选复核','确认身份，保留判断依据'),('04','发现记录','回看证据，导出任务结果')]
    for i,(num,title,desc) in enumerate(labels):
        xx=110+i*470
        s+=f'<g transform="translate({xx} 232) scale(.69)">{PHONES[i]}</g>'
        s+=text(xx+145,884,num+'  '+title,25,C['ink'],700,'middle')
        s+=text(xx+145,926,desc,20,C['muted'],anchor='middle')
    save_asset('02_手机交互界面',s,deck=True)
    for i,name in enumerate(['任务设置','搜索过程','候选复核','发现记录']):
        save_asset(f'app_{i+1:02d}_{name}',PHONES[i],420,876)


def card(x,y,w,h,title,desc,color=None,dashed=False):
    s=rect(x,y,w,h,'white',20,color or C['line'],2,'8 6' if dashed else None)
    s+=text(x+24,y+43,title,26,color or C['ink'],650)
    s+=lines(x+24,y+85,desc,21,33,C['muted'])
    return s


def make_architecture():
    s=board('从任务理解，到可执行的机器狗技能','语义规划与实时执行分层；地图、观测和任务记录构成反馈',3)
    for xx,title,caption in [(80,'用户与前端','说清需求'),(532,'任务理解与计划','生成受限任务'),(984,'NX 本地执行','按观测采取行动'),(1436,'结果与反馈','把证据交给用户')]:
        s+=text(xx,255,title,27,weight=700)+text(xx,290,caption,21,C['muted'])
    s+=card(80,326,374,186,'多模态入口',['拍照 / 语音 / 文字','搜索区域与目标条件'])
    s+=card(80,539,374,176,'任务操作',['预览、确认、开始','暂停、取消、查看记录'])
    s+=card(532,326,374,186,'任务解析与工具选择',['结构化目标、区域与步骤','大模型能力待接入','视觉问答与澄清（规划）'],C['blue'],True)
    s+=card(532,539,374,176,'任务状态管理',['到点 → 观察 → 复核','超时、取消与结果回执'])
    s+=card(984,326,374,186,'导航与障碍处理',['地图定位与观察点导航','受限执行 · 待实机验收'],C['blue'],True)
    s+=card(984,539,374,176,'感知与身份复核',['图像检测 / 短时跟踪','参考照片匹配 / 多帧确认'])
    s+=card(1436,326,374,186,'现场反馈',['候选照片与观察点','发现提示与人工确认'])
    s+=card(1436,539,374,176,'任务证据',['搜索轨迹、时间与状态','发现记录与确认结果'])
    for xx in [454,906,1358]:s+=arrow([(xx+9,420),(xx+61,420)])
    s+=rect(80,791,1730,153,'#EAF2FF',22)
    s+=text(114,837,'共享地图与任务记忆',26,C['blue'],700)
    s+=text(114,883,'已保存地图  /  区域与观察点  /  目标照片  /  历史发现  /  任务事件',24,C['muted'])
    s+=arrow([(1624,723),(1624,775)],C['teal'])
    s+=arrow([(720,776),(720,727)],C['teal'])
    s+=text(1290,981,'虚线框：待接入或待实机验收模块',19,C['muted'])
    save_asset('03_系统架构',s,deck=True)


def make_demo():
    s=board('现场展示：让一个任务完整发生','从目标输入到发现记录，屏幕同步显示机器狗为什么继续、停下或请求确认',4)
    titles=['拍照下任务','前往观察点','停步搜索','补看与确认','播报并记录']
    desc=[['拍照选定目标','指定 A 区'],['展示计划与路径','自主到达观察点'],['实时检测与匹配','连续观察候选'],['看不清时补看','相似人由用户复核'],['停步报告结果','保存照片与时间']]
    for i in range(5):
        xx=80+i*353
        s+=rect(xx,281,321,468,'white',23,C['line'])
        s+=pill(xx+23,305,'0'+str(i+1),64,size=20)
        s+=text(xx+24,400,titles[i],29,weight=700)
        s+=lines(xx+24,457,desc[i],23,40,C['muted'])
        if i==0:s+=camera_scene(xx+24,555,273,145,True)
        elif i==1:s+=map_diagram(xx+24,555,273,145,True)
        elif i==2:s+=person(xx+154,578,.61)+rect(xx+126,558,57,121,'none',6,C['teal'],2)
        elif i==3:
            s+=person(xx+95,578,.61)+person(xx+210,578,.61,'#5E89C5')
            s+=text(xx+211,563,'?',26,C['amber'],700,'middle')
        else:
            s+=rect(xx+24,558,273,132,'#E6F3ED',16)
            s+=text(xx+44,605,'发现疑似目标',23,C['teal'],650)
            s+=text(xx+44,646,'照片 · 时间 · 观察点',19,C['muted'])
        if i<4:s+=arrow([(xx+328,493),(xx+346,493)],width=2)
    s+=rect(80,811,1732,134,'#EAF2FF',23)
    s+=text(112,859,'演示中的研究场景',25,C['blue'],700)
    s+=text(112,904,'行人横穿 → 遮挡 / 漏检 → 风险判断 → 减速、等待或继续通行',27,C['ink'],500)
    save_asset('04_现场演示流程',s,deck=True)


def occlusion_scene(x,y,w,h,visible):
    s=rect(x,y,w,h,'#F1F5FA',18)
    s+=robot(x+74,y+h-78,.50)
    if visible:
        s+=f'<path d="M{x+122} {y+h-76} L{x+w-55} {y+27} L{x+w-20} {y+h-22} Z" fill="#D6E6FF"/>'
    else:
        s+=f'<path d="M{x+122} {y+h-76} L{x+w*.58} {y+37} L{x+w*.58} {y+h-18} Z" fill="#D6E6FF"/>'
    s+=('<g opacity=".32">' if visible else '')+person(x+w-78,y+78,.68)+('</g>' if visible else '')
    if visible:s+=text(x+w-78,y+62,'?',25,C['amber'],700,'middle')
    if not visible:s+=rect(x+w*.55,y+33,47,h-51,'#A8B8CD',3)
    s+=text(x+20,y+28,'可观测区域' if visible else '遮挡区域',18,C['muted'],600)
    return s


def make_research():
    s=board('面向遮挡与间歇性漏检的风险导航','拟研究：区分“本应看见却没有检测到”与“被遮挡所以看不见”',5)
    s+=rect(80,239,1028,481,'white',25,C['line'])
    s+=text(110,292,'相同的“未检出”，可能意味着不同的风险',28,weight=700)
    s+=occlusion_scene(110,324,469,236,False)
    s+=occlusion_scene(604,324,469,236,True)
    s+=text(112,608,'被遮挡时未检出',26,C['blue'],650)
    s+=text(112,648,'结合记忆，保留障碍风险',22,C['muted'])
    s+=text(606,608,'可见区域持续未检出',26,C['teal'],650)
    s+=text(606,648,'结合观测可靠性，更新风险',22,C['muted'])
    s+=rect(1151,239,660,481,'#EAF2FF',25)
    s+=text(1184,292,'拟研究的方法链路',28,C['blue'],700)
    for i,(title,desc) in enumerate([('观测可靠性','距离、稀疏程度、遮挡与运动状态'),('障碍存在与预测风险','有观测 / 无观测时的条件概率更新'),('导航行为约束','依据风险选择速度、等待或绕行')]):
        yy=319+i*121
        s+=rect(1182,yy,596,98,'white',17)
        s+=text(1205,yy+37,title,25,weight=650)+text(1205,yy+72,desc,21,C['muted'])
        if i<2:s+=arrow([(1480,yy+101),(1480,yy+116)],width=2)
    s+=text(82,780,'用统一条件的对照与消融，验证方法价值',27,weight=700)
    for i,label in enumerate(['安全边界侵犯','误停车次数','任务完成时间','运行延迟']):
        xx=80+i*441
        s+=rect(xx,814,407,126,'white',20,C['line'])
        s+=text(xx+25,858,'评价指标',17,C['muted'])+text(xx+25,905,label,29,C['ink'],650)
    s+=text(82,984,'本页为研究设计示意，尚不代表已验证的算法创新或性能提升。',20,C['muted'])
    save_asset('05_论文研究切入',s,deck=True)


def make_preview_pdf():
    dest=ROOT/'寻光搭子_PPT素材预览.pdf';backup(dest)
    pdf=QPdfWriter(str(dest));pdf.setResolution(144)
    pdf.setPageSizeMM(QSizeF(338.6667,190.5))
    pdf.setPageMargins(QMarginsF(0,0,0,0))
    pdf.setTitle('寻光搭子：PPT与手机界面设计素材')
    painter=QPainter(pdf)
    for i,(_,svg,_) in enumerate(slides):
        if i:pdf.newPage()
        QSvgRenderer(QByteArray(svg.encode())).render(painter,QRectF(0,0,pdf.width(),pdf.height()))
    painter.end()
    return dest


def make_flat_odp():
    temp=Path(tempfile.mkdtemp(prefix='go2-ppt-build-20261018-'))
    out=temp/'寻光搭子_PPT排版示例.fodp'
    ns='''xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0" xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" xmlns:xlink="http://www.w3.org/1999/xlink"'''
    pages=[]
    for i,(name,_,png) in enumerate(slides):
        b64=base64.b64encode(png.read_bytes()).decode()
        pages.append(f'''<draw:page draw:name="{html.escape(name)}" draw:style-name="dp1" draw:master-page-name="Default"><draw:frame draw:style-name="gr1" draw:layer="layout" svg:width="33.86667cm" svg:height="19.05cm" svg:x="0cm" svg:y="0cm"><draw:image><office:binary-data>{b64}</office:binary-data></draw:image></draw:frame></draw:page>''')
    xml=f'''<?xml version="1.0" encoding="UTF-8"?><office:document {ns} office:version="1.2" office:mimetype="application/vnd.oasis.opendocument.presentation"><office:automatic-styles><style:page-layout style:name="PM1"><style:page-layout-properties fo:page-width="33.86667cm" fo:page-height="19.05cm" style:print-orientation="landscape"/></style:page-layout><style:style style:name="dp1" style:family="drawing-page"><style:drawing-page-properties presentation:background-visible="true" presentation:background-objects-visible="true"/></style:style><style:style style:name="gr1" style:family="graphic"><style:graphic-properties draw:stroke="none" draw:fill="none"/></style:style></office:automatic-styles><office:master-styles><style:master-page style:name="Default" style:page-layout-name="PM1" draw:style-name="dp1"/></office:master-styles><office:body><office:presentation>{''.join(pages)}</office:presentation></office:body></office:document>'''
    out.write_text(xml)
    (ROOT/'build_input_path.txt').write_text(str(out)+'\n')
    return out


def make_readme():
    readme='''寻光搭子 · PPT设计素材包（2026-10-18）

建议定位：Go2语言驱动的校园主动寻人系统。
参考：用户提供的AirStar 33页PDF，尤其是第1/30页的系统结构与第32页的分层决策思想。

这是一组静态设计与讲演素材，不是实机截图或已部署App，不连接任何机器人接口。
蓝白风格沿用当前前端FRONTEND_STYLE.md。当前已具备手机网页前端，因此先设计移动端界面，复用原服务；原生Android/iOS App不作为首版依赖。

内容
01 项目总览：主题、宣传语、典型指令与Go2示意。
02 手机交互界面：任务设置、搜索过程、候选复核、发现记录。
03 系统架构：多模态入口、任务规划、NX执行、结果证据、共享地图与记忆。
04 现场演示流程：拍照下任务→观察点导航→停步搜索→补看确认→播报记录。
05 论文研究切入：遮挡/间歇性漏检下的观测可靠性与风险导航，包含拟评价指标。
app_01..04：四张单独的手机界面，可插入其他PPT页。
Go2示意图：透明背景矢量/PNG，可放进系统架构图；为概念插图，不代表实际安装尺寸。

格式与使用
- svg：可缩放的矢量源图，保留独立文字及图形。编辑时需字体Noto Sans CJK SC；Office对SVG拆分编辑的支持随版本而异。
- png：无需字体的高清导出；讲演页2880×1620，手机页840×1752。
- PPT素材预览.pdf：5页矢量预览。
- PPT排版示例.pptx（若生成）：5页PNG排版示例，可更换整页图片；页内文字未拆为PowerPoint文本框。逐项修改请编辑SVG或生成脚本再导出。
- build_assets.py：本地可复现生成器，依赖本机Python/PyQt5，不包含模型或机器人控制。

界面原则
手机端优先四件事：定目标、看过程、确认候选、查记录。内部ROS、IP、模型参数留在工程端。
参考照片、候选照片采用原创人物矢量占位示意；没有使用真实目标照片或真实身份记录。
地图、位置、计时、记录数量均为构图示例，非当前实机数据。位置标观察点，不冒充已标定的目标三维位置。
候选界面使用“疑似”“待确认”，没有把外观相似度写成身份概率。
“停止任务”是任务操作设计，不替代手柄或硬件急停。

实现边界
现有基础：雷达与SLAM、照片/衣着寻人、人工复核与发现记录、语音接口；各模块实时状态需现场核验。
待接入/待验收：自由语言任务规划、视觉问答、真实自主观察点导航与补看、风险导航方法。
研究页为候选机制，不代表已验证原创性、性能提升或论文录用。
本次仅生成展示素材；没有修改线上前端、ROS配置、运动策略，也没有启动/停止机器狗服务或发送动作。

讲演建议
先讲找人场景，再展示完整任务；接着用架构图解释模块如何合作，最后用实验数据证明研究贡献。
将来把示意界面和人物占位替换为已验收的真实运行截图、真实录像和同条件实验结果。
'''
    write(ROOT/'使用说明.txt',readme)


def normalise_pptx_canvas():
    """Match the canvas to our full-slide images after LibreOffice FODP import."""
    p=ROOT/'寻光搭子_PPT排版示例.pptx'
    if not p.exists():return
    with zipfile.ZipFile(p) as z:
        src=z.read('ppt/presentation.xml')
        changed,n=re.subn(rb'(<p:sldSz\b[^>]*\bcx=")[0-9]+',rb'\g<1>12192120',src)
        changed,m=re.subn(rb'(<p:sldSz\b[^>]*\bcy=")[0-9]+',rb'\g<1>6858000',changed)
        if n!=1 or m!=1:raise RuntimeError('PPTX canvas element not found')
        if changed==src:return
        result=io.BytesIO()
        with zipfile.ZipFile(result,'w',zipfile.ZIP_DEFLATED) as dest:
            for info in z.infolist():
                dest.writestr(info,changed if info.filename=='ppt/presentation.xml' else z.read(info.filename))
    backup(p);p.write_bytes(result.getvalue())


def package():
    normalise_pptx_canvas()
    dest=ROOT.parent/'寻光搭子_PPT设计素材_20261018.zip';backup(dest)
    with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(ROOT.rglob('*')):
            if p.is_file() and p.name not in ['build_input_path.txt']:
                z.write(p,'寻光搭子_PPT设计素材/'+str(p.relative_to(ROOT)))
    return dest


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--package':
        print(package());raise SystemExit
    make_cover();make_mobile();make_architecture();make_demo();make_research()
    save_asset('Go2_示意图_透明背景',robot(250,102,1.45),500,340)
    make_preview_pdf();make_readme()
    p=ROOT/'build_input_path.txt';backup(p)
    print('LO input:',make_flat_odp())
    print('Created 5 slide designs and 4 standalone mobile screens.')
