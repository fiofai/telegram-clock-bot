#!/bin/bash

# 启动脚本 - 同时运行主应用和照片服务器

# 确保环境变量已设置
if [ -z "$TOKEN" ]; then
    echo "错误: 未设置TOKEN环境变量"
    exit 1
fi

# 可选: 设置照片服务器URL (如果未设置，将使用默认值)
if [ -z "$PHOTO_SERVER_URL" ]; then
    export PHOTO_SERVER_URL="http://127.0.0.1:5001"
    echo "未设置PHOTO_SERVER_URL，使用默认值: $PHOTO_SERVER_URL"
fi

# 启动照片服务器 (后台运行)
echo "启动照片服务器..."
python3 photo_server.py &
PHOTO_SERVER_PID=$!
echo "照片服务器已启动，PID: $PHOTO_SERVER_PID"

# 等待照片服务器启动
sleep 2

# 启动主应用
echo "启动主应用..."
python3 persistent_app.py

# 如果主应用退出，也关闭照片服务器
echo "主应用已退出，关闭照片服务器..."
kill $PHOTO_SERVER_PID
