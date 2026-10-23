"""Foxy TF listener restricted to the two transforms used by route guards.
Uses the standard geometry2 buffer/listener, avoiding unrelated joint TF load.
"""
from tf2_ros import TransformListener
from tf2_msgs.msg import TFMessage

class RouteTransformListener(TransformListener):
    def callback(self, message):
        required = {('map', 'odom'), ('odom', 'base_footprint')}
        filtered = TFMessage(transforms=[t for t in message.transforms
            if (t.header.frame_id, t.child_frame_id) in required])
        super().callback(filtered)
