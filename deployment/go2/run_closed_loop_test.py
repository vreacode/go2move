#!/usr/bin/env python3
"""Minimal supervised mapping -> return/loop evidence -> save -> Nav2 test.
Default requires --preflight; motion requires explicit --execute authorization.
Reuses existing guarded mapping, upstream SLAM/Nav2 and official Move/Stop.
"""
import argparse,json,math,os,signal,subprocess,sys,time
from pathlib import Path
p=argparse.ArgumentParser();mode=p.add_mutually_exclusive_group(required=True);mode.add_argument('--preflight',action='store_true');mode.add_argument('--execute',action='store_true');p.add_argument('--navigation-backend',choices=['ros2','sdk2'],default='ros2');p.add_argument('--mapping-yaw-gain',type=float,default=1.5);p.add_argument('--mapping-backend',choices=['ros2','sdk2'],default='ros2');p.add_argument('--platform-log',default='/tmp/go2-closed-loop-platform.log');p.add_argument('--resume-return',action='store_true');p.add_argument('--origin-from');p.add_argument('--route-map-origin');p.add_argument('--directory',required=True);args=p.parse_args()
if not math.isfinite(args.mapping_yaw_gain) or not 0.<=args.mapping_yaw_gain<=1.5:raise SystemExit('Mapping yaw gain must be 0..1.5')
if args.resume_return and not args.origin_from:raise SystemExit('Resume return requires the previous mapping report')
from go2_platform_write import backup,write
out=Path(args.directory).resolve();backup(out);out.mkdir(parents=True,exist_ok=True);backup(out/'report.json')
base=Path(__file__).resolve().parent;dds=Path('/home/unitree/go2move/ros2/go2_bringup/config/dds');env=os.environ.copy();env['CYCLONEDDS_URI']=str(dds/'test.xml')
report={'authorized_execute_flag':args.execute,'motion_attempted':False,'resumed_return_only':args.resume_return,'route_map_origin':args.route_map_origin,'mapping_yaw_gain':args.mapping_yaw_gain,'mapping_backend':args.mapping_backend,'navigation_backend':args.navigation_backend,'route':{'mapping_out_m':.50,'mapping_return_m':.50,'navigation_forward_m':.30,'speed_limit_m_s':.20,'lateral_limit_m':.12,'heading_limit_rad':.25},'steps':[]}
child=None;record=None;record_log=None

def run(script,extra,timeout=30,child_env=None):
 global child
 log_path=out/(Path(script).stem+'.log');backup(log_path)
 with log_path.open('w') as log:
  child=subprocess.Popen([sys.executable,'-u',str(base/script)]+extra,env=child_env or env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  try:rc=child.wait(timeout=timeout)
  except BaseException:
   if child.poll() is None:os.killpg(child.pid,signal.SIGINT)
   try:child.wait(timeout=6)
   except subprocess.TimeoutExpired:
    os.killpg(child.pid,signal.SIGTERM)
    try:child.wait(timeout=4)
    except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
   raise
  finally:child=None
 report['steps'].append({'script':script,'exit_code':rc,'log':str(log_path)})
 return rc

def read(name):return json.loads((out/name).read_text())
try:
 if run('check_short_goal.py',['--distance','.30','--motion-profile','--output',str(out/'planning-preflight.json')],25):raise RuntimeError('Planning preflight process failed')
 plan=read('planning-preflight.json')
 report['planning_preflight_passed']=bool(plan.get('passed'))
 if not plan.get('passed'):
  if not args.execute:
   raise RuntimeError('Planning preflight failed: '+plan.get('error','unknown'))
  report['initial_navigation_path_rejected']=plan.get('error');report['navigation_checks_deferred_until_mapping_return']=True
  print('Initial navigation path rejected; mapping only now, navigation replans with corridor checks after saving map.',flush=True)
 native_env=os.environ.copy();native_env['CYCLONEDDS_URI']=str(base/'cyclonedds_go2.xml')
 if run('prepare_walking.py',['--output',str(out/'native-preflight.json')],18,native_env):raise RuntimeError('Native state query failed')
 native=read('native-preflight.json');state=native.get('after',{});rpy=state.get('rpy',[])
 if not native.get('completed') or state.get('body_height',0)<.20 or len(rpy)!=3 or not all(math.isfinite(v) for v in rpy) or max(abs(rpy[0]),abs(rpy[1]))>=.35:raise RuntimeError('Standing/tilt preflight failed')
 report['standing_preflight_passed']=True;report['preflight_passed']=bool(plan.get('passed'));print('Standing preflight passed; no navigation or walking goal sent yet.',flush=True)
 if args.execute:
  platform_log=Path(args.platform_log);offset=platform_log.stat().st_size if platform_log.exists() else 0
  qos={}
  for topic in ['/scan','/imu/data','/lidar_packets_loss']:
   qos[topic]={'history':'keep_last','depth':10,'reliability':'best_effort','durability':'volatile'}
  for topic in ['/odom','/joint_states','/tf','/go2/status','/go2/motion_test_preview','/nav2/cmd_vel_preview']:
   qos[topic]={'history':'keep_last','depth':10,'reliability':'reliable','durability':'volatile'}
  for topic in ['/tf_static','/map']:
   qos[topic]={'history':'keep_last','depth':1,'reliability':'reliable','durability':'transient_local'}
  import yaml
  write(out/'bag-qos.yaml',yaml.safe_dump(qos));backup(out/'bag');backup(out/'bag.log');record_log=(out/'bag.log').open('w')
  record_env=os.environ.copy();record_env['CYCLONEDDS_URI']=str(dds/'record.xml')
  record=subprocess.Popen(['ros2','bag','record','-o',str(out/'bag'),'--qos-profile-overrides-path',str(out/'bag-qos.yaml')]+list(qos),env=record_env,stdout=record_log,stderr=subprocess.STDOUT,start_new_session=True)
  # Recorder does not open a robot command publisher. Mapping owns the armed bridge.
  report['raw_cloud_recorded']=False;report['cloud_observed_in_motion_preview']=True
  report['motion_attempted']=True;print('START supervised return to original origin' if args.resume_return else 'START supervised 0.50 m out-and-back mapping',flush=True)
  run('run_minimal_mapping.py',['--execute','--distance','.50','--max-speed','.20','--yaw-gain',str(args.mapping_yaw_gain),'--backend',args.mapping_backend,'--output',str(out/'mapping.json')]+(['--origin-from',args.origin_from] if args.origin_from else [])+(['--return-only'] if args.resume_return else [])+(['--route-map-origin',args.route_map_origin] if args.route_map_origin else []),78)
  mapping=read('mapping.json');report['mapping']={k:v for k,v in mapping.items() if k not in ['samples','control_samples','map_samples']}
  text=platform_log.read_text(errors='replace')[offset:] if platform_log.exists() else ''
  report['loop_closure']={'accepted_events':text.count('Karto accepted loop closure:'),'evidence_log':str(platform_log),'accepted_lines':[line for line in text.splitlines() if 'Karto accepted loop closure:' in line],'returning_alone_is_not_algorithm_confirmation':True}
  save_rc=run('save_platform_map.py',['--directory',str(out/'map')],28)
  if save_rc:raise RuntimeError('Map save failed')
  report['saved_map']=json.loads((out/'map/snapshot.json').read_text())
  if not mapping.get('completed') or mapping.get('return_error_m',999)>.10:raise RuntimeError('Mapping did not return within 10 cm; navigation skipped')
  if run('check_short_goal.py',['--distance','.30','--motion-profile','--output',str(out/'planning-after-return.json')],25):raise RuntimeError('Planning after return failed; no navigation goal sent')
  final_plan=read('planning-after-return.json');report['planning_after_return']=final_plan
  if not final_plan.get('passed'):raise RuntimeError('Navigation skipped after map save: '+final_plan.get('error','planning failed'))
  print('START supervised single 0.30 m forward NavigateToPose',flush=True)
  nav_rc=run('run_short_navigation.py',['--execute','--distance','.30','--backend',args.navigation_backend,'--output',str(out/'navigation.json')],65)
  navigation=read('navigation.json');report['navigation']={k:v for k,v in navigation.items() if k not in ['commands','feedback','odom_samples','control_samples']}
  if nav_rc or not navigation.get('completed'):raise RuntimeError('Single-goal navigation did not complete')
  report['motion_chain_completed']=True;report['completed']=report['loop_closure']['accepted_events']>0
  if not report['completed']:report['remaining']='Physical return, map save and navigation passed; no accepted SLAM loop event observed on this short route.'
 else:report.update(completed=False,prepared=True,remaining='Full 0.20 m/s out-and-back and navigation require new onsite motion authorization.')
except (Exception,KeyboardInterrupt) as e:report.update(completed=False,error=str(e));print('STOP:',e,flush=True)
finally:
 if record:
  if record.poll() is None:os.killpg(record.pid,signal.SIGINT)
  try:record.wait(timeout=8)
  except subprocess.TimeoutExpired:
   os.killpg(record.pid,signal.SIGTERM)
   try:record.wait(timeout=3)
   except subprocess.TimeoutExpired:os.killpg(record.pid,signal.SIGKILL);record.wait()
  record_log.close();report['bag_metadata_written']=(out/'bag/metadata.yaml').exists()
 (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2),flush=True)
raise SystemExit(0 if report.get('prepared') or report.get('motion_chain_completed') else 1)
