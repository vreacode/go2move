import socket
import struct
import time
import datetime
import threading
import os
from typing import Optional

class ZT6PreciseRecordingController:
    def __init__(self, ip: str = "", port: int = 37260):
        self.ip = ip
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(10.0)
        self.is_recording = False
        self.seq = 0
        self.recording_start_time = None
        self.command_delay = 2.0  # 命令处理延迟时间
        
        # CRC16查找表
        self.crc16_tab = [
            0x0,0x1021,0x2042,0x3063,0x4084,0x50a5,0x60c6,0x70e7,
            0x8108,0x9129,0xa14a,0xb16b,0xc18c,0xd1ad,0xe1ce,0xf1ef,
            0x1231,0x210,0x3273,0x2252,0x52b5,0x4294,0x72f7,0x62d6,
            0x9339,0x8318,0xb37b,0xa35a,0xd3bd,0xc39c,0xf3ff,0xe3de,
            0x2462,0x3443,0x420,0x1401,0x64e6,0x74c7,0x44a4,0x5485,
            0xa56a,0xb54b,0x8528,0x9509,0xe5ee,0xf5cf,0xc5ac,0xd58d,
            0x3653,0x2672,0x1611,0x630,0x76d7,0x66f6,0x5695,0x46b4,
            0xb75b,0xa77a,0x9719,0x8738,0xf7df,0xe7fe,0xd79d,0xc7bc,
            0x48c4,0x58e5,0x6886,0x78a7,0x840,0x1861,0x2802,0x3823,
            0xc9cc,0xd9ed,0xe98e,0xf9af,0x8948,0x9969,0xa90a,0xb92b,
            0x5af5,0x4ad4,0x7ab7,0x6a96,0x1a71,0xa50,0x3a33,0x2a12,
            0xdbfd,0xcbdc,0xfbbf,0xeb9e,0x9b79,0x8b58,0xbb3b,0xab1a,
            0x6ca6,0x7c87,0x4ce4,0x5cc5,0x2c22,0x3c03,0xc60,0x1c41,
            0xedae,0xfd8f,0xcdec,0xddcd,0xad2a,0xbd0b,0x8d68,0x9d49,
            0x7e97,0x6eb6,0x5ed5,0x4ef4,0x3e13,0x2e32,0x1e51,0xe70,
            0xff9f,0xefbe,0xdfdd,0xcffc,0xbf1b,0xaf3a,0x9f59,0x8f78,
            0x9188,0x81a9,0xb1ca,0xa1eb,0xd10c,0xc12d,0xf14e,0xe16f,
            0x1080,0xa1,0x30c2,0x20e3,0x5004,0x4025,0x7046,0x6067,
            0x83b9,0x9398,0xa3fb,0xb3da,0xc33d,0xd31c,0xe37f,0xf35e,
            0x2b1,0x1290,0x22f3,0x32d2,0x4235,0x5214,0x6277,0x7256,
            0xb5ea,0xa5cb,0x95a8,0x8589,0xf56e,0xe54f,0xd52c,0xc50d,
            0x34e2,0x24c3,0x14a0,0x481,0x7466,0x6447,0x5424,0x4405,
            0xa7db,0xb7fa,0x8799,0x97b8,0xe75f,0xf77e,0xc71d,0xd73c,
            0x26d3,0x36f2,0x691,0x16b0,0x6657,0x7676,0x4615,0x5634,
            0xd94c,0xc96d,0xf90e,0xe92f,0x99c8,0x89e9,0xb98a,0xa9ab,
            0x5844,0x4865,0x7806,0x6827,0x18c0,0x8e1,0x3882,0x28a3,
            0xcb7d,0xdb5c,0xeb3f,0xfb1e,0x8bf9,0x9bd8,0xabbb,0xbb9a,
            0x4a75,0x5a54,0x6a37,0x7a16,0xaf1,0x1ad0,0x2ab3,0x3a92,
            0xfd2e,0xed0f,0xdd6c,0xcd4d,0xbdaa,0xad8b,0x9de8,0x8dc9,
            0x7c26,0x6c07,0x5c64,0x4c45,0x3ca2,0x2c83,0x1ce0,0xcc1,
            0xef1f,0xff3e,0xcf5d,0xdf7c,0xaf9b,0xbfba,0x8fd9,0x9ff8,
            0x6e17,0x7e36,0x4e55,0x5e74,0x2e93,0x3eb2,0xed1,0x1ef0
        ]
    
    def calculate_crc16(self, data: bytes) -> int:
        """计算CRC16校验码"""
        crc = 0
        for byte in data:
            temp = (crc >> 8) & 0xff
            oldcrc16 = self.crc16_tab[byte ^ temp]
            crc = (crc << 8) ^ oldcrc16
        return crc & 0xFFFF
    
    def build_packet(self, cmd_id: int, data: bytes = b'', need_ack: bool = True) -> bytes:
        """构建数据包"""
        stx = struct.pack('<H', 0x6655)  # 小端序
        ctrl = 0x01 if need_ack else 0x00
        data_len = len(data)
        seq = self.seq
        self.seq = (self.seq + 1) % 65536
        
        packet = stx + struct.pack('B', ctrl) + struct.pack('<H', data_len) + struct.pack('<H', seq) + struct.pack('B', cmd_id) + data
        
        crc = self.calculate_crc16(packet)
        packet += struct.pack('<H', crc)  # 小端序
        
        return packet
    
    def send_command(self, cmd_id: int, data: bytes = b'', need_ack: bool = True, timeout: float = 10.0) -> Optional[bytes]:
        """发送命令并等待响应"""
        try:
            original_timeout = self.sock.gettimeout()
            self.sock.settimeout(timeout)
            
            packet = self.build_packet(cmd_id, data, need_ack)
            print(f"发送命令: CMD_ID=0x{cmd_id:02X}, 数据包: {packet.hex()}")
            
            self.sock.sendto(packet, (self.ip, self.port))
            
            if need_ack:
                try:
                    response, addr = self.sock.recvfrom(1024)
                    print(f"收到响应: {response.hex()} 来自 {addr}")
                    return response
                except socket.timeout:
                    print(f"等待响应超时 ({timeout}秒)")
                    return None
            return None
        except Exception as e:
            print(f"发送命令失败: {e}")
            return None
        finally:
            self.sock.settimeout(original_timeout)
    
    def send_heartbeat(self) -> bool:
        """发送心跳包"""
        heartbeat = bytes([0x55, 0x66, 0x01, 0x01, 0x00, 0x00, 0x00, 0x08, 0x01, 0xd1, 0x12])
        print(f"发送心跳包: {heartbeat.hex()}")
        
        self.sock.sendto(heartbeat, (self.ip, self.port))
        
        try:
            response, addr = self.sock.recvfrom(1024)
            print(f"心跳响应: {response.hex()} 来自 {addr}")
            return True
        except socket.timeout:
            print("心跳无响应")
            return False
    
    def set_utc_time(self) -> bool:
        """设置UTC时间"""
        print("设置UTC时间")
        utc_time_us = int(time.time() * 1000000)
        data = struct.pack('<Q', utc_time_us)
        response = self.send_command(0x30, data)
        return response is not None
    
    def start_recording(self) -> bool:
        """开始录像"""
        if self.is_recording:
            print("录像已在进行中")
            return False
        
        print("开始录像...")
        
        # 录像: CMD_ID=0x0C, func_type=2
        data = struct.pack('B', 2)
        response = self.send_command(0x0C, data, timeout=5.0)
        
        print("✅ 录像命令已发送！")
        self.is_recording = True
        self.recording_start_time = time.time()
        
        # 等待命令处理完成
        print(f"等待命令处理完成 ({self.command_delay}秒)...")
        time.sleep(self.command_delay)
        
        return True
    
    def stop_recording(self) -> bool:
        """停止录像"""
        if not self.is_recording:
            print("当前未在录像")
            return False
        
        print("停止录像...")
        
        # 停止录像: 发送拍照命令
        response = self.send_command(0x0C, struct.pack('B', 0), timeout=5.0)
        
        # 计算实际录制时间
        if self.recording_start_time:
            actual_duration = time.time() - self.recording_start_time
            print(f"实际录制时间: {actual_duration:.1f}秒")
        
        print("✅ 停止录像命令已发送！")
        self.is_recording = False
        self.recording_start_time = None
        
        # 等待命令处理完成
        print(f"等待命令处理完成 ({self.command_delay}秒)...")
        time.sleep(self.command_delay)
        
        return True
    
    def take_photo(self) -> bool:
        """拍照"""
        print("拍照")
        data = struct.pack('B', 0)
        response = self.send_command(0x0C, data)
        return response is not None
    
    def precise_recording(self, duration: int = 30):
        """精确录制 - 考虑命令处理延迟"""
        print(f"开始精确录制: {duration}秒")
        print("=" * 50)
        
        # 设置时间
        print("1. 设置时间...")
        self.set_utc_time()
        time.sleep(1)
        
        # 开始录制
        print(f"2. 开始录制...")
        start_time = time.time()
        
        if self.start_recording():
            # 计算实际等待时间（减去命令处理时间）
            actual_wait_time = duration - (self.command_delay * 2)  # 开始和停止各需要延迟
            if actual_wait_time > 0:
                print(f"3. 录制中... 等待 {actual_wait_time:.1f} 秒")
                time.sleep(actual_wait_time)
            else:
                print("3. 录制时间太短，立即停止")
            
            # 停止录制
            print("4. 停止录制...")
            self.stop_recording()
            
            # 计算实际时间
            actual_time = time.time() - start_time
            print(f"5. 录制完成！")
            print(f"   计划录制时间: {duration}秒")
            print(f"   实际录制时间: {actual_time:.1f}秒")
            print(f"   时间差异: {abs(actual_time - duration):.1f}秒")
            print(f"   命令处理延迟: {self.command_delay * 2:.1f}秒")
            
            print("\n✅ 精确录制完成！")
            print("请检查SD卡中的录像文件")
        else:
            print("❌ 录制启动失败")
    
    def batch_precise_recording(self, count: int = 5, duration: int = 10, interval: int = 5):
        """批量精确录制"""
        print(f"开始批量精确录制: {count}次，每次{duration}秒，间隔{interval}秒")
        print("=" * 60)
        
        for i in range(count):
            print(f"\n=== 第 {i+1}/{count} 次录制 ===")
            
            # 设置时间
            print("设置时间...")
            self.set_utc_time()
            time.sleep(1)
            
            # 精确录制
            self.precise_recording(duration)
            
            # 等待间隔
            if i < count - 1:
                print(f"\n等待 {interval} 秒后开始下次录制...")
                time.sleep(interval)
        
        print("\n" + "=" * 60)
        print("✅ 批量精确录制完成！")
        print("请检查SD卡中的所有录像文件")
    
    def calibrate_delay(self):
        """校准命令处理延迟"""
        print("=== 校准命令处理延迟 ===")
        print("这将测试设备的命令处理时间...")
        
        # 测试开始录像延迟
        print("\n1. 测试开始录像延迟...")
        start_time = time.time()
        self.start_recording()
        start_delay = time.time() - start_time
        print(f"开始录像延迟: {start_delay:.1f}秒")
        
        # 等待1秒
        time.sleep(1)
        
        # 测试停止录像延迟
        print("\n2. 测试停止录像延迟...")
        start_time = time.time()
        self.stop_recording()
        stop_delay = time.time() - start_time
        print(f"停止录像延迟: {stop_delay:.1f}秒")
        
        # 计算平均延迟
        avg_delay = (start_delay + stop_delay) / 2
        print(f"\n平均命令处理延迟: {avg_delay:.1f}秒")
        print(f"建议设置延迟: {avg_delay:.1f}秒")
        
        # 更新延迟设置
        self.command_delay = avg_delay
        print(f"已更新延迟设置为: {self.command_delay:.1f}秒")
    
    def test_connection(self) -> bool:
        """测试连接"""
        print(f"测试连接到 {self.ip}:{self.port}")
        return self.send_heartbeat()
    
    def interactive_mode(self):
        """交互模式"""
        print("\n=== ZT6 精确录像控制模式 ===")
        print("可用命令:")
        print("1. 精确单次录制")
        print("2. 批量精确录制")
        print("3. 校准延迟")
        print("4. 拍照")
        print("5. 设置时间")
        print("6. 测试连接")
        print("q. 退出")
        
        while True:
            try:
                cmd = input("\n请输入命令 (1-6, q): ").strip().lower()
                
                if cmd == 'q':
                    break
                elif cmd == '1':
                    duration = int(input("录制时长(秒): "))
                    self.precise_recording(duration)
                elif cmd == '2':
                    count = int(input("录制次数: "))
                    duration = int(input("每次时长(秒): "))
                    interval = int(input("间隔时间(秒): "))
                    self.batch_precise_recording(count, duration, interval)
                elif cmd == '3':
                    self.calibrate_delay()
                elif cmd == '4':
                    self.take_photo()
                elif cmd == '5':
                    self.set_utc_time()
                elif cmd == '6':
                    self.test_connection()
                else:
                    print("无效命令")
                    
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"命令执行出错: {e}")
        
        print("精确录像控制模式结束")
    
    def close(self):
        """关闭连接"""
        self.sock.close()

def main():
    """主函数"""
    controller = ZT6PreciseRecordingController()
    
    try:
        print("=== ZT6 精确录像控制器 ===")
        print("特点: 考虑命令处理延迟，提供更精确的录制时间控制")
        
        # 1. 测试连接
        if not controller.test_connection():
            print("无法连接到设备")
            return
        
        # 2. 设置时间
        controller.set_utc_time()
        
        # 3. 运行交互模式
        controller.interactive_mode()
        
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    except Exception as e:
        print(f"程序执行出错: {e}")
    finally:
        controller.close()
        print("程序结束")

if __name__ == "__main__":
    main() 
