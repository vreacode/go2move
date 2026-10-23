#!/usr/bin/env python3
"""Reuse Nav2 ComputePathToPose to the confirmed forward route; no motion/action goal."""
import argparse,json,math,time,sys
from pathlib import Path
import rclpy
from rclpy.action import ActionClient
from nav2_msgs.action import ComputePathToPose
from tf2_ros import Buffer
from route_tf import RouteTransformListener
p=argparse.ArgumentParser();p.add_argument('--distance',type=float,default=.3);p.add_argument('--motion-profile',action='store_true');p.add_argument('--output',required=True);p.add_argument('--goal-from');args=p.parse_args()
if not 0<args.distance<=.5:raise SystemExit('Planning check limited to 0.5 m forward')
from go2_platform_write import backup
backup(args.output)
rclpy.init();n=rclpy.create_node('go2_short_goal_planning_check');b=Buffer();l=RouteTransformListener(b,n);client=ActionClient(n,ComputePathToPose,'/compute_path_to_pose');report={'motion_sent':False,'action':'ComputePathToPose'}
try:
 until=time.monotonic()+6
 while time.monotonic()<until:rclpy.spin_once(n,timeout_sec=.05)
 if args.motion_profile:
  from rcl_interfaces.srv import GetParameters
  c=n.create_client(GetParameters,'/controller_server/get_parameters')
  if not c.wait_for_service(timeout_sec=3):raise RuntimeError('Controller parameter service unavailable')
  names=['FollowPath.desired_linear_vel','FollowPath.min_approach_linear_velocity','FollowPath.goal_dist_tol','goal_checker.xy_goal_tolerance']
  req=GetParameters.Request();req.names=names;f=c.call_async(req);rclpy.spin_until_future_complete(n,f,timeout_sec=3)
  if not f.done() or f.result() is None:raise RuntimeError('Controller parameter query timed out')
  values=f.result().values;report['controller_parameters']={name:{'type':v.type,'value':v.double_value} for name,v in zip(names,values)}
  if len(values)!=4 or any(v.type!=3 or abs(v.double_value-e)>1e-6 for v,e in zip(values,[.20,.20,.10,.10])):raise RuntimeError('Active controller does not match supervised 0.20 m/s profile')
 t=b.lookup_transform('map','base_footprint',rclpy.time.Time());q=t.transform.rotation;yaw=math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z));x=t.transform.translation.x;y=t.transform.translation.y
 gx=x+args.distance*math.cos(yaw);gy=y+args.distance*math.sin(yaw)
 if args.goal_from:
  prior=json.loads(Path(args.goal_from).read_text());fixed=prior['start_map'];target=prior['goal_map']
  if not all(math.isfinite(v)for v in fixed+target)or abs(prior['distance_m']-args.distance)>1e-6:raise RuntimeError('Invalid original navigation goal')
  if math.hypot(x-fixed[0],y-fixed[1])>.06 or abs(math.atan2(math.sin(yaw-fixed[2]),math.cos(yaw-fixed[2])))>.12:raise RuntimeError('Pose moved after original planning; refusing to reset goal')
  x,y,yaw=fixed;gx,gy,heading=target;q.x=0.;q.y=0.;q.z=math.sin(heading/2);q.w=math.cos(heading/2);report['goal_preserved_from']=str(Path(args.goal_from).resolve())
 report['start']=[x,y,yaw];report['goal']=[gx,gy,yaw]
 if not client.wait_for_server(timeout_sec=4):raise RuntimeError('Planner unavailable')
 goal=ComputePathToPose.Goal();goal.pose.header.frame_id='map';goal.pose.header.stamp=n.get_clock().now().to_msg();goal.pose.pose.position.x=float(gx);goal.pose.pose.position.y=float(gy);goal.pose.pose.orientation=q;goal.planner_id='GridBased'
 f=client.send_goal_async(goal);rclpy.spin_until_future_complete(n,f,timeout_sec=5)
 if not f.done() or not f.result().accepted:raise RuntimeError('Planning goal not accepted')
 h=f.result();f=h.get_result_async();rclpy.spin_until_future_complete(n,f,timeout_sec=8)
 if not f.done():h.cancel_goal_async();raise RuntimeError('Planning result timeout')
 result=f.result();poses=result.result.path.poses;path=[[p.pose.position.x,p.pose.position.y] for p in poses];report.update(status=result.status,path=path,poses=len(path))
 if result.status!=4 or len(path)<2:raise RuntimeError('Planning did not produce a path')
 error=math.hypot(path[-1][0]-gx,path[-1][1]-gy);length=sum(math.hypot(x1-x0,y1-y0) for (x0,y0),(x1,y1) in zip(path,path[1:]));c,s=math.cos(yaw),math.sin(yaw);route=[[(px-x)*c+(py-y)*s,-(px-x)*s+(py-y)*c] for px,py in path];lateral=max(abs(lat) for along,lat in route);inside=all(abs(lat)<=.12 and -.06<=along<=args.distance+.06 for along,lat in route)
 report.update(endpoint_error_m=error,path_length_m=length,max_lateral_m=lateral,path_inside_confirmed_corridor=inside,passed=error<=.08 and args.distance-.08<=length<=args.distance+.20 and inside)
 if not report['passed']:raise RuntimeError('Path endpoint/length/corridor does not fit the supervised straight route')
except Exception as e:report.update(passed=False,error=str(e))
finally:
 Path(args.output).write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2));client.destroy();n.destroy_node();rclpy.shutdown()
