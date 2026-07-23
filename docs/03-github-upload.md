# 第一次上传 GitHub

本仓库建议先设为 Private。原因是项目仍包含设备网络配置、车辆控制逻辑、模型接口和未完成的工程代码。

## 你先在网页上做

1. 登录 GitHub，点击右上角 + → New repository；
2. 仓库名可填 carmove 或 smart-connected-vehicle；
3. 选择 Private；
4. 不要勾选 Add a README file、.gitignore 或 License，因为本地已经准备好了；
5. 点击 Create repository；
6. 把新仓库页面地址发给我，例如 https://github.com/你的用户名/carmove.git。不要把密码、Token 或 SSH 私钥发给我。

## 我们在终端执行

    cd /home/nvidia/carmove
    git init
    git add .
    git status --short
    git diff --cached --stat
    git commit -m "chore: organize vehicle system and documentation"
    git branch -M main
    git remote add origin https://github.com/你的用户名/carmove.git
    git push -u origin main

在 git status 和 git diff --cached --stat 阶段先检查清单，确认没有 .env、模型权重、录像、rosbag、密码或大压缩包，再执行 commit/push。

如果 GitHub 要求登录，建议使用浏览器登录后的 Git Credential Manager 或 SSH；不要把 Personal Access Token 写进仓库文件、命令历史或聊天消息。
