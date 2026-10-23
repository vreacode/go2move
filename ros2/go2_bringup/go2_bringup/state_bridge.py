"""Subscribe to hardware only; no robot command API or command publisher exists."""
import json, math, os, sys, time, subprocess, threading, collections
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Imu, JointState
from nav_msgs.msg import Odometry
from geometry_msgs.msg import TransformStamped
from std_msgs.msg import String
from tf2_ros import TransformBroadcaster, StaticTransformBroadcaster

JOINTS = [leg+'_'+joint+'_joint' for leg in ['FR','FL','RR','RL'] for joint in ['hip','thigh','calf']]
def quat_wxyz(q):
    v=[float(q[1]),float(q[2]),float(q[3]),float(q[0])]
    norm=math.sqrt(sum(x*x for x in v))
    if not all(math.isfinite(x) for x in v) or norm < 0.5:
        raise ValueError('invalid hardware quaternion')
    return [x/norm for x in v]
def rpy_quat(r,p,y):
    cr,sr=math.cos(r/2),math.sin(r/2);cp,sp=math.cos(p/2),math.sin(p/2);cy,sy=math.cos(y/2),math.sin(y/2)
    return [sr*cp*cy-cr*sp*sy,cr*sp*cy+sr*cp*sy,cr*cp*sy-sr*sp*cy,cr*cp*cy+sr*sp*sy]
def fill_q(q,v): q.x,q.y,q.z,q.w=v
def fill_v(v,a): v.x,v.y,v.z=[float(x) for x in a]
def stamp(ns):
    from builtin_interfaces.msg import Time
    return Time(sec=ns//1000000000,nanosec=ns%1000000000)

def udp_reader():
    from unitree_go.msg import LowState, SportModeState
    rclpy.init(args=['--ros-args','--disable-external-lib-logs','--disable-rosout-logs'])
    n=rclpy.create_node('go2_hardware_readonly_reader',enable_rosout=False)
    qos=QoSProfile(depth=10,reliability=ReliabilityPolicy.RELIABLE)
    def emit(kind,m):
        i=m.imu_state
        data={'kind':kind,'receive_ns':time.time_ns(),'quaternion':list(map(float,i.quaternion)),
              'rpy':list(map(float,i.rpy)),'gyro':list(map(float,i.gyroscope)),
              'accel':list(map(float,i.accelerometer))}
        if kind=='low':
            data.update(q=[float(x.q) for x in m.motor_state[:12]],
                        dq=[float(x.dq) for x in m.motor_state[:12]],
                        effort=[float(x.tau_est) for x in m.motor_state[:12]],tick=int(m.tick))
        else:
            data.update(position=list(map(float,m.position)),velocity=list(map(float,m.velocity)),
                        yaw_speed=float(m.yaw_speed),body_height=float(m.body_height),
                        mode=int(m.mode),error_code=int(m.error_code),
                        source_stamp_ns=int(m.stamp.sec)*1000000000+int(m.stamp.nanosec))
        print(json.dumps(data,separators=(',',':')),flush=True)
    subs=[n.create_subscription(LowState,os.environ['GO2_LOW_TOPIC'],lambda m:emit('low',m),qos),
          n.create_subscription(SportModeState,os.environ['GO2_SPORT_TOPIC'],lambda m:emit('sport',m),qos)]
    try:rclpy.spin(n)
    except KeyboardInterrupt:pass
    finally:n.destroy_node();rclpy.shutdown()

class StateBridge(Node):
    def __init__(self):
        super().__init__('state_bridge')
        for key,value in [('lowstate_topic','/lf/lowstate'),('sportstate_topic','/lf/sportmodestate'),
            ('state_timeout',0.5),('go2_dds_uri','/home/unitree/go2move/deployment/go2/cyclonedds_go2.xml'),
            ('lidar_xyz',[0.0,0.0,0.15]),('lidar_rpy',[0.0,0.0,0.0]),('lidar_extrinsics_confirmed',False)]:
            self.declare_parameter(key,value)
        self.joint_pub=self.create_publisher(JointState,'/joint_states',10)
        self.imu_pub=self.create_publisher(Imu,'/imu/data',10)
        self.odom_pub=self.create_publisher(Odometry,'/odom',10)
        self.status_pub=self.create_publisher(String,'/go2/status',10)
        self.tf=TransformBroadcaster(self)
        self.latest={};self.lock=threading.Lock();self.processed={};self.counts=collections.Counter()
        env=os.environ.copy();env['CYCLONEDDS_URI']=self.get_parameter('go2_dds_uri').value
        env['GO2_LOW_TOPIC']=self.get_parameter('lowstate_topic').value;env['GO2_SPORT_TOPIC']=self.get_parameter('sportstate_topic').value
        env['PYTHONUNBUFFERED']='1';env['PYTHONDONTWRITEBYTECODE']='1'
        self.reader=subprocess.Popen([sys.executable,'-u',__file__,'--udp-reader'],env=env,
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
        threading.Thread(target=self.read_stream,daemon=True).start()
        threading.Thread(target=self.read_errors,daemon=True).start()
        self.timer=self.create_timer(.01,self.publish_latest);self.health=self.create_timer(1.,self.publish_status)
        self.get_logger().info('Read-only state bridge started; robot command publishers: none')
        if not self.get_parameter('lidar_extrinsics_confirmed').value:
            self.get_logger().warn('XT-16 extrinsics are PROVISIONAL; physical alignment is not verified')
    def read_stream(self):
        for line in self.reader.stdout:
            try:
                d=json.loads(line)
                with self.lock:self.latest[d['kind']]=d
            except (ValueError,KeyError):pass
    def read_errors(self):
        for line in self.reader.stderr:
            if line.strip():self.get_logger().warn('hardware reader: '+line.strip())
    def publish_latest(self):
        with self.lock:latest=dict(self.latest)
        for kind,d in latest.items():
            ns=d['receive_ns']
            if self.processed.get(kind)==ns or (time.time_ns()-ns)*1e-9>self.get_parameter('state_timeout').value:continue
            try:
                if kind=='low':self.publish_low(d)
                else:self.publish_sport(d)
                self.processed[kind]=ns;self.counts[kind]+=1
            except (ValueError,IndexError) as e:self.get_logger().error(str(e))
    def publish_low(self,d):
        if not all(math.isfinite(x) for x in d['q']+d['dq']+d['accel']+d['gyro']):raise ValueError('non-finite hardware state')
        js=JointState();js.header.stamp=stamp(d['receive_ns']);js.name=JOINTS;js.position=d['q'];js.velocity=d['dq'];js.effort=d['effort'];self.joint_pub.publish(js)
        m=Imu();m.header.stamp=js.header.stamp;m.header.frame_id='imu';fill_q(m.orientation,quat_wxyz(d['quaternion']))
        fill_v(m.angular_velocity,d['gyro']);fill_v(m.linear_acceleration,d['accel'])
        for index in [0,4,8]:m.orientation_covariance[index]=0.01;m.angular_velocity_covariance[index]=0.0025;m.linear_acceleration_covariance[index]=0.04
        self.imu_pub.publish(m)
    def publish_sport(self,d):
        if not all(math.isfinite(x) for x in d['position']+d['velocity']+d['rpy']):raise ValueError('non-finite sport state')
        # Go2 estimated position/velocity is world aligned; rotate velocity into child frame.
        roll,pitch,yaw=d['rpy'];q=quat_wxyz(d['quaternion']);xy=d['position'];cy,sy=math.cos(yaw),math.sin(yaw)
        m=Odometry();m.header.stamp=stamp(d['receive_ns']);m.header.frame_id='odom';m.child_frame_id='base_footprint'
        m.pose.pose.position.x=xy[0];m.pose.pose.position.y=xy[1];fill_q(m.pose.pose.orientation,rpy_quat(0.,0.,yaw))
        vx,vy,vz=d['velocity'];m.twist.twist.linear.x=cy*vx+sy*vy;m.twist.twist.linear.y=-sy*vx+cy*vy;m.twist.twist.angular.z=d['yaw_speed']
        for i,v in [(0,.02),(7,.02),(14,1e6),(21,1e6),(28,1e6),(35,.02)]:m.pose.covariance[i]=v
        for i,v in [(0,.05),(7,.05),(14,1e6),(21,1e6),(28,1e6),(35,.03)]:m.twist.covariance[i]=v
        self.odom_pub.publish(m)
        t=TransformStamped();t.header=m.header;t.child_frame_id='base_footprint';t.transform.translation.x=xy[0];t.transform.translation.y=xy[1];t.transform.rotation=m.pose.pose.orientation
        body=TransformStamped();body.header.stamp=m.header.stamp;body.header.frame_id='base_footprint';body.child_frame_id='base_link'
        body.transform.translation.z=xy[2];fill_q(body.transform.rotation,rpy_quat(roll,pitch,0.))
        self.tf.sendTransform([t,body])
    def publish_status(self):
        with self.lock:latest=dict(self.latest)
        d=latest.get('sport',{});ages={k:(time.time_ns()-v['receive_ns'])*1e-9 for k,v in latest.items()}
        fresh=len(ages)==2 and all(v<self.get_parameter('state_timeout').value for v in ages.values())
        status={'fresh':fresh,'age_seconds':ages,'counts':dict(self.counts),'source_mode':d.get('mode'),
                'source_error_code':d.get('error_code'),'source_body_height':d.get('body_height'),
                'hardware_stamp_offset_seconds':(d.get('receive_ns',0)-d.get('source_stamp_ns',0))*1e-9,
                'lidar_extrinsics_confirmed':self.get_parameter('lidar_extrinsics_confirmed').value,
                'motion_output_enabled':False,'reader_alive':self.reader.poll() is None}
        self.status_pub.publish(String(data=json.dumps(status)))
        if not fresh:self.get_logger().warn('Go2 state missing/stale; odom and joints are not synthesized')
    def close(self):
        if self.reader.poll() is None:
            self.reader.terminate()
            try:self.reader.wait(timeout=3)
            except subprocess.TimeoutExpired:self.reader.kill();self.reader.wait()

def main():
    if '--udp-reader' in sys.argv:udp_reader();return
    rclpy.init();node=StateBridge()
    try:rclpy.spin(node)
    except KeyboardInterrupt:pass
    finally:node.close();node.destroy_node();rclpy.shutdown()
if __name__=='__main__':main()
