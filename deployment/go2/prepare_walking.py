#!/usr/bin/env python3
"""Read Go2 motion state; optional single BalanceStand from official API.
No nonzero Move, no controller/service switch, no joystick disable.
MCF StandUp -> BalanceStand preparation follows legion1581 MCF example.
"""
import argparse,json,math,time,sys
from pathlib import Path
import rclpy
from unitree_api.msg import Request,Response
from unitree_go.msg import SportModeState,LowState,WirelessController
from rclpy.qos import QoSProfile,ReliabilityPolicy
p=argparse.ArgumentParser();p.add_argument('--balance-stand',action='store_true');p.add_argument('--observe-duration',type=float,default=0.);p.add_argument('--output',required=True);args=p.parse_args()
from go2_platform_write import backup
backup(args.output)
rclpy.init();n=rclpy.create_node('go2_walking_preparation',enable_rosout=False)
state={};responses={};samples=[];requests=[];low_samples=[];observed_requests=[];wireless={};low={};report={'balance_requested':args.balance_stand,'queries':[]}
q=QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE)
def sport(m):
 d={'at':time.monotonic(),'mode':int(m.mode),'progress':float(m.progress),'gait_type':int(m.gait_type),'error_code':int(m.error_code),'body_height':float(m.body_height),'position':list(map(float,m.position)),'velocity':list(map(float,m.velocity)),'yaw_speed':float(m.yaw_speed),'rpy':list(map(float,m.imu_state.rpy)),'foot_force':list(map(int,m.foot_force)),'foot_speed_body':list(map(float,m.foot_speed_body))}
 state.update(d);samples.append(d)
def low_state(m):
 low.update(at=time.monotonic(),joint_q=[float(x.q) for x in m.motor_state[:12]],joint_dq=[float(x.dq) for x in m.motor_state[:12]],foot_force=list(map(int,m.foot_force)),wireless_remote=list(map(int,m.wireless_remote)))
 if len(low_samples)<2500:low_samples.append(dict(low))
def wireless_state(m):wireless.update(at=time.monotonic(),keys=int(m.keys),lx=float(m.lx),ly=float(m.ly),rx=float(m.rx),ry=float(m.ry))
subs=[n.create_subscription(SportModeState,'/lf/sportmodestate',sport,q),n.create_subscription(LowState,'/lf/lowstate',low_state,q),n.create_subscription(WirelessController,'/wirelesscontroller',wireless_state,q)]
def observed_request(m):
 if len(observed_requests)<10000:observed_requests.append({'at':time.monotonic(),'id':int(m.header.identity.id),'api':int(m.header.identity.api_id),'priority':int(m.header.policy.priority),'lease_id':int(m.header.lease.id),'noreply':bool(m.header.policy.noreply),'parameter':m.parameter})
request_sub=n.create_subscription(Request,'/api/sport/request',observed_request,10)
pubs={};reply_subs=[]
for service in ['sport','motion_switcher','obstacles_avoid']:
 pubs[service]=n.create_publisher(Request,'/api/'+service+'/request',10)
 def callback(m,svc=service):responses[(svc,int(m.header.identity.id))]={'service':svc,'id':int(m.header.identity.id),'api':int(m.header.identity.api_id),'code':int(m.header.status.code),'data':m.data,'at':time.monotonic()}
 reply_subs.append(n.create_subscription(Response,'/api/'+service+'/response',callback,10))
def spin(seconds):
 until=time.monotonic()+seconds
 while time.monotonic()<until:rclpy.spin_once(n,timeout_sec=.02)
def good():
 return time.monotonic()-state.get('at',0)<.25 and state.get('body_height',0)>.20 and len(state.get('rpy',[]))==3 and all(math.isfinite(x) for x in state['rpy']) and max(abs(state['rpy'][0]),abs(state['rpy'][1]))<.35
def rpc(api,param=None,svc='sport'):
 req=Request();req.header.identity.id=time.monotonic_ns();req.header.identity.api_id=int(api);req.parameter=json.dumps({} if param is None else param);req.header.policy.noreply=False
 event={'service':svc,'id':int(req.header.identity.id),'api':int(api),'parameter':req.parameter,'at':time.monotonic()};requests.append(event);pubs[svc].publish(req)
 end=time.monotonic()+2
 while time.monotonic()<end:
  rclpy.spin_once(n,timeout_sec=.02)
  if (svc,event['id']) in responses:return responses[(svc,event['id'])]
 return {'service':svc,'api':api,'id':event['id'],'error':'No matching response within 2 seconds'}
try:
 spin(3);report['before']=dict(state);report['wireless_before']=dict(wireless);report['low_before']=dict(low)
 for api,param,svc in [(1,None,'sport'),(1001,None,'motion_switcher'),(1026,None,'sport'),(1002,None,'obstacles_avoid'),(1034,['state','bodyHeight','speedLevel','gait','continuousGait','economicGait'],'sport')]:report['queries'].append(rpc(api,param,svc))
 if args.balance_stand:
  if not good():raise RuntimeError('Standing/fresh-state guard failed')
  report['zero_move_response']=rpc(1008,{'x':0.0,'y':0.0,'z':0.0})
  if report['zero_move_response'].get('code')!=0:raise RuntimeError('Zero Move service response not successful')
  if not good():raise RuntimeError('Standing guard failed before BalanceStand')
  report['balance_response']=rpc(1002)
  if report['balance_response'].get('code')!=0:raise RuntimeError('BalanceStand response not successful')
  until=time.monotonic()+3
  while time.monotonic()<until:
   rclpy.spin_once(n,timeout_sec=.02)
   if not good():rpc(1003);raise RuntimeError('Guard failed while settling BalanceStand; stopped')
 if args.observe_duration:
  print('READ_ONLY_OBSERVER_READY',flush=True);spin(min(90.,max(0.,args.observe_duration)))
 report['after']=dict(state);report['wireless_after']=dict(wireless);report['low_after']=dict(low);report['completed']=True
except (Exception,KeyboardInterrupt) as e:report['completed']=False;report['error']=str(e)
finally:
 report['requests']=requests;report['samples']=samples;report['low_samples']=low_samples;report['observed_sport_requests']=observed_requests;Path(args.output).write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ['samples','requests','low_before','low_after','low_samples','observed_sport_requests']},indent=2));n.destroy_node();rclpy.shutdown()
