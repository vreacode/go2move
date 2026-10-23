#!/usr/bin/env python3
"""Only StopMove; no velocity, gait or posture execution path."""
import json
from pathlib import Path
import time
from unitree_sdk2py.core import channel
from unitree_sdk2py.go2.sport.sport_client import SportClient
channel.ChannelConfigAutoDetermine=Path(__file__).with_name('cyclonedds_go2.xml').read_text()
channel.ChannelFactoryInitialize(0)
client=SportClient(False);client.SetTimeout(.5);client.Init()
results=[]
for _ in range(3):
    results.append(int(client.StopMove()));time.sleep(.05)
print(json.dumps({'stopped':all(code==0 for code in results),'stop_codes':results}))
