# RunPod Serverless 部署与测试指南

本指南基于镜像 `yueqianma/trellis-runpod:latest`。镜像入口使用 RunPod Serverless 任务模型（非常驻 HTTP），启动命令默认 `python -u rp_handler.py`。

## 1. 在 AWS 创建 S3 桶与凭证
1) 登录 AWS 控制台 → S3 → Create bucket  
   - Bucket name：如 `trellis-runpod`（全球唯一）  
   - Region：与 RunPod 使用的区域保持一致（常用 `us-east-1`）  
   - 其余保持默认（Block Public Access 保持开启）。  
2) 创建 IAM Policy（授予最小权限），JSON 参考：  
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": ["s3:ListBucket"],
         "Resource": "arn:aws:s3:::trellis-runpod"
       },
       {
         "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:PutObject"],
         "Resource": "arn:aws:s3:::trellis-runpod/*"
       }
     ]
   }
   ```
3) 创建 IAM User（Programmatic access），附加上面 Policy，生成 Access Key / Secret。保存好这两项，用于 RunPod 环境变量。

## 2. RunPod Serverless 端点创建
1) 在 RunPod 控制台创建 Serverless Endpoint：  
   - Container Image：`yueqianma/trellis-runpod:latest`（Public）  
   - GPU：选择具备 CUDA 的卡（如 A10/A100/L4），显存越大越好。  
   - Command：`python -u rp_handler.py`（无需 HTTP server）。  
   - Volume：可选；如要缓存 HF 模型，可挂载 `/root/.cache/huggingface`。
2) 环境变量（按需设置）：  
   - `STORAGE_BACKEND=s3`  
   - `S3_BUCKET=trellis-runpod`  
   - `AWS_ACCESS_KEY_ID=<你的 AK>`  
   - `AWS_SECRET_ACCESS_KEY=<你的 SK>`  
   - `AWS_REGION=us-east-1`（或你的桶区域）  
   - 可选：`S3_ENDPOINT_URL`（自定义 S3 兼容服务/R2/MinIO），`S3_PUBLIC_URL_BASE`（自定义外网访问域名）  
   - 其它可选：`ATTN_BACKEND=flash-attn` 或 `xformers`；`SPCONV_ALGO=native`；`PRELOAD_MODEL=true`（默认）。

## 3. 任务请求示例（runsync）
```bash
curl -X POST https://api.runpod.ai/v2/<ENDPOINT_ID>/runsync \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <RUNPOD_API_KEY>" \
  -d '{
        "input": {
          "images": ["https://your-s3-presigned-url/test.png"],
          "seed": 0,
          "randomize_seed": true,
          "generate_color": true,
          "generate_normal": false,
          "generate_model": true,
          "save_gaussian_ply": true,
          "return_no_background": true,
          "ss_guidance_strength": 7.5,
          "ss_sampling_steps": 12,
          "slat_guidance_strength": 3.0,
          "slat_sampling_steps": 12,
          "mesh_simplify": 0.95,
          "texture_size": 1024
        }
      }'
```
- `images` 支持 HTTP/HTTPS URL、data URL、或 base64；S3 私有对象请使用预签名 URL。  
- 返回内容：`color_video`/`normal_video`/`combined_video`、`model_file`(glb)、`gaussian_ply`、`no_background_images` 等，视参数而定。输出存储优先 S3，失败回退 base64。

## 4. 本地快速检查（无 GPU 的机器）
在无 GPU 环境只能做轻量 import 检查，无法跑推理：  
```bash
docker run --rm trellis-runpod \
  python - <<'PY'
import torch
print("torch", torch.__version__, "cuda?", torch.cuda.is_available())
PY
```
完整推理请在 RunPod GPU 端点上测试。

## 5. 其他注意
- Endpoint 并非常驻 HTTP，按任务调用计费。  
- 如果改用 base64 输出作为兜底，无需额外配置；S3 失败会自动回退。  
- 如需自定义公开 URL，可设置 `S3_PUBLIC_URL_BASE`（形如 `https://cdn.example.com`），生成的返回 URL 会用该前缀拼接对象键。  
- HF 模型会在首次调用下载，建议预热或挂载缓存卷以减少冷启动。  
