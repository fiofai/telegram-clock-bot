#!/bin/bash

# 启动脚本 - 只运行主应用（照片服务器功能已整合）

# 确保环境变量已设置
if [ -z "$TOKEN" ]; then
    echo "错误: 未设置TOKEN环境变量"
    exit 1
fi

# 启动主应用
echo "启动主应用..."
python3 persistent_app.py
