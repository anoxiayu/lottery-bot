# 使用轻量级 Python 基础镜像
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 设置时区为上海 (关键，否则定时任务时间错乱)
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 创建数据目录
RUN mkdir -p /app/data

# 复制并安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制所有代码
COPY . .

# 暴露端口
EXPOSE 5000

# 启动应用
CMD ["python", "app.py"]