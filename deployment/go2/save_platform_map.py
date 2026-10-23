#!/usr/bin/env python3
"""Save using upstream slam_toolbox serialization and Nav2 map_saver_cli."""
import argparse,json,os,subprocess,sys,time,math
from pathlib import Path
import rclpy
from slam_toolbox.srv import SerializePoseGraph
from tf2_ros import Buffer,TransformListener
from nav_msgs.msg import OccupancyGrid
from rclpy.qos import QoSProfile,DurabilityPolicy
p=argparse.ArgumentParser();p.add_argument('--directory',required=True);args=p.parse_args()
from go2_platform_write import backup
out=Path(args.directory).resolve();backup(out);out.mkdir(parents=True,exist_ok=True)
for name in ['posegraph.data','posegraph.posegraph','map.pgm','map.yaml','snapshot.json','map_saver.log']:backup(out/name)
rclpy.init();n=rclpy.create_node('go2_map_snapshot');b=Buffer();l=TransformListener(b,n);maps=[]
s=n.create_subscription(OccupancyGrid,'/map',lambda m:maps.append(m),QoSProfile(depth=1,durability=DurabilityPolicy.TRANSIENT_LOCAL))
report={'motion_sent':False,'directory':str(out)}
def spin(sec):
 end=time.monotonic()+sec
 while time.monotonic()<end:rclpy.spin_once(n,timeout_sec=.05)
try:
 spin(4)
 t=b.lookup_transform('map','base_footprint',rclpy.time.Time());q=t.transform.rotation
 report['map_pose']=[t.transform.translation.x,t.transform.translation.y,math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))]
 if not maps:raise RuntimeError('No map received')
 m=maps[-1];report['map']={'width':m.info.width,'height':m.info.height,'resolution':m.info.resolution,'free':sum(0<=v<=50 for v in m.data),'occupied':sum(v>50 for v in m.data)}
 client=n.create_client(SerializePoseGraph,'/slam_toolbox/serialize_map')
 if not client.wait_for_service(timeout_sec=4):raise RuntimeError('SLAM serialization unavailable')
 req=SerializePoseGraph.Request();req.filename=str(out/'posegraph');f=client.call_async(req)
 rclpy.spin_until_future_complete(n,f,timeout_sec=10)
 if not f.done() or f.exception() is not None:raise RuntimeError('Serialization failed or timed out')
 for name in ['posegraph.data','posegraph.posegraph']:
  if not (out/name).is_file() or (out/name).stat().st_size==0:raise RuntimeError('Serialization did not write '+name)
 env=os.environ.copy();env['CYCLONEDDS_URI']='/home/unitree/go2move/ros2/go2_bringup/config/dds/map_saver.xml'
 with (out/'map_saver.log').open('w') as log:
  result=subprocess.run(['ros2','run','nav2_map_server','map_saver_cli','-f',str(out/'map'),'--free','0.196','--ros-args','-p','save_map_timeout:=10000'],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=18)
 if result.returncode or not (out/'map.yaml').exists() or not (out/'map.pgm').exists():raise RuntimeError('Nav2 map_saver_cli failed; see map_saver.log')
 report['files']={name:(out/name).stat().st_size for name in ['map.yaml','map.pgm','posegraph.data','posegraph.posegraph']};report['saved']=True
except Exception as e:report.update(saved=False,error=str(e))
finally:
 (out/'snapshot.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2));n.destroy_node();rclpy.shutdown()
raise SystemExit(0 if report.get('saved') else 1)
