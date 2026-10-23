#!/usr/bin/env python3
"""Minimal XT16 test adapter: Unitree official Move(1008)/StopMove(1003).
Transport split reuses the existing read-only state bridge approach.
References: official unitree_ros2 ros2_sport_client.cpp and h-naderi sport_ctrl.py.
Default preview creates no robot command publisher. Optional supervised ContinuousGait only; no firmware API.
"""
import argparse,json,math,os,sys,time,threading,subprocess,signal
LIMIT_V=.10;LIMIT_W=.20;TIMEOUT=.25

def bounded(v):
 if len(v)!=3 or not all(math.isfinite(float(x)) for x in v):raise ValueError('invalid command')
 x,y,w=map(float,v);norm=math.hypot(x,y)
 if norm>LIMIT_V:x*=LIMIT_V/norm;y*=LIMIT_V/norm
 return [x,y,max(-LIMIT_W,min(LIMIT_W,w))]

def worker(continuous_gait=False,duration=45.):
 # Only --armed parent invokes this worker. Existing UDP interface config is reused.
 from motion_health_guard import MotionHealthGuard
 health_guard=MotionHealthGuard(duration)
 import fcntl
 motion_lock=open('/tmp/go2-motion-writer.lock','a')
 fcntl.flock(motion_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 import rclpy
 from unitree_api.msg import Request,Response
 from unitree_go.msg import SportModeState,LowState
 from rclpy.qos import QoSProfile,ReliabilityPolicy
 rclpy.init();n=rclpy.create_node('go2_low_speed_test_writer',enable_rosout=False)
 pub=n.create_publisher(Request,'/api/sport/request',10)
 state={};command={};responses={};own_responses={};pending=set();low={};gait_on=False;gait_request=None;gait_sent_at=None;lock=threading.Lock();eof=threading.Event();moved=False;tripped=False;was_active=False;last=[0.,0.,0.];motion_ready=False
 def received(m):
  state.update(at=time.monotonic(),height=float(m.body_height),rpy=list(map(float,m.imu_state.rpy)),mode=int(m.mode),gait=int(m.gait_type),error=int(m.error_code),velocity=list(map(float,m.velocity)),position=list(map(float,m.position)),yaw_speed=float(getattr(m,'yaw_speed',0.)))
 def response(m):
  rid=int(m.header.identity.id);d={'api':int(m.header.identity.api_id),'id':rid,'code':int(m.header.status.code),'data':m.data}
  if rid in pending:responses.update(d);own_responses[rid]=d
 reply_sub=n.create_subscription(Response,'/api/sport/response',response,10)
 sub=n.create_subscription(SportModeState,'/lf/sportmodestate',received,QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE))
 def received_low(m):low.update(at=time.monotonic(),foot_force=list(map(int,m.foot_force)),joint_q=[float(x.q) for x in m.motor_state[:12]],joint_dq=[float(x.dq) for x in m.motor_state[:12]],temperatures=[int(x.temperature) for x in m.motor_state[:12]],motor_lost=[int(x.lost) for x in m.motor_state[:12]],motor_faults=[int(x.reserve[0]) for x in m.motor_state[:12]],soc=int(m.bms_state.soc),power_v=float(m.power_v),wireless_remote=list(map(int,m.wireless_remote)),bit_flag=getattr(m,'bit_flag',None),tick=getattr(m,'tick',None))
 low_sub=n.create_subscription(LowState,'/lf/lowstate',received_low,QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE))
 def read():
  try:
   for line in sys.stdin:
    d=json.loads(line);v=bounded(d['velocity'])
    with lock:command.update(at=time.monotonic(),velocity=v,valid=bool(d.get('valid',False)))
  except Exception:eof.set()
  finally:eof.set()
 threading.Thread(target=read,daemon=True).start()
 def send(api,v=None,data=None):
  req=Request();req.header.identity.id=time.monotonic_ns();req.header.identity.api_id=api;req.header.policy.noreply=(api==1008)
  req.parameter=json.dumps({'data':data}) if data is not None else json.dumps(dict(zip(['x','y','z'],v))) if v is not None else '{}'
  if not req.header.policy.noreply:pending.add(int(req.header.identity.id))
  pub.publish(req);return int(req.header.identity.id)
 def tick():
  nonlocal moved,tripped,was_active,last,gait_on,gait_request,gait_sent_at,motion_ready
  now=time.monotonic()
  with lock:c=command.copy()
  good=(not tripped and now-state.get('at',0)<TIMEOUT and now-low.get('at',0)<TIMEOUT and state.get('height',0)>.20 and len(state.get('rpy',[]))==3 and all(math.isfinite(float(v)) for v in state.get('rpy',[])) and max(abs(state['rpy'][0]),abs(state['rpy'][1]))<.35 and now-c.get('at',0)<TIMEOUT and c.get('valid',False))
  guard_reason=health_guard.check(state,low,now)
  if guard_reason:good=False
  if motion_ready and not good:tripped=True
  if good:motion_ready=True
  base_good=good
  if continuous_gait:
   # Optional bounded MCF preparation; never changes the motion controller.
   if good and gait_request is None:
    gait_request=send(1019,data=1);gait_sent_at=now;gait_on=True
   reply=own_responses.get(gait_request,{})
   if gait_request is not None and ((reply and reply.get('code')!=0) or (not reply and now-gait_sent_at>2.)):
    tripped=True
   if gait_on and (not base_good or tripped):
    if not was_active:send(1008,[0.,0.,0.]);send(1003);send(1019,data=0)
    tripped=True
   good=good and reply.get('code')==0 and not tripped
  desired=c.get('velocity',[0,0,0]) if good else [0,0,0]
  active=good and any(abs(v)>1e-5 for v in desired)
  if active:
   health_guard.mark_started(now)
   # 0.1 m/s2 linear and 0.2 rad/s2 angular ramp at 20 Hz.
   steps=[.005,.005,.01];last=[a+max(-s,min(s,b-a)) for a,b,s in zip(last,desired,steps)]
   moved=True  # An exception does not prove the request was not delivered.
   send(1008,last)
  elif was_active:
   send(1008,[0.,0.,0.]);send(1003);last=[0.,0.,0.]
   if not good:
    tripped=True
    if gait_on:send(1019,data=0)
  was_active=active
  print(json.dumps({'worker_ready':good and not tripped,'latched_stop':tripped,'commanded':last,'guard_reason':guard_reason,'body_height':state.get('height'),'last_response':responses,'request_matches':pub.get_subscription_count(),'native_state':state,'native_low':low,'continuous_gait_requested':continuous_gait,'continuous_gait_response':own_responses.get(gait_request,{})}),flush=True)
 timer=n.create_timer(.05,tick)
 signal.signal(signal.SIGTERM,lambda *_:eof.set())
 try:
  while not eof.is_set():rclpy.spin_once(n,timeout_sec=.05)
 except KeyboardInterrupt:pass
 finally:
  # Draining stop replies must not run the normal motion timer again.
  timer.cancel()
  if moved or gait_on:
   for _ in range(3):
    send(1008,[0.,0.,0.]);send(1003)
    if gait_on:send(1019,data=0)
    rclpy.spin_once(n,timeout_sec=.05)
   until=time.monotonic()+.3
   while time.monotonic()<until:rclpy.spin_once(n,timeout_sec=.03)
   print(json.dumps({'shutdown':True,'stop_responses':list(own_responses.values())[-12:]}),flush=True)
  n.destroy_node();rclpy.shutdown()

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--armed',action='store_true');parser.add_argument('--source',choices=['mapping','nav2'],default='mapping');parser.add_argument('--worker',action='store_true');parser.add_argument('--continuous-gait',action='store_true');parser.add_argument('--max-speed',type=float,default=.10);parser.add_argument('--backend',choices=['sdk2','ros2'],default='ros2');parser.add_argument('--duration',type=float,default=45.);args=parser.parse_args()
 if not .04<=args.max_speed<=.20:raise SystemExit('Speed outside bounded test range')
 global LIMIT_V;LIMIT_V=args.max_speed
 if args.worker:
  if not args.armed:raise SystemExit('Internal writer requires --armed')
  worker(args.continuous_gait,args.duration);return
 import rclpy
 from geometry_msgs.msg import Twist
 from sensor_msgs.msg import LaserScan,PointCloud2
 from nav_msgs.msg import Odometry
 from rclpy.qos import qos_profile_sensor_data
 from std_msgs.msg import String
 rclpy.init();n=rclpy.create_node('go2_low_speed_test_adapter');latest={};motion={};process=None;fault={};worker_state={}
 preview=n.create_publisher(String,'/go2/motion_test_preview',10)
 def mark(key,m):latest[key]=(time.monotonic(),m)
 def cmd(m):
  try:motion.update(at=time.monotonic(),velocity=bounded([m.linear.x,m.linear.y,m.angular.z]))
  except Exception:motion.update(at=0,velocity=[0.,0.,0.])
 topic='/go2/mapping_cmd_vel' if args.source=='mapping' else '/nav2/cmd_vel_preview'
 subs=[n.create_subscription(Twist,topic,cmd,1),n.create_subscription(LaserScan,'/scan',lambda m:mark('scan',m),qos_profile_sensor_data),n.create_subscription(Odometry,'/odom',lambda m:mark('odom',m),10),n.create_subscription(PointCloud2,'/lidar_points',lambda m:mark('cloud',m),qos_profile_sensor_data)]
 if args.armed:
  env=os.environ.copy();env['CYCLONEDDS_URI']='/home/unitree/go2move/deployment/go2/cyclonedds_go2.xml'
  writer=[sys.executable,'-u',__file__,'--worker','--armed','--max-speed',str(args.max_speed),'--duration',str(min(60.,max(1.,args.duration)))]
  if args.backend=='sdk2':
   env['CYCLONEDDS_HOME']='/home/unitree/cyclonedds_ws/install/cyclonedds';env['LD_LIBRARY_PATH']=env['CYCLONEDDS_HOME']+'/lib:'+env.get('LD_LIBRARY_PATH','')
   writer=['/home/unitree/go2_sdk2_venv/bin/python','-u',str(__import__('pathlib').Path(__file__).with_name('official_sdk_worker.py')),'--armed','--max-speed',str(args.max_speed),'--duration',str(min(60.,max(1.,args.duration)))]
  process=subprocess.Popen(writer+(['--continuous-gait'] if args.continuous_gait else []),env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,bufsize=1)
  def worker_output():
   for line in process.stdout:
    try:worker_state.update(json.loads(line),at=time.monotonic())
    except Exception:pass
  threading.Thread(target=worker_output,daemon=True).start()
 def tick():
  now=time.monotonic();v=motion.get('velocity',[0.,0.,0.]);ages={k:now-latest.get(k,(0,None))[0] for k in ['scan','odom','cloud']};command_age=now-motion.get('at',0);stale=(["command"] if command_age>=TIMEOUT else [])+[k for k,a in ages.items() if a>=.3];valid=not stale
  reason='ok' if valid else 'stale '+', '.join(stale);obstacle=None
  if valid and math.hypot(v[0],v[1])>1e-5:
   scan=latest['scan'][1];direction=math.atan2(v[1],v[0])
   for i,d in enumerate(scan.ranges):
    angle=scan.angle_min+i*scan.angle_increment-direction;angle=math.atan2(math.sin(angle),math.cos(angle))
    if abs(angle)<.52 and math.isfinite(d) and scan.range_min<=d<.60:valid=False;obstacle={'range_m':float(d),'angle_base_rad':float(scan.angle_min+i*scan.angle_increment),'x_base_m':float(d*math.cos(scan.angle_min+i*scan.angle_increment)),'y_base_m':float(d*math.sin(scan.angle_min+i*scan.angle_increment)),'motion_direction_rad':direction};reason='obstacle inside 0.60 m motion-direction margin';break
  out={'armed':args.armed,'source':args.source,'max_speed':LIMIT_V,'valid':valid,'reason':reason,'velocity':v if valid else [0.,0.,0.], 'worker':worker_state,'obstacle':obstacle,'requested_velocity':v,'command_age_s':command_age,'sensor_age_s':ages}
  if 'cloud' in latest:
   cloud_at,cloud=latest['cloud'];out['cloud_metadata']={'receive_monotonic_s':cloud_at,'header_stamp_s':cloud.header.stamp.sec+cloud.header.stamp.nanosec/1e9,'frame':cloud.header.frame_id,'points':cloud.width*cloud.height}
  if process:
   if process.poll() is not None:fault['reason']='writer exited';out['valid']=False
   else:
    try:process.stdin.write(json.dumps(out)+'\n');process.stdin.flush()
    except Exception:fault['reason']='writer pipe failed'
  msg=String();msg.data=json.dumps(out);preview.publish(msg)
 timer=n.create_timer(.05,tick)
 deadline=time.monotonic()+min(60.,max(1.,args.duration))
 n.get_logger().info('Motion adapter source=%s armed=%s; limits %.2f m/s, 0.20 rad/s; continuous_gait=%s'%(args.source,args.armed,args.max_speed,args.continuous_gait))
 try:
  while time.monotonic()<deadline:rclpy.spin_once(n,timeout_sec=.05)
 except KeyboardInterrupt:pass
 finally:
  if process:
   process.stdin.close()
   try:process.wait(timeout=2)
   except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=2)
  n.destroy_node();rclpy.shutdown()
if __name__=='__main__':main()
