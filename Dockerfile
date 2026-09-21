FROM python:3.11-slim-bookworm

# 仅在构建阶段联网安装 Git；运行时 DockerRuntime 使用 --network none。
RUN apt-get update && apt-get install -y --no-install-recommends git coreutils && rm -rf /var/lib/apt/lists/*

# 安装与当前代码相同的 Runtime 包及最小测试依赖，供容器内测试命令使用。
WORKDIR /opt/patchflow
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir -e . 'pytest>=8.3,<9.0'

# 容器入口由 DockerRuntime 显式指定，镜像不内置宿主凭据或任务代码。
CMD ["python", "--version"]
