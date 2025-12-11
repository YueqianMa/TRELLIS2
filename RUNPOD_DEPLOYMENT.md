# TRELLIS RunPod Serverless 部署指南

## 概述

本指南说明如何将 TRELLIS Image-to-3D 管道部署到 RunPod Serverless。

## 文件结构

```
TRELLIS2/
├── rp_handler.py          # RunPod serverless handler (主入口)
├── Dockerfile             # Docker 构建配置
├── docker-compose.yml     # 本地测试配置
├── requirements.txt       # Python 依赖列表
├── test_input.json        # 测试输入示例
├── .env.example           # 环境变量模板
├── trellis/               # TRELLIS 核心代码
└── configs/               # 模型配置文件
```

## 本地测试

### 1. 使用 Docker Compose

```bash
# 构建镜像
docker-compose build

# 运行容器
docker-compose up

# 测试 API
curl -X POST http://localhost:8000/runsync \
  -H "Content-Type: application/json" \
  -d @test_input.json
```

### 2. 直接运行 (需要已配置环境)

```bash
# 安装依赖
pip install runpod

# 使用测试输入运行
python rp_handler.py --test_input test_input.json

# 或启动本地 API 服务器
python rp_handler.py --rp_serve_api --rp_api_host 0.0.0.0 --rp_api_port 8000
```

## 部署到 RunPod

### 步骤 1: 构建并推送 Docker 镜像

```bash
# 登录 Docker Hub (或其他 registry)
docker login

# 构建镜像
docker build -t yourusername/trellis-runpod:latest .

# 推送镜像
docker push yourusername/trellis-runpod:latest
```

### 步骤 2: 在 RunPod 创建 Serverless Endpoint

1. 登录 [RunPod Console](https://www.runpod.io/console/serverless)
2. 点击 **"New Endpoint"**
3. 配置:
   - **Name**: `trellis-image-to-3d`
   - **Docker Image**: `yourusername/trellis-runpod:latest`
   - **GPU Type**: 推荐 A40 或 A100 (需要 ~16GB VRAM)
   - **Active Workers**: 0 (按需启动)
   - **Max Workers**: 根据需求设置
   - **Idle Timeout**: 60 秒 (建议)
   - **Execution Timeout**: 300 秒 (5分钟，3D生成需要时间)

### 步骤 3: 配置环境变量

在 RunPod Endpoint 设置中添加环境变量:

```
STORAGE_BACKEND=base64
PRELOAD_MODEL=true
ATTN_BACKEND=xformers
SPCONV_ALGO=native
```

如果使用 S3 存储:

```
STORAGE_BACKEND=s3
AWS_ACCESS_KEY_ID=your_key
AWS_SECRET_ACCESS_KEY=your_secret
S3_BUCKET=your-bucket
AWS_REGION=us-east-1
```

## API 使用

### 请求格式

```json
{
  "input": {
    "images": ["base64_encoded_image_or_url"],
    "seed": 42,
    "randomize_seed": false,
    "generate_color": true,
    "generate_normal": false,
    "generate_model": true,
    "save_gaussian_ply": false,
    "return_no_background": false,
    "ss_guidance_strength": 7.5,
    "ss_sampling_steps": 12,
    "slat_guidance_strength": 3.0,
    "slat_sampling_steps": 12,
    "mesh_simplify": 0.95,
    "texture_size": 1024
  }
}
```

### 输入参数说明

| 参数 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `images` | array | **必填** | 图像列表 (base64 或 URL) |
| `seed` | int | 0 | 随机种子 |
| `randomize_seed` | bool | true | 是否随机化种子 |
| `generate_color` | bool | true | 生成彩色渲染视频 |
| `generate_normal` | bool | false | 生成法线贴图视频 |
| `generate_model` | bool | false | 生成 GLB 3D 模型 |
| `save_gaussian_ply` | bool | false | 保存高斯点云 PLY |
| `return_no_background` | bool | false | 返回去背景图像 |
| `ss_guidance_strength` | float | 7.5 | 第一阶段引导强度 (0-10) |
| `ss_sampling_steps` | int | 12 | 第一阶段采样步数 (1-50) |
| `slat_guidance_strength` | float | 3.0 | 第二阶段引导强度 (0-10) |
| `slat_sampling_steps` | int | 12 | 第二阶段采样步数 (1-50) |
| `mesh_simplify` | float | 0.95 | 网格简化比例 (0.9-0.98) |
| `texture_size` | int | 1024 | 纹理分辨率 (512-2048) |

### 调用 API

#### 同步调用 (等待结果)

```bash
curl -X POST "https://api.runpod.ai/v2/YOUR_ENDPOINT_ID/runsync" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "images": ["https://example.com/image.png"],
      "generate_model": true
    }
  }'
```

#### 异步调用 (提交任务)

```bash
# 提交任务
curl -X POST "https://api.runpod.ai/v2/YOUR_ENDPOINT_ID/run" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "images": ["https://example.com/image.png"],
      "generate_model": true
    }
  }'

# 返回: {"id": "job_id", "status": "IN_QUEUE"}

# 检查状态
curl "https://api.runpod.ai/v2/YOUR_ENDPOINT_ID/status/JOB_ID" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

### 响应格式

#### Base64 存储模式 (默认)

```json
{
  "seed": 42,
  "color_video": {
    "type": "base64",
    "data": "AAAA...",
    "mime_type": "video/mp4",
    "filename": "color.mp4"
  },
  "model_file": {
    "type": "base64",
    "data": "Z2xU...",
    "mime_type": "model/gltf-binary",
    "filename": "model.glb"
  }
}
```

#### S3 存储模式

```json
{
  "seed": 42,
  "color_video": {
    "type": "url",
    "url": "https://your-bucket.s3.amazonaws.com/trellis-outputs/uuid/color.mp4",
    "filename": "color.mp4"
  },
  "model_file": {
    "type": "url",
    "url": "https://your-bucket.s3.amazonaws.com/trellis-outputs/uuid/model.glb",
    "filename": "model.glb"
  }
}
```

## 存储后端配置

### Base64 (默认)

适用于小文件，文件以 base64 字符串返回。注意 RunPod 响应大小限制:
- `/run`: 10MB
- `/runsync`: 20MB

### S3/R2/MinIO

推荐用于生产环境，支持:
- AWS S3
- Cloudflare R2
- MinIO
- 其他 S3 兼容服务

配置示例 (Cloudflare R2):

```bash
STORAGE_BACKEND=s3
AWS_ACCESS_KEY_ID=your_r2_access_key
AWS_SECRET_ACCESS_KEY=your_r2_secret_key
S3_BUCKET=your-bucket-name
S3_ENDPOINT_URL=https://your-account-id.r2.cloudflarestorage.com
S3_PUBLIC_URL_BASE=https://your-public-domain.com
```

## 性能优化

### 减少冷启动时间

1. **烘焙模型到镜像**: 取消 Dockerfile 中的注释以预下载模型
2. **使用 Active Workers**: 保持至少 1 个 worker 活跃
3. **增加 Idle Timeout**: 设置更长的空闲超时

### GPU 选择

| GPU | VRAM | 推荐场景 |
|-----|------|---------|
| RTX 4090 | 24GB | 开发测试 |
| A40 | 48GB | 生产环境 |
| A100 40GB | 40GB | 高吞吐量 |
| A100 80GB | 80GB | 多请求并发 |

### 批处理

对于多图像输入，使用 `run_multi_image` 模式会自动启用:

```json
{
  "input": {
    "images": [
      "https://example.com/front.png",
      "https://example.com/side.png",
      "https://example.com/back.png"
    ]
  }
}
```

## 故障排除

### 常见问题

1. **CUDA 内存不足**: 减小 `texture_size` 或使用更大 VRAM 的 GPU
2. **超时错误**: 增加 Execution Timeout 设置
3. **模型加载失败**: 检查 Hugging Face 连接或使用烘焙模型

### 查看日志

在 RunPod Console 中查看 worker 日志以诊断问题。

## 与 Cog/Replicate 的差异

| 功能 | Cog/Replicate | RunPod Serverless |
|------|---------------|-------------------|
| 入口文件 | `predict.py` | `rp_handler.py` |
| 配置文件 | `cog.yaml` | `Dockerfile` |
| 输入方式 | 文件路径 | base64/URL |
| 输出方式 | 文件路径 | base64/URL |
| 模型加载 | `setup()` | 全局 `load_model()` |
| 推理函数 | `predict()` | `handler()` |
