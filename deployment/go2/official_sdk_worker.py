#!/usr/bin/env python3
"""Bounded IPC adapter around official Unitree SDK2 Move/StopMove.
Runs in the existing go2_sdk2_venv with matching CycloneDDS 0.10.2.
No posture, service switching, firmware or joystick-disable commands.
"""
import argparse,json,math,os,sys,time,threading,signal
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--armed',action='store_true');p.add_argument('--max-speed',type=float,default=.10);p.add_argument('--continuous-gait',action='store_true');p.add_argument('--rotation-only',action='store_true');p.add_argument('--autonomy',action='store_true');p.add_argument('--forward-only',action='store_true');p.add_argument('--radius',type=float,default=1.);p.add_argument('--duration',type=float,default=30.);args=p.parse_args()
if not args.armed or not .04<=args.max_speed<=.20:raise SystemExit('Official SDK writer requires --armed and bounded speed')
if args.forward_only and (not args.autonomy or args.duration>8):raise SystemExit('Forward-only calibration requires autonomy and at most 8 seconds')
rotation_guard=None
if args.autonomy:
 if args.rotation_only or args.continuous_gait or args.max_speed>.10:raise SystemExit('Autonomy forbids gait changes, rotation-only mode, and speed above .10 m/s')
 from autonomy_guard import AutonomyGuard
 rotation_guard=AutonomyGuard(args.radius,args.duration)
if args.rotation_only:
 if args.continuous_gait:raise SystemExit('Rotation forbids gait or posture changes')
 policy=json.loads(Path(__file__).with_name('config').joinpath('scan_policy.json').read_text())
 if policy.get('enabled') is not True or policy.get('physical_validated') is not True:raise SystemExit('Rotation policy is held')
 import fcntl
 lock_path=Path(__file__).with_name('runtime')/'scan'/'worker.lock';lock_path.parent.mkdir(parents=True,exist_ok=True)
 rotation_lock=lock_path.open('a');fcntl.flock(rotation_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 from rotation_guard import RotationGuard
 rotation_guard=RotationGuard()
if rotation_guard is None:
 from motion_health_guard import MotionHealthGuard
 rotation_guard=MotionHealthGuard(args.duration)
# All SDK writers share one lock, including manual tests and person rotation.
import fcntl
motion_lock=open('/tmp/go2-motion-writer.lock','a')
fcntl.flock(motion_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
from low_speed_bridge import bounded
import low_speed_bridge
low_speed_bridge.LIMIT_V=args.max_speed
import unitree_sdk2py.core.channel as channel
from unitree_sdk2py.idl.unitree_go.msg.dds_ import SportModeState_,LowState_
from unitree_sdk2py.go2.sport.sport_client import SportClient
channel.ChannelConfigAutoDetermine=Path('/home/unitree/go2move/deployment/go2/cyclonedds_go2.xml').read_text()
channel.ChannelFactoryInitialize(0)
state={};low={};command={};lock=threading.Lock();eof=threading.Event();last=[0.,0.,0.];moved=False;tripped=False;was_active=False;response={};gait_on=False
client=SportClient(False);client.SetTimeout(.2);client.Init()
def sport(m):state.update(at=time.monotonic(),height=float(m.body_height),rpy=list(map(float,m.imu_state.rpy)),mode=int(m.mode),gait=int(m.gait_type),error=int(m.error_code),velocity=list(map(float,m.velocity)),position=list(map(float,m.position)),yaw_speed=float(getattr(m,'yaw_speed',0.)))
def low_state(m):low.update(at=time.monotonic(),foot_force=list(map(int,m.foot_force)),joint_q=[float(x.q) for x in m.motor_state[:12]],joint_dq=[float(x.dq) for x in m.motor_state[:12]],temperatures=[int(x.temperature) for x in m.motor_state[:12]],motor_lost=[int(x.lost) for x in m.motor_state[:12]],motor_faults=[int(x.reserve[0]) for x in m.motor_state[:12]],soc=int(m.bms_state.soc),power_v=float(m.power_v),wireless_remote=list(map(int,m.wireless_remote)),bit_flag=getattr(m,'bit_flag',None),tick=getattr(m,'tick',None))
sub=channel.ChannelSubscriber('rt/lf/sportmodestate',SportModeState_);sub.Init(sport,4)
low_sub=channel.ChannelSubscriber('rt/lf/lowstate',LowState_);low_sub.Init(low_state,4)
def read():
 try:
  for line in sys.stdin:
   d=json.loads(line);v=bounded(d['velocity'])
   if args.rotation_only:
    raw=d['velocity']
    if len(raw)!=3 or not all(math.isfinite(float(x)) for x in raw) or float(raw[0])!=0 or float(raw[1])!=0 or not 0<=float(raw[2])<=.15:raise ValueError('rotation-only command rejected')
    v=[0.,0.,float(raw[2])]
   if args.autonomy:
    raw=d['velocity']
    if len(raw)!=3 or not all(math.isfinite(float(x)) for x in raw) or not 0<=float(raw[0])<=args.max_speed or float(raw[1])!=0 or abs(float(raw[2]))>.15 or (float(raw[0])!=0 and float(raw[2])!=0) or (args.forward_only and float(raw[2])!=0):raise ValueError('Autonomy command rejected')
    v=list(map(float,raw))
   with lock:command.update(at=time.monotonic(),velocity=v,valid=bool(d.get('valid',False)))
 except Exception:eof.set()
 finally:eof.set()
threading.Thread(target=read,daemon=True).start();signal.signal(signal.SIGTERM,lambda *_:eof.set())
autonomy_ready=False;autonomy_startup_deadline=time.monotonic()+5.;autonomy_sync_at=None;autonomy_synced=False;autonomy_preparation={};autonomy_stationary_since=None;motion_ready=False
def gait(flag):
 code,_=client._CallBase(1019,json.dumps({'data':int(flag)}),0,0)
 return {'api':1019,'code':int(code),'source':'official SDK RPC'}
def stop():
 # Try both stop paths independently: one RPC exception must not suppress the other.
 errors=[];zero_code=None;code=None
 try:zero_code=int(client.Move(0.,0.,0.))
 except Exception as exc:errors.append('zero_move: '+type(exc).__name__)
 try:code=int(client.StopMove())
 except Exception as exc:errors.append('stop_move: '+type(exc).__name__)
 return {'api':1003,'code':code,'zero_move_code':zero_code,'errors':errors,'source':'official SDK StopMove'}
try:
 while not eof.is_set():
  begin=time.monotonic()
  with lock:c=command.copy()
  good=(not tripped and begin-state.get('at',0)<.25 and begin-low.get('at',0)<.25 and state.get('height',0)>.20 and len(state.get('rpy',[]))==3 and all(math.isfinite(v) for v in state['rpy']) and max(abs(state['rpy'][0]),abs(state['rpy'][1]))<.35 and begin-c.get('at',0)<.25 and c.get('valid',False))
  guard_reason=None
  settling=args.autonomy and autonomy_sync_at is not None and not autonomy_synced
  if rotation_guard:
   guard_reason=rotation_guard.check(state,low,begin,settling=settling) if args.autonomy else rotation_guard.check(state,low,begin)
   if guard_reason:good=False
   if moved and not good:tripped=True
  if not args.autonomy and not args.rotation_only:
   if motion_ready and not good:tripped=True
   if good:motion_ready=True
  if args.autonomy:
   healthy_input=good
   if autonomy_sync_at is not None and not healthy_input:tripped=True
   if good and autonomy_sync_at is None:
    if any(abs(v)>1e-8 for v in c.get('velocity',[0,0,0])):
     good=False;guard_reason='Waiting for a zero command before stop synchronization'
    else:
     before=dict(state);response=stop()
     autonomy_preparation={'before':before,'stop_response':response,'started_at':begin}
     if response.get('code')!=0 or response.get('zero_move_code')!=0 or response.get('errors'):
      tripped=True;good=False;guard_reason='Initial stop synchronization failed'
     else:autonomy_sync_at=begin
   if autonomy_sync_at is not None and not autonomy_synced and not tripped:
    before=autonomy_preparation['before']
    displacement=math.sqrt(sum((a-b)**2 for a,b in zip(state['position'],before['position'])))
    if displacement>.08 or begin-autonomy_sync_at>3.:
     tripped=True;good=False;guard_reason='Stop preparation exceeded 0.08 m displacement or 3 s settling deadline'
    elif math.hypot(*state['velocity'][:2])<=.025 and abs(state['yaw_speed'])<=.04:
     autonomy_stationary_since=begin if autonomy_stationary_since is None else autonomy_stationary_since
     if begin-autonomy_sync_at>=.75 and begin-autonomy_stationary_since>=.5:autonomy_synced=True
    else:autonomy_stationary_since=None
   if autonomy_sync_at is None or not autonomy_synced:good=False
   if good:autonomy_ready=True
   elif autonomy_ready or begin>=autonomy_startup_deadline:
    # Once ready, every rejection consumes the one-shot worker, even at rest.
    tripped=True
  if args.continuous_gait and good and not gait_on:
   gait_on=True;response=gait(True)
   if response['code']!=0:good=False;tripped=True
  active=good and any(abs(v)>1e-5 for v in c.get('velocity',[0,0,0]))
  if active:
   if rotation_guard:rotation_guard.mark_started(begin)
   if args.autonomy and (last[0]*c['velocity'][2]!=0 or last[2]*c['velocity'][0]!=0):
    response=stop();last=[0.,0.,0.]
   last=[a+max(-step,min(step,b-a)) for a,b,step in zip(last,c['velocity'],[.005,.005,.01])]
   moved=True  # A transport exception does not prove the motion request was not delivered.
   code=client.Move(*last);response={'api':1008,'code':int(code),'source':'official SDK Move; no-reply transport only'}
   if code!=0:response=stop();last=[0.,0.,0.];tripped=True;active=False
  elif was_active:
   response=stop();last=[0.,0.,0.]
   if not good:tripped=True
  if gait_on and (not good or tripped):response=stop();gait(False);tripped=True
  was_active=active
  print(json.dumps({'worker_ready':good and not tripped,'latched_stop':tripped,'commanded':last,'body_height':state.get('height'),'last_response':response,'native_state':state,'native_low':low,'backend':'official_sdk2','guard_reason':guard_reason,'rotation':rotation_guard.progress() if rotation_guard else None,'autonomy_preparation':dict(autonomy_preparation,synchronized=autonomy_synced) if args.autonomy else None}),flush=True)
  eof.wait(max(0.,.05-(time.monotonic()-begin)))
except KeyboardInterrupt:pass
finally:
 if moved or gait_on or (args.autonomy and autonomy_preparation):
  replies=[]
  for _ in range(3):
   replies.append(stop())
   if gait_on:replies.append(gait(False))
   time.sleep(.05)
  print(json.dumps({'shutdown':True,'stop_responses':replies,'native_state':dict(state),'native_low':dict(low)}),flush=True)
 low_sub.Close();sub.Close()
