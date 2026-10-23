#!/usr/bin/env python3
"""Bounded supervised out-and-back test. Requires --execute after onsite confirmation."""
import argparse,time,math,json,sys,pathlib,subprocess,os
import rclpy
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Twist
from std_msgs.msg import String
from tf2_ros import Buffer
from route_tf import RouteTransformListener
p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--distance',type=float,default=.8);p.add_argument('--pulse',action='store_true');p.add_argument('--return-only',action='store_true');p.add_argument('--continuous-gait',action='store_true');p.add_argument('--max-speed',type=float,default=.10);p.add_argument('--pulse-seconds',type=float,default=2.);p.add_argument('--pulse-distance-limit',type=float,default=.20);p.add_argument('--yaw-gain',type=float,default=1.5);p.add_argument('--backend',choices=['ros2','sdk2'],default='ros2');p.add_argument('--origin-from');p.add_argument('--route-map-origin');p.add_argument('--output',required=True);args=p.parse_args()
if not args.execute:raise SystemExit('Refusing motion without --execute')
if not 0<args.distance<=.8:raise SystemExit('Mapping distance capped at 0.8 m within confirmed 1 m route')
if not .04<=args.max_speed<=.20 or not .5<=args.pulse_seconds<=3. or not 0<args.pulse_distance_limit<=.20:raise SystemExit('Pulse/speed test limits exceeded')
if not math.isfinite(args.yaw_gain) or not 0.<=args.yaw_gain<=1.5:raise SystemExit('Yaw gain must be 0..1.5; all heading/deviation guards remain active')
if args.return_only and (args.pulse or not args.origin_from):raise SystemExit('Return-only requires a previous origin report and cannot be a pulse')
from go2_platform_write import backup
backup(args.output);backup(str(pathlib.Path(args.output).with_suffix('.bridge.log')))
rclpy.init();n=rclpy.create_node('go2_minimal_mapping_test');latest={};preview={};samples=[];control_samples=[];map_samples=[]
original_report=json.loads(pathlib.Path(args.origin_from).read_text()) if args.origin_from else None
use_map=bool(args.route_map_origin or not args.pulse)
buffer=Buffer() if use_map else None
tf_node=rclpy.create_node('go2_mapping_tf_receiver',enable_rosout=False) if buffer else None
listener=RouteTransformListener(buffer,tf_node,spin_thread=True) if buffer else None
map_origin=original_report.get('map_origin') if use_map and original_report else None
if args.route_map_origin:
 d=json.loads(pathlib.Path(args.route_map_origin).read_text())['tf']['map->base_footprint']
 x,y,z,w=d['xyzw'];norm=math.sqrt(x*x+y*y+z*z+w*w)
 if not all(math.isfinite(v) for v in d['xyz']+d['xyzw']) or norm<.5:raise SystemExit('Invalid original map route anchor')
 x,y,z,w=[v/norm for v in [x,y,z,w]]
 map_origin={'x':d['xyz'][0],'y':d['xyz'][1],'yaw':math.atan2(2*(w*z+x*y),1-2*(y*y+z*z)),'source_file':str(pathlib.Path(args.route_map_origin).resolve())}
if use_map and original_report and not map_origin:raise SystemExit('Resuming requires the original map anchor; supply --route-map-origin from the original read-only preflight')
if map_origin and not all(math.isfinite(map_origin[k])for k in ['x','y','yaw']):raise SystemExit('Invalid preserved map origin')
def odom(m):
 q=m.pose.pose.orientation;yaw=math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))
 latest.update(at=time.monotonic(),x=m.pose.pose.position.x,y=m.pose.pose.position.y,yaw=yaw)
 if len(samples)<3000:samples.append(dict(latest))
def status(m):
 d=json.loads(m.data)
 if d.get('armed') and d.get('source')=='mapping':
  preview.update(d,at=time.monotonic())
  if len(control_samples)<2000:control_samples.append(dict(preview))
subs=[n.create_subscription(Odometry,'/odom',odom,10),n.create_subscription(String,'/go2/motion_test_preview',status,10)]
pub=n.create_publisher(Twist,'/go2/mapping_cmd_vel',1)
bridge=None;report={'route_distance_limit':args.distance,'phases':[],'executed':True,'max_speed_m_s':args.max_speed,'pulse_duration_limit_s':args.pulse_seconds,'pulse_displacement_limit_m':args.pulse_distance_limit,'pulse_stop_margin_m':min(.025,args.pulse_distance_limit*.2),'yaw_gain':args.yaw_gain,'backend':args.backend,'return_only':args.return_only,'heading_reference':'map TF' if use_map else 'raw odom','map_origin':map_origin}
def send(v=0,w=0):
 m=Twist();m.linear.x=float(v);m.angular.z=float(w);pub.publish(m)
def spin(seconds=.05):
 end=time.monotonic()+seconds
 while time.monotonic()<end:rclpy.spin_once(n,timeout_sec=.02)
def zero_hold(seconds):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  send();spin(min(.05,max(.001,end-time.monotonic())))
def angle(a):return math.atan2(math.sin(a),math.cos(a))
def route_heading():
 if not map_origin:return latest['yaw'],origin['yaw']
 t=buffer.lookup_transform('map','base_footprint',rclpy.time.Time());q=t.transform.rotation
 age=(n.get_clock().now().nanoseconds-(t.header.stamp.sec*1000000000+t.header.stamp.nanosec))*1e-9
 if not -.10<=age<=.30:raise RuntimeError('Stale SLAM route transform; age='+str(round(age,4))+' s')
 h=math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z));dx=t.transform.translation.x-map_origin['x'];dy=t.transform.translation.y-map_origin['y'];c,s=math.cos(map_origin['yaw']),math.sin(map_origin['yaw']);along=dx*c+dy*s;lateral=-dx*s+dy*c
 record={'at':time.monotonic(),'x':t.transform.translation.x,'y':t.transform.translation.y,'yaw':h,'along':along,'lateral':lateral,'stamp_age_s':age}
 if len(map_samples)<2000:map_samples.append(record)
 if not all(math.isfinite(v) for v in [h,along,lateral]):raise RuntimeError('Non-finite SLAM route pose')
 if abs(lateral)>.12 or not -.10<=along<=args.distance+.08:raise RuntimeError('SLAM pose left original confirmed route')
 return h,map_origin['yaw']
try:
 spin(4)
 if time.monotonic()-latest.get('at',0)>.3:raise RuntimeError('Odometry unavailable')
 bridge_env=os.environ.copy();bridge_env['CYCLONEDDS_URI']='/home/unitree/go2move/ros2/go2_bringup/config/dds/motion.xml'
 bridge=subprocess.Popen([sys.executable,'-u',str(pathlib.Path(__file__).with_name('low_speed_bridge.py')),'--armed','--source','mapping','--duration','55','--max-speed',str(args.max_speed),'--backend',args.backend]+(['--continuous-gait'] if args.continuous_gait else []),env=bridge_env,stdout=open(str(pathlib.Path(args.output).with_suffix('.bridge.log')),'w'),stderr=subprocess.STDOUT)
 start=time.monotonic()
 while time.monotonic()-start<12:
  send();spin()
  if preview.get('valid') and preview.get('worker',{}).get('worker_ready'):break
 else:raise RuntimeError('Writer/sensor/posture preflight did not become ready: '+json.dumps(preview))
 origin=json.loads(pathlib.Path(args.origin_from).read_text())['origin'] if args.origin_from else dict(latest)
 if not all(math.isfinite(origin[k]) for k in ['x','y','yaw']):raise RuntimeError('Invalid original route origin')
 report['origin']=origin;report['resumed_origin_from']=args.origin_from
 if use_map and map_origin is None:
  t=buffer.lookup_transform('map','base_footprint',rclpy.time.Time());q=t.transform.rotation
  map_origin={'x':t.transform.translation.x,'y':t.transform.translation.y,'yaw':math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z)),'captured_at_monotonic_s':time.monotonic()}
 report['map_origin']=map_origin;route_origin=map_origin or origin;cy,sy=math.cos(route_origin['yaw']),math.sin(route_origin['yaw'])
 phases=[('return',0.,-args.max_speed)] if args.return_only else ([('forward',args.distance,args.max_speed)] if args.pulse else [('forward',args.distance,args.max_speed),('return',0.,-args.max_speed)])
 for phase,target,speed in phases:
  route_heading();begin=time.monotonic();best=0.;progress_at=begin;phase_origin=dict(map_samples[-1] if map_origin else latest);previous_pose=dict(phase_origin)
  while True:
   now=time.monotonic();current_heading,original_heading=route_heading();tracking=map_samples[-1] if map_origin else latest
   dx=tracking['x']-route_origin['x'];dy=tracking['y']-route_origin['y'];along=dx*cy+dy*sy;lateral=-dx*sy+dy*cy
   if math.hypot(tracking['x']-previous_pose['x'],tracking['y']-previous_pose['y'])>.15 or abs(angle(tracking['yaw']-previous_pose['yaw']))>.15:raise RuntimeError('Route pose jumped; stop before accepting localization correction')
   previous_pose=dict(tracking)
   if now-latest['at']>.3:raise RuntimeError('Stale odometry')
   if now-preview.get('at',0)>.3 or not preview.get('valid') or preview.get('worker',{}).get('latched_stop'):raise RuntimeError('Motion guard stopped: '+json.dumps({'reason':preview.get('reason'),'command_age_s':preview.get('command_age_s'),'sensor_age_s':preview.get('sensor_age_s'),'worker_latched':preview.get('worker',{}).get('latched_stop')}))
   if abs(lateral)>.12 or abs(angle(current_heading-original_heading))>.25:raise RuntimeError('Lateral/heading deviation exceeded')
   if along < -.10 or along > args.distance+.08:raise RuntimeError('Confirmed route distance exceeded')
   if args.pulse and (now-begin>args.pulse_seconds or math.hypot(dx,dy)>=args.pulse_distance_limit-min(.025,args.pulse_distance_limit*.2)):break
   error=target-along
   if (phase=='forward' and error<.03) or (phase=='return' and along<.03):break
   progress=math.hypot(tracking['x']-phase_origin['x'],tracking['y']-phase_origin['y'])
   if progress>best+.02:best=progress;progress_at=now
   if now-progress_at>4.:raise RuntimeError('Odometry did not advance 2 cm in 4 seconds')
   if now-begin>22.:raise RuntimeError('Phase deadline exceeded')
   v=speed if not args.pulse else math.copysign(min(abs(speed),max(.04,abs(error)*.5)),speed)
   send(v,max(-.15,min(.15,-angle(current_heading-original_heading)*args.yaw_gain)));spin()
  zero_hold(1.);report['phases'].append({'name':phase,'seconds':time.monotonic()-begin,'end':dict(latest),'distance_from_start':math.hypot(dx,dy)})
 report['completed']=True;report['pulse_only']=args.pulse;route_heading();tracking=map_samples[-1] if map_origin else latest;report['end_displacement_m']=math.hypot(tracking['x']-route_origin['x'],tracking['y']-route_origin['y']);
 if not args.pulse:report['return_error_m']=report['end_displacement_m']
except (Exception, KeyboardInterrupt) as e:report['completed']=False;report['error']=str(e);print('STOP:',e,flush=True)
finally:
 for _ in range(5):send();spin(.05)
 if bridge:
  if bridge.poll() is None:bridge.send_signal(2)
  try:bridge.wait(timeout=3)
  except subprocess.TimeoutExpired:bridge.terminate();bridge.wait(timeout=3)
 if 'origin' in report and latest:
  spin(.25);report['end']=dict(latest);report['raw_odom_end_displacement_m']=math.hypot(latest['x']-report['origin']['x'],latest['y']-report['origin']['y'])
  if map_origin:
   try:
    route_heading();report['end_map']=dict(map_samples[-1]);report['end_displacement_m']=math.hypot(report['end_map']['x']-map_origin['x'],report['end_map']['y']-map_origin['y'])
    if report.get('completed')and not args.pulse:report['return_error_m']=report['end_displacement_m']
   except Exception as e:report['completed']=False;report['end_pose_error']=str(e)
  else:report['end_displacement_m']=report['raw_odom_end_displacement_m']
 report['samples']=samples;report['control_samples']=control_samples;report['map_samples']=map_samples;pathlib.Path(args.output).write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k not in ['samples','control_samples','map_samples']},indent=2));
 if listener:
  listener.executor.shutdown();listener.dedicated_listener_thread.join(timeout=3);listener.unregister();tf_node.destroy_node()
 n.destroy_node();rclpy.shutdown()
raise SystemExit(0 if report.get('completed') else 1)
