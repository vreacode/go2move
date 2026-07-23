# CarMove Android APK

这是一个很薄的 Android WebView 外壳，真正的采集与监控界面仍由 Dashboard 提供。APK 首次启动会要求填写 Dashboard 地址，例如：

```text
http://100.x.y.z:8080
```

手机必须先安装并登录 Tailscale，且和 Dashboard/Agent 加入同一个 tailnet。APK 不内置 Tailscale VPN，也不会绕过 Dashboard 登录。

## 直接安装测试

1. 手机安装 Tailscale，登录和小车/Dashboard 相同的 tailnet。
2. 安装 `app-debug.apk`。
3. 打开 APK，在地址框填写 Dashboard 的 Tailscale 地址，不要填写 Agent 的 9001 端口：

   ```text
   http://<Dashboard设备的Tailscale IP>:8080
   ```

4. 登录 Dashboard 使用 `.env` 中的 `AUTH_USERNAME` 和 `AUTH_PASSWORD`。
5. 先打开“监控”确认小车在线，再回到“采集”测试可见光/红外切换、实时画面、单帧快照和 1 分钟采集。

如果 Dashboard 和 Agent 分开运行，Dashboard 的 `.env` 需要把 `TAILSCALE_HOST` 设置成 Agent 的 Tailscale IP；手机地址仍然填写 Dashboard 所在设备的 Tailscale IP。

在小车/Dashboard 设备上查看地址：

```bash
tailscale ip -4
```

手机访问前可先在手机浏览器打开同一个 `http://<IP>:8080`，浏览器能打开后 APK 才能打开。

## Windows 构建

用 Android Studio 打开 `apps/nano-deploy/mobile/android`，等待 Gradle 同步完成，执行 `Build > Build APK(s)`。也可以在 PowerShell 中执行：

```powershell
cd apps\nano-deploy\mobile\android
.\gradlew.bat assembleDebug
```

输出仍然是 `app\build\outputs\apk\debug\app-debug.apk`。项目自带 Gradle Wrapper，不需要在 Windows 预装 Gradle；Android Studio 需要 JDK 11、Android SDK Platform 28 和可用的 Android Build Tools。

## 常见问题

- 页面显示旧版：当前 systemd 服务可能仍指向 `/home/nvidia/Desktop/.../Nano_Deploy` 的旧工作树，需要把新 Dashboard/Agent 部署到实际运行目录并重启服务。
- 显示“等待小车”：先确认 Tailscale 在线，再确认 Dashboard 的 8080 端口可访问。
- 视频没有画面：手机不需要直连 RTSP；检查 Agent/摄像头状态和 Dashboard 到 Agent 的 Tailscale 配置。
- APK 只负责连接和展示，第一版不会发送车辆运动命令。

## 构建

需要 Android SDK、Gradle 和 JDK 11。项目使用 Android Gradle Plugin 3.5.4，最低 Android API 23，目标 API 28。

```bash
cd apps/nano-deploy/mobile/android
gradle assembleDebug
```

输出文件：

```text
apps/nano-deploy/mobile/android/app/build/outputs/apk/debug/app-debug.apk
```
