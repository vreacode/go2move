# 部署文件

board-scripts/ 中保存了板上现有的建图和导航入口脚本，便于审查和版本化。脚本目前使用板上的绝对路径，例如 /home/nvidia/Desktop/wheeltec_ros2 和 /home/nvidia/fastlio2_ws，暂时不能视作跨机器安装脚本。

后续推进时，优先把这些路径改为环境变量或安装前缀，并增加：

1. ROS 节点和 topic readiness 检查；
2. 启动失败时的清理和日志归档；
3. 仿真/实车模式开关；
4. 速度限制和急停前置检查。
