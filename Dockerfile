# ==============================================================================
# TRELLIS RunPod Serverless Dockerfile
# Migrated from Cog/Replicate deployment
# ==============================================================================

FROM yueqianma/cuda121:base

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV CUDA_HOME=/usr/local/cuda
ENV PATH="${CUDA_HOME}/bin:${PATH}"
ENV LD_LIBRARY_PATH="${CUDA_HOME}/lib64:${LD_LIBRARY_PATH}"
ENV TORCH_CUDA_ARCH_LIST="7.0;7.5;8.0;8.6;8.9;9.0"
ENV ATTN_BACKEND=flash-attn
ENV SPCONV_ALGO=native
ENV MAX_JOBS=6

# Install system packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.10 \
    python3.10-dev \
    python3-pip \
    python3.10-venv \
    # OpenGL/EGL libraries (runtime)
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    libegl1 \
    libglu1-mesa \
    libglfw3 \
    # OpenGL/EGL development libraries (for building nvdiffrast, etc.)
    libgl1-mesa-dev \
    libegl1-mesa-dev \
    libgles2-mesa-dev \
    libglu1-mesa-dev \
    libglfw3-dev \
    libxrender-dev \
    # Build tools
    ninja-build \
    git \
    cmake \
    build-essential \
    libglm-dev \
    wget \
    curl \
    ffmpeg \
    libboost-all-dev \
    libgoogle-glog-dev \
    libsparsehash-dev \
    # X11 libraries for open3d/pyvista
    libx11-6 \
    libx11-dev \
    libxcursor1 \
    libxrandr2 \
    libxinerama1 \
    libxi6 \
    libxxf86vm1 \
    libxkbcommon0 \
    xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.10 /usr/bin/python \
    && ln -sf /usr/bin/python3.10 /usr/bin/python3

# Upgrade pip
RUN python -m pip install --upgrade pip setuptools wheel packaging

# Install PyTorch with CUDA 12.1 support
RUN pip install --no-cache-dir \
    torch==2.1.2 \
    torchvision==0.16.2 \
    torchaudio==2.1.2 \
    --index-url https://download.pytorch.org/whl/cu121

# Install attention backends (keep torch 2.1.2, avoid downgrades)
RUN pip install --no-cache-dir \
    xformers==0.0.23.post1 \
    --index-url https://download.pytorch.org/whl/cu121 \
    --no-deps

# Install flash-attn (use pre-built wheel if available, otherwise build from source)
# Note: Building flash-attn requires significant memory and time
RUN pip install --no-cache-dir flash-attn==2.3.6 --no-build-isolation || \
    pip install --no-cache-dir flash-attn --no-build-isolation || \
    echo "Warning: flash-attn installation failed, will use xformers as fallback"

# Install spconv for sparse convolutions (prefer pinned wheel; fallback to source build)
ARG SPCONV_VERSION=2.3.8
RUN pip install --no-cache-dir spconv-cu121==${SPCONV_VERSION} || \
    pip install --no-cache-dir --no-build-isolation --no-binary spconv-cu121 spconv-cu121==${SPCONV_VERSION}

# Install core ML dependencies
RUN pip install --no-cache-dir \
    numpy==1.26.4 \
    scipy \
    pillow \
    imageio \
    imageio-ffmpeg \
    tqdm \
    easydict \
    safetensors \
    opencv-python-headless \
    plyfile \
    pygltflib \
    ipyevents

# Install background removal
RUN pip install --no-cache-dir rembg onnxruntime

# Install 3D processing libraries (install separately for better error tracking)
RUN pip install --no-cache-dir trimesh
RUN pip install --no-cache-dir open3d
RUN pip install --no-cache-dir xatlas
RUN pip install --no-cache-dir pyvista
RUN pip install --no-cache-dir pymeshfix
RUN pip install --no-cache-dir igraph

# Install transformers and huggingface
RUN pip install --no-cache-dir \
    transformers==4.37.2 \
    huggingface_hub==0.20.3

# Install utils3d
RUN pip install --no-cache-dir --no-build-isolation git+https://github.com/EasternJournalist/utils3d.git@9a4eb15e4021b67b12c460c7057d642626897ec8

# Install kaolin
RUN pip install --no-cache-dir --no-deps kaolin -f https://nvidia-kaolin.s3.us-east-2.amazonaws.com/torch-2.1.2_cu121.html && \
    pip install --no-cache-dir numpy==1.26.4

# Install warp (kaolin physics dependency)
RUN pip install --no-cache-dir warp-lang==1.10.1

# Install nvdiffrast (requires OpenGL/EGL dev libraries)
RUN git clone https://github.com/NVlabs/nvdiffrast.git /tmp/nvdiffrast && \
    cd /tmp/nvdiffrast && \
    pip install --no-cache-dir --no-build-isolation . && \
    cd / && rm -rf /tmp/nvdiffrast

# Install diffoctreerast for octree rendering
RUN git clone --recurse-submodules https://github.com/JeffreyXiang/diffoctreerast.git /tmp/diffoctreerast && \
    cd /tmp/diffoctreerast && \
    pip install --no-cache-dir --no-build-isolation . && \
    cd / && rm -rf /tmp/diffoctreerast

# Install diff-gaussian-rasterization from mip-splatting
RUN git clone https://github.com/autonomousvision/mip-splatting.git /tmp/mip-splatting && \
    cd /tmp/mip-splatting/submodules/diff-gaussian-rasterization && \
    pip install --no-cache-dir --no-build-isolation . && \
    cd / && rm -rf /tmp/mip-splatting

# Install RunPod SDK
RUN pip install --no-cache-dir runpod

# Install additional utilities (for S3 support, etc.)
RUN pip install --no-cache-dir \
    boto3 \
    requests

# Create workspace directory
WORKDIR /app

# Copy application code
COPY trellis/ /app/trellis/
COPY rp_handler.py /app/
COPY configs/ /app/configs/

# Pre-download models during build (optional, uncomment if you want to bake models into image)
# This increases image size but reduces cold start time
# RUN python -c "from trellis.pipelines import TrellisImageTo3DPipeline; TrellisImageTo3DPipeline.from_pretrained('Circova/TRELLIS-image-large')"

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD python -c "import torch; print(torch.cuda.is_available())" || exit 1

# Run the handler
CMD ["python", "-u", "rp_handler.py"]
