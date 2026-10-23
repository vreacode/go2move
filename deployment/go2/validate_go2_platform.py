#!/usr/bin/env python3
"""Passive hardware/software validation; no robot control or navigation goals."""
import rclpy,time,json,math,collections,pathlib,os,sys
import numpy as np
from hesai_ros_driver.msg import LossPacket
from sensor_msgs.msg import PointCloud2,LaserScan,Imu,JointState
from nav_msgs.msg import Odometry,OccupancyGrid
from geometry_msgs.msg import Twist
from std_msgs.msg import String
from rclpy.qos import qos_profile_sensor_data,QoSProfile,DurabilityPolicy
from tf2_ros import Buffer,TransformListener
from lifecycle_msgs.srv import GetState
rclpy.init();n=rclpy.create_node('go2_platform_validation');b=Buffer();listener=TransformListener(b,n)
records=collections.defaultdict(list);last={};ages=collections.defaultdict(list);stamps=collections.defaultdict(list);subs=[];status=None
active=False

def cb(topic,m):
 global status
 last[topic]=m
 if topic=='/go2/status':status=json.loads(m.data)
 if not active:return
 records[topic].append(time.monotonic())
 if hasattr(m,'header'):
  s=m.header.stamp.sec+m.header.stamp.nanosec/1e9
  ages[topic].append(time.time()-s);stamps[topic].append(s)
for topic,typ,q in [('/lidar_packets_loss',LossPacket,10),('/lidar_points',PointCloud2,10),('/scan',LaserScan,qos_profile_sensor_data),('/odom',Odometry,10),('/imu/data',Imu,10),('/joint_states',JointState,10),('/go2/status',String,10),('/nav2/cmd_vel_preview',Twist,10),('/map',OccupancyGrid,QoSProfile(depth=1,durability=DurabilityPolicy.TRANSIENT_LOCAL))]:
 subs.append(n.create_subscription(typ,topic,lambda m,t=topic:cb(t,m),q))
def spin(seconds):
 end=time.monotonic()+seconds
 while time.monotonic()<end:rclpy.spin_once(n,timeout_sec=.05)
spin(8)
def udp_drops():
 result={}
 for table in ['/proc/net/udp','/proc/net/udp6']:
  for line in pathlib.Path(table).read_text().splitlines()[1:]:
   f=line.split()
   if len(f)>12 and f[1].endswith(':0940'):result[f[9]]=int(f[-1])
 return result
loss_before=last.get('/lidar_packets_loss');before=udp_drops();active=True;start=time.monotonic();spin(60);elapsed=time.monotonic()-start;active=False;after=udp_drops()
report={'duration_seconds':elapsed,'topics':{},'radar_socket_drop_delta':{i:after[i]-before.get(i,after[i]) for i in after},'status':status,'tf':{},'nav2_lifecycle':{}}
for topic,times in records.items():
 d=[v-u for u,v in zip(times,times[1:])];a=ages[topic];s=stamps[topic]
 report['topics'][topic]={'count':len(times),'hz':(len(times)-1)/(times[-1]-times[0]) if len(times)>1 else 0,'max_gap_ms':max(d)*1000 if d else None,'gaps_over_200ms':sum(x>.2 for x in d),'stamp_backwards':sum(v<u for u,v in zip(s,s[1:])), 'age_mean_ms':sum(a)/len(a)*1000 if a else None,'age_max_ms':max(a)*1000 if a else None}
for target,source in [('map','base_link'),('odom','base_footprint'),('base_link','hesai_lidar'),('base_link','imu')]+[('odom',leg+'_foot') for leg in ['FR','FL','RR','RL']]:
 try:
  t=b.lookup_transform(target,source,rclpy.time.Time());v=t.transform.translation;q=t.transform.rotation
  report['tf'][target+'->'+source]={'xyz':[v.x,v.y,v.z],'xyzw':[q.x,q.y,q.z,q.w]}
 except Exception as e:report['tf'][target+'->'+source]={'error':str(e)}
if '/lidar_points' in last:
 m=last['/lidar_points'];report['cloud']={'frame':m.header.frame_id,'points':m.width*m.height,'fields':[f.name for f in m.fields]}
if '/lidar_packets_loss' in last:
 m=last['/lidar_packets_loss'];report['radar_packet_counter']={'total':m.total_packet_count,'total_lost':m.total_packet_loss_count,'loss_delta':m.total_packet_loss_count-loss_before.total_packet_loss_count if loss_before else None}
if '/lidar_points' in last:
 m=last['/lidar_points'];fields={f.name:f for f in m.fields};dtype=np.dtype({'names':['x','y','z','timestamp'],'formats':['<f4','<f4','<f4','<f8'],'offsets':[fields[k].offset for k in ['x','y','z','timestamp']],'itemsize':m.point_step});arr=np.frombuffer(m.data,dtype=dtype)
 report['cloud']['finite_xyz_points']=int(np.count_nonzero(np.isfinite(arr['x']) & np.isfinite(arr['y']) & np.isfinite(arr['z'])))
 report['cloud']['point_timestamp_age_seconds']=[float(time.time()-np.max(arr['timestamp'])),float(time.time()-np.min(arr['timestamp']))]
if '/scan' in last:
 m=last['/scan'];report['scan']={'frame':m.header.frame_id,'finite':sum(math.isfinite(x) for x in m.ranges),'bins':len(m.ranges)}
if '/map' in last:
 m=last['/map'];report['map']={'width':m.info.width,'height':m.info.height,'resolution':m.info.resolution,'occupied':sum(x>50 for x in m.data),'free':sum(0<=x<=50 for x in m.data)}
for name in ['planner_server','controller_server','bt_navigator']:
 client=n.create_client(GetState,'/'+name+'/get_state')
 if client.wait_for_service(timeout_sec=3):
  future=client.call_async(GetState.Request());rclpy.spin_until_future_complete(n,future,timeout_sec=3)
  report['nav2_lifecycle'][name]=future.result().current_state.label if future.done() and future.result() else 'timeout'
 else:report['nav2_lifecycle'][name]='unavailable'
report['motion_preview_messages']=len(records['/nav2/cmd_vel_preview'])
report['robot_command_publishers']={t:[x.node_name for x in n.get_publishers_info_by_topic(t)] for t in ['/api/sport/request','/cmd_vel','/lowcmd']}
output=pathlib.Path(sys.argv[1]);output.write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2));n.destroy_node();rclpy.shutdown()
