#!/usr/bin/env python3
"""
ROS2 SLAM 系统调试脚本
用于检查gmapping建图系统的各个组件是否正常工作
"""

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import LaserScan, PointCloud2
from nav_msgs.msg import OccupancyGrid, MapMetaData
from geometry_msgs.msg import TransformStamped
from tf2_ros import TransformListener, Buffer
import time
import subprocess
import sys

class SLAMDebugger(Node):
    def __init__(self):
        super().__init__('slam_debugger')
        
        # 创建TF监听器
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        
        # 订阅话题
        self.scan_sub = self.create_subscription(
            LaserScan, '/scan', self.scan_callback, 10)
        self.map_sub = self.create_subscription(
            OccupancyGrid, '/map', self.map_callback, 10)
        self.map_meta_sub = self.create_subscription(
            MapMetaData, '/map_metadata', self.map_meta_callback, 10)
        self.pointcloud_sub = self.create_subscription(
            PointCloud2, '/hesai_ros_driver/points', self.pointcloud_callback, 10)
        
        # 状态变量
        self.scan_received = False
        self.map_received = False
        self.pointcloud_received = False
        self.last_scan_time = None
        self.last_map_time = None
        self.last_pointcloud_time = None
        
        # 创建定时器进行定期检查
        self.timer = self.create_timer(2.0, self.check_system_status)
        
        self.get_logger().info("SLAM调试器已启动，开始监控系统状态...")

    def scan_callback(self, msg):
        self.scan_received = True
        self.last_scan_time = time.time()
        self.get_logger().info(f"收到激光扫描数据: {len(msg.ranges)} 个点, 范围: {msg.range_min:.2f}-{msg.range_max:.2f}m")

    def map_callback(self, msg):
        self.map_received = True
        self.last_map_time = time.time()
        self.get_logger().info(f"收到地图数据: {msg.info.width}x{msg.info.height}, 分辨率: {msg.info.resolution:.3f}m/pixel")

    def map_meta_callback(self, msg):
        self.get_logger().info(f"地图元数据更新: 分辨率={msg.resolution:.3f}, 原点=({msg.origin.position.x:.2f}, {msg.origin.position.y:.2f})")

    def pointcloud_callback(self, msg):
        self.pointcloud_received = True
        self.last_pointcloud_time = time.time()
        self.get_logger().info(f"收到点云数据: {msg.width}x{msg.height} 点, 时间戳: {msg.header.stamp.sec}")

    def check_tf_chain(self):
        """检查TF变换链"""
        self.get_logger().info("=== 检查TF变换链 ===")
        
        tf_chain = [
            ('map', 'odom'),
            ('odom', 'base_link'),
            ('base_link', 'laser_link')
        ]
        
        for parent, child in tf_chain:
            try:
                transform = self.tf_buffer.lookup_transform(
                    parent, child, rclpy.time.Time(), timeout=rclpy.duration.Duration(seconds=1.0))
                self.get_logger().info(f"✓ {parent} -> {child}: 变换正常")
            except Exception as e:
                self.get_logger().error(f"✗ {parent} -> {child}: 变换失败 - {str(e)}")

    def check_topics(self):
        """检查话题状态"""
        self.get_logger().info("=== 检查话题状态 ===")
        
        topics_to_check = [
            ('/scan', 'sensor_msgs/msg/LaserScan'),
            ('/map', 'nav_msgs/msg/OccupancyGrid'),
            ('/hesai_ros_driver/points', 'sensor_msgs/msg/PointCloud2'),
            ('/map_metadata', 'nav_msgs/msg/MapMetaData')
        ]
        
        for topic, msg_type in topics_to_check:
            try:
                result = subprocess.run(
                    ['ros2', 'topic', 'list', '|', 'grep', topic],
                    shell=True, capture_output=True, text=True, timeout=5)
                if topic in result.stdout:
                    self.get_logger().info(f"✓ {topic}: 话题存在")
                else:
                    self.get_logger().warn(f"✗ {topic}: 话题不存在")
            except Exception as e:
                self.get_logger().error(f"✗ 检查话题 {topic} 时出错: {str(e)}")

    def check_system_status(self):
        """定期检查系统状态"""
        self.get_logger().info("=== 系统状态检查 ===")
        
        # 检查数据接收状态
        current_time = time.time()
        
        if self.scan_received:
            if self.last_scan_time and (current_time - self.last_scan_time) < 5.0:
                self.get_logger().info("✓ 激光扫描数据: 正常接收")
            else:
                self.get_logger().warn("⚠ 激光扫描数据: 接收中断")
        else:
            self.get_logger().error("✗ 激光扫描数据: 未接收到")
        
        if self.pointcloud_received:
            if self.last_pointcloud_time and (current_time - self.last_pointcloud_time) < 5.0:
                self.get_logger().info("✓ 点云数据: 正常接收")
            else:
                self.get_logger().warn("⚠ 点云数据: 接收中断")
        else:
            self.get_logger().error("✗ 点云数据: 未接收到")
        
        if self.map_received:
            if self.last_map_time and (current_time - self.last_map_time) < 10.0:
                self.get_logger().info("✓ 地图数据: 正常更新")
            else:
                self.get_logger().warn("⚠ 地图数据: 更新缓慢")
        else:
            self.get_logger().warn("⚠ 地图数据: 尚未生成")
        
        # 检查TF链
        self.check_tf_chain()
        
        # 检查话题
        self.check_topics()
        
        self.get_logger().info("=== 检查完成 ===\n")

def main():
    rclpy.init()
    debugger = SLAMDebugger()
    
    try:
        rclpy.spin(debugger)
    except KeyboardInterrupt:
        debugger.get_logger().info("调试器停止")
    finally:
        debugger.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
