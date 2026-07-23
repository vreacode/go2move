# CarMove Android 网页壳

Android 端回到已经验证过的 WebView 壳：APK 负责保存 Dashboard 地址并加载网页控制台，实时视频、登录、采集、监控和底盘安全逻辑全部复用网页端实现。

这样手机端与浏览器端使用同一套 `MobileCaptureConsole` 和 WebRTC 视频实现，不在 APK 内重复实现视频协议。

## 构建

当前 Android 工程使用旧版 Gradle/Android Gradle Plugin，以兼容现有构建环境。Windows 上进入：

```text
apps/nano-deploy/mobile/android
```

首次构建先生成本机专属的 `local.properties`（这个文件不提交到 Git）：

```powershell
powershell -ExecutionPolicy Bypass -File .\create-local-properties.ps1
```

脚本优先读取 `ANDROID_SDK_ROOT` 或 `ANDROID_HOME`，否则使用 Android Studio 默认目录 `%LOCALAPPDATA%\Android\Sdk`。

执行：

```bat
gradlew.bat :app:assembleDebug
```

当前构建版本为 `0.5.0-debug`，不新增 Android WebRTC 依赖。

APK 位于：

```text
app\build\outputs\apk\debug\app-debug.apk
```

## 使用

1. 手机安装并打开 APK。
2. 先打开 Tailscale，登录同一个 tailnet。
3. 填写 Dashboard 地址，例如 `http://100.x.y.z:8080`。
4. 登录网页控制台即可使用采集、实时监控和控制功能。
