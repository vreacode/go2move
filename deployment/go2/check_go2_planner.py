#!/usr/bin/env python3
"""ComputePathToPose only. Never sends NavigateToPose or FollowPath."""
import rclpy,time,json,math,pathlib,sys
from rclpy.action import ActionClient
from rclpy.qos import QoSProfile,DurabilityPolicy
from nav_msgs.msg import OccupancyGrid
from nav2_msgs.action import ComputePathToPose
from tf2_ros import Buffer,TransformListener
from slam_toolbox.srv import SerializePoseGraph
rclpy.init();n=rclpy.create_node('go2_planning_validation');b=Buffer();l=TransformListener(b,n);maps=[]
s=n.create_subscription(OccupancyGrid,'/global_costmap/costmap',lambda m:maps.append(m),QoSProfile(depth=1,durability=DurabilityPolicy.TRANSIENT_LOCAL))
t=time.monotonic()
while time.monotonic()-t<10:rclpy.spin_once(n,timeout_sec=.1)
report={'action':'ComputePathToPose only','goals':[]};a=ActionClient(n,ComputePathToPose,'/compute_path_to_pose')
try:
 t=b.lookup_transform('map','base_footprint',rclpy.time.Time());x=t.transform.translation.x;y=t.transform.translation.y
 candidates=[]
 if maps:
  m=maps[-1];r=m.info.resolution;ox=m.info.origin.position.x;oy=m.info.origin.position.y
  for iy in range(m.info.height):
   for ix in range(m.info.width):
    gx=ox+(ix+.5)*r;gy=oy+(iy+.5)*r;d=math.hypot(gx-x,gy-y)
    if .25<d<.65 and 0<=m.data[iy*m.info.width+ix]<30:candidates.append((abs(d-.4),gx,gy))
 candidates=sorted(candidates)[::max(1,len(candidates)//10)][:10]
 if not candidates:candidates=[(0,x,y)]
 if not a.wait_for_server(timeout_sec=5):raise RuntimeError('Planner action server unavailable')
 for _,gx,gy in candidates:
  goal=ComputePathToPose.Goal();goal.pose.header.frame_id='map';goal.pose.header.stamp=n.get_clock().now().to_msg();goal.pose.pose.position.x=gx;goal.pose.pose.position.y=gy;goal.pose.pose.orientation.w=1.;goal.planner_id='GridBased'
  f=a.send_goal_async(goal);rclpy.spin_until_future_complete(n,f,timeout_sec=5)
  if not f.done():raise RuntimeError('goal response timeout')
  handle=f.result()
  if not handle.accepted:report['goals'].append({'xy':[gx,gy],'accepted':False});continue
  f=handle.get_result_async();rclpy.spin_until_future_complete(n,f,timeout_sec=10)
  if not f.done():raise RuntimeError('compute path result timeout')
  result=f.result();poses=result.result.path.poses
  report['goals'].append({'xy':[gx,gy],'status':result.status,'poses':len(poses),'planning_seconds':result.result.planning_time.sec+result.result.planning_time.nanosec/1e9})
  if result.status==4 and len(poses)>1:
   report['path']=[[p.pose.position.x,p.pose.position.y] for p in poses];break
except Exception as e:report['error']=str(e)
c=n.create_client(SerializePoseGraph,'/slam_toolbox/serialize_map')
if c.wait_for_service(timeout_sec=5):
 req=SerializePoseGraph.Request();req.filename='/home/unitree/go2_ros2_ws/maps/initial-20261005/posegraph'
 f=c.call_async(req);rclpy.spin_until_future_complete(n,f,timeout_sec=10);report['posegraph_saved']=f.done() and f.result() is not None
pathlib.Path(sys.argv[1]).write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2));a.destroy();n.destroy_node();rclpy.shutdown()
