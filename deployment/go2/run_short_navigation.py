#!/usr/bin/env python3
"""One guarded Nav2 NavigateToPose on a previously confirmed straight route.
Reuses Nav2 planner/controller and the proven Unitree ROS2 Move/Stop adapter.
Without --execute, refuses to arm or send a navigation goal.
"""
import argparse,json,math,os,signal,subprocess,sys,time
from pathlib import Path
import rclpy
from rclpy.action import ActionClient
from nav2_msgs.action import NavigateToPose,ComputePathToPose
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Twist
from std_msgs.msg import String
from tf2_ros import Buffer
from route_tf import RouteTransformListener
p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--distance',type=float,default=.30);p.add_argument('--backend',choices=['ros2','sdk2'],default='ros2');p.add_argument('--output',required=True);p.add_argument('--goal-from');args=p.parse_args()
if not args.execute:raise SystemExit('Refusing real navigation without --execute')
if not .20<=args.distance<=.30:raise SystemExit('This supervised navigation test is capped at 30 cm')
from go2_platform_write import backup
out=Path(args.output).resolve();backup(out);backup(out.with_suffix('.bridge.log'))
rclpy.init();n=rclpy.create_node('go2_short_navigation_test');buf=Buffer();tf_node=rclpy.create_node('go2_navigation_tf_receiver',enable_rosout=False);listener=RouteTransformListener(buf,tf_node,spin_thread=True)
latest={};preview={};commands=[];odom_samples=[];feedback=[];controls=[];bridge=None;handle=None;result_future=None;nav=None;log=None
report={'executed':False,'distance_m':args.distance,'max_speed_m_s':.20,'deadline_s':25.,'backend':args.backend,'bt':'ComputePathToPose + FollowPath; no recovery actions'}
def yaw(q):return math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))
def angle(a):return math.atan2(math.sin(a),math.cos(a))
def odom(m):
 latest.update(at=time.monotonic(),x=m.pose.pose.position.x,y=m.pose.pose.position.y,yaw=yaw(m.pose.pose.orientation))
 if len(odom_samples)<1600:odom_samples.append(dict(latest))
def status(m):
 d=json.loads(m.data)
 if d.get('armed') and d.get('source')=='nav2':
  preview.update(d,at=time.monotonic())
  if len(controls)<1400:controls.append(dict(preview))
def cmd(m):
 commands.append({'at':time.monotonic(),'x':float(m.linear.x),'y':float(m.linear.y),'w':float(m.angular.z)})
subs=[n.create_subscription(Odometry,'/odom',odom,10),n.create_subscription(String,'/go2/motion_test_preview',status,10),n.create_subscription(Twist,'/nav2/cmd_vel_preview',cmd,10)]
zero_pub=n.create_publisher(Twist,'/nav2/cmd_vel_preview',1)
planner=ActionClient(n,ComputePathToPose,'/compute_path_to_pose');nav=ActionClient(n,NavigateToPose,'/navigate_to_pose')
def spin(sec=.05):
 until=time.monotonic()+sec
 while time.monotonic()<until:rclpy.spin_once(n,timeout_sec=.02)
def zero():zero_pub.publish(Twist())
def map_pose():
 t=buf.lookup_transform('map','base_footprint',rclpy.time.Time());age=(n.get_clock().now().nanoseconds-t.header.stamp.sec*1000000000-t.header.stamp.nanosec)*1e-9
 if not -.10<=age<=.30:raise RuntimeError('Stale navigation map TF')
 return t,[t.transform.translation.x,t.transform.translation.y,yaw(t.transform.rotation)]
try:
 spin(5)
 if time.monotonic()-latest.get('at',0)>.3:raise RuntimeError('Odometry unavailable')
 t,start=map_pose();gx=start[0]+args.distance*math.cos(start[2]);gy=start[1]+args.distance*math.sin(start[2])
 if args.goal_from:
  previous=json.loads(Path(args.goal_from).read_text());fixed_start=previous['start_map'];fixed_goal=previous['goal_map']
  if len(fixed_start)!=3 or len(fixed_goal)!=3 or not all(math.isfinite(v)for v in fixed_start+fixed_goal):raise RuntimeError('Invalid original navigation goal')
  if abs(previous['distance_m']-args.distance)>1e-6 or abs(math.hypot(fixed_goal[0]-fixed_start[0],fixed_goal[1]-fixed_start[1])-args.distance)>.01:raise RuntimeError('Fixed goal does not match authorized distance')
  if math.hypot(start[0]-fixed_start[0],start[1]-fixed_start[1])>.06 or abs(angle(start[2]-fixed_start[2]))>.12:raise RuntimeError('Pose moved after original navigation planning; refusing to reset route')
  start=list(fixed_start);gx,gy,heading=fixed_goal;t.transform.rotation.x=0.;t.transform.rotation.y=0.;t.transform.rotation.z=math.sin(heading/2);t.transform.rotation.w=math.cos(heading/2);report['goal_preserved_from']=str(Path(args.goal_from).resolve())
 report.update(start_map=start,goal_map=[gx,gy,start[2]])
 if not planner.wait_for_server(timeout_sec=3) or not nav.wait_for_server(timeout_sec=3):raise RuntimeError('Nav2 action servers unavailable')
 plan=ComputePathToPose.Goal();plan.pose.header.frame_id='map';plan.pose.header.stamp=n.get_clock().now().to_msg();plan.pose.pose.position.x=float(gx);plan.pose.pose.position.y=float(gy);plan.pose.pose.orientation=t.transform.rotation;plan.planner_id='GridBased'
 f=planner.send_goal_async(plan);rclpy.spin_until_future_complete(n,f,timeout_sec=4)
 if not f.done() or not f.result().accepted:raise RuntimeError('Planning goal rejected')
 ph=f.result();f=ph.get_result_async();rclpy.spin_until_future_complete(n,f,timeout_sec=8)
 if not f.done():ph.cancel_goal_async();raise RuntimeError('Planning timeout')
 r=f.result();path=[[x.pose.position.x,x.pose.position.y] for x in r.result.path.poses]
 if r.status!=4 or len(path)<2:raise RuntimeError('Planner failed')
 endpoint=math.hypot(path[-1][0]-gx,path[-1][1]-gy);length=sum(math.hypot(b[0]-a[0],b[1]-a[1]) for a,b in zip(path,path[1:]))
 c,s=math.cos(start[2]),math.sin(start[2]);route=[[(x-start[0])*c+(y-start[1])*s,-(x-start[0])*s+(y-start[1])*c] for x,y in path]
 report['planning']={'status':r.status,'poses':len(path),'length_m':length,'endpoint_error_m':endpoint,'max_lateral_m':max(abs(lat)for along,lat in route),'path':path}
 if endpoint>.08 or not args.distance-.08<=length<=args.distance+.20 or any(abs(lat)>.12 or along<-.06 or along>args.distance+.06 for along,lat in route):raise RuntimeError('Planned path leaves confirmed straight corridor')
 # Arming follows successful planning; preflight zeros never request movement.
 env=os.environ.copy();env['CYCLONEDDS_URI']='/home/unitree/go2move/ros2/go2_bringup/config/dds/motion.xml'
 log=out.with_suffix('.bridge.log').open('w')
 bridge=subprocess.Popen([sys.executable,'-u',str(Path(__file__).with_name('low_speed_bridge.py')),'--armed','--source','nav2','--duration','45','--max-speed','.20','--backend',args.backend],env=env,stdout=log,stderr=subprocess.STDOUT)
 until=time.monotonic()+12
 while time.monotonic()<until:
  zero();spin()
  if preview.get('valid') and preview.get('worker',{}).get('worker_ready'):break
 else:raise RuntimeError('Standing/sensor/native writer preflight failed')
 _,now_map=map_pose()
 if math.hypot(now_map[0]-start[0],now_map[1]-start[1])>.06 or abs(angle(now_map[2]-start[2]))>.12:raise RuntimeError('Pose changed during planning/preflight')
 origin=dict(latest);cy,sy=math.cos(origin['yaw']),math.sin(origin['yaw']);report['origin_odom']=origin
 goal=NavigateToPose.Goal();goal.pose=plan.pose;goal.pose.header.stamp=n.get_clock().now().to_msg();goal.behavior_tree='/home/unitree/go2move/ros2/go2_bringup/config/preview_navigation.xml'
 def fb(m):
  d=m.feedback
  feedback.append({'at':time.monotonic(),'remaining':float(d.distance_remaining),'recoveries':int(d.number_of_recoveries),'xy':[d.current_pose.pose.position.x,d.current_pose.pose.position.y]})
 begin=time.monotonic();f=nav.send_goal_async(goal,feedback_callback=fb);rclpy.spin_until_future_complete(n,f,timeout_sec=4)
 if not f.done() or not f.result().accepted:raise RuntimeError('Navigation goal not accepted')
 handle=f.result();report['executed']=True;result_future=handle.get_result_async();last_progress=begin;best=0.;seen_motion=False;nonzero_since=None
 while not result_future.done():
  spin();now=time.monotonic()
  if bridge.poll() is not None:raise RuntimeError('Motion bridge exited')
  if now-begin>25:raise RuntimeError('Navigation exceeded 25 s deadline')
  if now-latest.get('at',0)>.3:raise RuntimeError('Stale odometry')
  dx,dy=latest['x']-origin['x'],latest['y']-origin['y'];along=dx*cy+dy*sy;lateral=-dx*sy+dy*cy
  _,map_now=map_pose();mx,my=map_now[0]-start[0],map_now[1]-start[1];mc,ms=math.cos(start[2]),math.sin(start[2]);map_along=mx*mc+my*ms;map_lateral=-mx*ms+my*mc
  if not -.08<=map_along<=args.distance+.08 or abs(map_lateral)>.12 or abs(angle(map_now[2]-start[2]))>.25:raise RuntimeError('Navigation map pose left original confirmed corridor/heading')
  if not -.08<=along<=args.distance+.08 or abs(lateral)>.12 or abs(angle(latest['yaw']-origin['yaw']))>.25:raise RuntimeError('Navigation left confirmed corridor/heading')
  if commands and now-commands[-1]['at']<.3 and any(abs(commands[-1][k])>1e-4 for k in ['x','y','w']):
   seen_motion=True;nonzero_since=nonzero_since or now
   if commands[-1]['x']<-.001 or abs(commands[-1]['y'])>.01 or abs(commands[-1]['w'])>.20:raise RuntimeError('Nav2 requested motion outside approved forward test')
   if now-preview.get('at',0)>.3 or not preview.get('valid') or preview.get('worker',{}).get('latched_stop'):raise RuntimeError('Sensor/posture/obstacle guard stopped navigation: '+preview.get('reason','unknown'))
  if preview.get('worker',{}).get('latched_stop'):raise RuntimeError('Native motion guard latched stop')
  if seen_motion and (not commands or now-commands[-1]['at']>.5):raise RuntimeError('Nav2 command stream timed out')
  if along>best+.02:best=along;last_progress=now
  if now-last_progress>6:raise RuntimeError('No odometry progress for 6 s; cancel navigation')
 r=result_future.result();report.update(status=r.status,seconds=time.monotonic()-begin)
 if r.status!=4:raise RuntimeError('NavigateToPose did not succeed; status='+str(r.status))
 report['completed']=True
except (Exception,KeyboardInterrupt) as e:report.update(completed=False,error=str(e));print('STOP:',e,flush=True)
finally:
 if handle and result_future and not result_future.done():
  f=handle.cancel_goal_async();rclpy.spin_until_future_complete(n,f,timeout_sec=2);report['cancel_response_received']=f.done()
 for _ in range(5):zero();spin(.05)
 if bridge:
  if bridge.poll() is None:bridge.send_signal(signal.SIGINT)
  try:bridge.wait(timeout=3)
  except subprocess.TimeoutExpired:bridge.terminate();bridge.wait(timeout=3)
 if log:log.close()
 spin(.4)
 try:
  _,end=map_pose();report['end_map']=end
  if 'goal_map' in report:
   report['endpoint_error_m']=math.hypot(end[0]-report['goal_map'][0],end[1]-report['goal_map'][1]);report['heading_error_rad']=abs(angle(end[2]-report['goal_map'][2]))
   if report.get('completed') and (report['endpoint_error_m']>.12 or report['heading_error_rad']>.20):report.update(completed=False,error='Final stopped pose exceeds goal tolerance')
 except Exception as e:report['end_pose_error']=str(e);report['completed']=False
 report.update(commands=commands,feedback=feedback,odom_samples=odom_samples,control_samples=controls)
 out.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ['commands','feedback','odom_samples','control_samples']},indent=2))
 planner.destroy();nav.destroy();listener.executor.shutdown();listener.dedicated_listener_thread.join(timeout=3);listener.unregister();tf_node.destroy_node();n.destroy_node();rclpy.shutdown()
raise SystemExit(0 if report.get('completed') else 1)
