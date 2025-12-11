"""
RunPod Serverless Handler for TRELLIS Image-to-3D Pipeline
Migrated from Cog/Replicate deployment
"""

import runpod
import os
import base64
import tempfile
import logging
import uuid
from io import BytesIO

# Set environment variables before importing torch
os.environ['SPCONV_ALGO'] = 'native'
os.environ['ATTN_BACKEND'] = os.environ.get('ATTN_BACKEND', 'xformers')

import torch
import numpy as np
import imageio
from PIL import Image
import requests

# Import TRELLIS pipeline
from trellis.pipelines import TrellisImageTo3DPipeline
from trellis.utils import render_utils, postprocessing_utils

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
MAX_SEED = np.iinfo(np.int32).max

# Global pipeline variable for model caching
pipeline = None


# ============================================================================
# Storage Backends
# ============================================================================

class StorageBackend:
    """Base class for storage backends"""
    def upload(self, file_path: str, filename: str) -> str:
        raise NotImplementedError


class Base64Storage(StorageBackend):
    """Return files as base64 encoded strings (for small files)"""
    def upload(self, file_path: str, filename: str) -> dict:
        with open(file_path, 'rb') as f:
            data = base64.b64encode(f.read()).decode('utf-8')
        # Determine mime type
        ext = filename.split('.')[-1].lower()
        mime_types = {
            'mp4': 'video/mp4',
            'glb': 'model/gltf-binary',
            'ply': 'application/octet-stream',
            'png': 'image/png',
        }
        mime_type = mime_types.get(ext, 'application/octet-stream')
        return {
            "type": "base64",
            "data": data,
            "mime_type": mime_type,
            "filename": filename
        }


class S3Storage(StorageBackend):
    """Upload files to S3-compatible storage (AWS S3, Cloudflare R2, MinIO, etc.)"""
    def __init__(self):
        import boto3
        self.bucket = os.environ.get('S3_BUCKET')
        self.endpoint_url = os.environ.get('S3_ENDPOINT_URL')  # For R2/MinIO
        self.public_url_base = os.environ.get('S3_PUBLIC_URL_BASE', '')

        client_kwargs = {
            'aws_access_key_id': os.environ.get('AWS_ACCESS_KEY_ID'),
            'aws_secret_access_key': os.environ.get('AWS_SECRET_ACCESS_KEY'),
            'region_name': os.environ.get('AWS_REGION', 'us-east-1'),
        }
        if self.endpoint_url:
            client_kwargs['endpoint_url'] = self.endpoint_url

        self.client = boto3.client('s3', **client_kwargs)

    def upload(self, file_path: str, filename: str) -> dict:
        key = f"trellis-outputs/{uuid.uuid4()}/{filename}"
        self.client.upload_file(file_path, self.bucket, key)

        if self.public_url_base:
            url = f"{self.public_url_base.rstrip('/')}/{key}"
        else:
            url = f"https://{self.bucket}.s3.amazonaws.com/{key}"

        return {
            "type": "url",
            "url": url,
            "filename": filename
        }


class RunPodStorage(StorageBackend):
    """Use RunPod's built-in blob storage"""
    def upload(self, file_path: str, filename: str) -> dict:
        # RunPod provides presigned URLs for file uploads
        # This requires RUNPOD_API_KEY environment variable
        try:
            with open(file_path, 'rb') as f:
                file_data = f.read()

            # Upload using runpod's upload utility if available
            if hasattr(runpod, 'upload_file'):
                url = runpod.upload_file(file_path)
                return {
                    "type": "url",
                    "url": url,
                    "filename": filename
                }
            else:
                # Fallback to base64 if runpod upload not available
                return Base64Storage().upload(file_path, filename)
        except Exception as e:
            logger.warning(f"RunPod storage failed, falling back to base64: {e}")
            return Base64Storage().upload(file_path, filename)


def get_storage_backend() -> StorageBackend:
    """Get the appropriate storage backend based on environment configuration"""
    storage_type = os.environ.get('STORAGE_BACKEND', 'base64').lower()

    if storage_type == 's3' and os.environ.get('S3_BUCKET'):
        logger.info("Using S3 storage backend")
        return S3Storage()
    elif storage_type == 'runpod':
        logger.info("Using RunPod storage backend")
        return RunPodStorage()
    else:
        logger.info("Using base64 storage backend")
        return Base64Storage()


# ============================================================================
# Model Loading
# ============================================================================

def load_model():
    """
    Load the TRELLIS pipeline model.
    This is called once and cached globally for efficiency.
    Equivalent to Cog's setup() method.
    """
    global pipeline

    if pipeline is not None:
        logger.info("Model already loaded, reusing cached pipeline")
        return pipeline

    logger.info("Loading TRELLIS pipeline...")
    pipeline = TrellisImageTo3DPipeline.from_pretrained("Circova/TRELLIS-image-large")
    pipeline.cuda()

    # Preload rembg for background removal
    logger.info("Preloading rembg...")
    try:
        pipeline.preprocess_image(Image.fromarray(np.zeros((512, 512, 3), dtype=np.uint8)))
    except Exception as e:
        logger.warning(f"Rembg preload warning (usually fine): {e}")

    logger.info("Model loading complete!")
    return pipeline


# ============================================================================
# Image Input Processing
# ============================================================================

def decode_image(image_input) -> Image.Image:
    """
    Decode image from various input formats.
    Supports: base64 string, URL, or raw bytes
    """
    if isinstance(image_input, str):
        if image_input.startswith(('http://', 'https://')):
            # Download from URL
            logger.info(f"Downloading image from URL: {image_input[:50]}...")
            response = requests.get(image_input, timeout=30)
            response.raise_for_status()
            return Image.open(BytesIO(response.content))
        elif image_input.startswith('data:'):
            # Data URL format: data:image/png;base64,xxx
            header, data = image_input.split(',', 1)
            image_data = base64.b64decode(data)
            return Image.open(BytesIO(image_data))
        else:
            # Assume base64 encoded
            image_data = base64.b64decode(image_input)
            return Image.open(BytesIO(image_data))
    elif isinstance(image_input, bytes):
        return Image.open(BytesIO(image_input))
    else:
        raise ValueError(f"Unsupported image input type: {type(image_input)}")


# ============================================================================
# Main Handler
# ============================================================================

def handler(job):
    """
    RunPod serverless handler function.
    Equivalent to Cog's predict() method.

    Input schema:
    {
        "input": {
            "images": ["base64_or_url", ...],  # Required: list of images
            "seed": 0,                          # Optional: random seed
            "randomize_seed": true,             # Optional: randomize seed
            "generate_color": true,             # Optional: generate color video
            "generate_normal": false,           # Optional: generate normal video
            "generate_model": false,            # Optional: generate GLB file
            "save_gaussian_ply": false,         # Optional: save Gaussian PLY
            "return_no_background": false,      # Optional: return preprocessed images
            "ss_guidance_strength": 7.5,        # Optional: stage 1 guidance
            "ss_sampling_steps": 12,            # Optional: stage 1 steps
            "slat_guidance_strength": 3.0,      # Optional: stage 2 guidance
            "slat_sampling_steps": 12,          # Optional: stage 2 steps
            "mesh_simplify": 0.95,              # Optional: mesh simplification
            "texture_size": 1024                # Optional: texture resolution
        }
    }
    """
    job_input = job["input"]
    job_id = job.get("id", str(uuid.uuid4()))

    logger.info(f"Processing job: {job_id}")

    # Load model (cached after first call)
    pipe = load_model()
    storage = get_storage_backend()

    # Create temporary directory for outputs
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            # ================================================================
            # Parse Input Parameters
            # ================================================================
            images_input = job_input.get("images", [])
            if not images_input:
                return {"error": "No images provided. 'images' field is required."}

            if isinstance(images_input, str):
                images_input = [images_input]

            seed = job_input.get("seed", 0)
            randomize_seed = job_input.get("randomize_seed", True)
            generate_color = job_input.get("generate_color", True)
            generate_normal = job_input.get("generate_normal", False)
            generate_model = job_input.get("generate_model", False)
            save_gaussian_ply = job_input.get("save_gaussian_ply", False)
            return_no_background = job_input.get("return_no_background", False)

            # Sampling parameters with validation
            ss_guidance_strength = max(0.0, min(10.0, job_input.get("ss_guidance_strength", 7.5)))
            ss_sampling_steps = max(1, min(50, job_input.get("ss_sampling_steps", 12)))
            slat_guidance_strength = max(0.0, min(10.0, job_input.get("slat_guidance_strength", 3.0)))
            slat_sampling_steps = max(1, min(50, job_input.get("slat_sampling_steps", 12)))
            mesh_simplify = max(0.9, min(0.98, job_input.get("mesh_simplify", 0.95)))
            texture_size = max(512, min(2048, job_input.get("texture_size", 1024)))

            # ================================================================
            # Process Images
            # ================================================================
            logger.info(f"Processing {len(images_input)} input image(s)...")
            input_images = [decode_image(img) for img in images_input]
            processed_images = [pipe.preprocess_image(img) for img in input_images]

            # ================================================================
            # Handle No Background Images
            # ================================================================
            output = {}

            if return_no_background:
                no_bg_results = []
                for idx, processed_image in enumerate(processed_images):
                    no_bg_path = os.path.join(temp_dir, f"no_background_{idx}.png")
                    processed_image.save(no_bg_path)
                    result = storage.upload(no_bg_path, f"no_background_{idx}.png")
                    no_bg_results.append(result)
                output["no_background_images"] = no_bg_results
                logger.info("Saved images without background")

            # ================================================================
            # Handle Seed
            # ================================================================
            if randomize_seed:
                seed = np.random.randint(0, MAX_SEED)
            logger.info(f"Using seed: {seed}")
            output["seed"] = seed

            # ================================================================
            # Run TRELLIS Pipeline
            # ================================================================
            logger.info("Running TRELLIS pipeline...")

            if len(processed_images) > 1:
                outputs = pipe.run_multi_image(
                    processed_images,
                    seed=seed,
                    formats=["gaussian", "mesh"],
                    preprocess_image=False,
                    sparse_structure_sampler_params={
                        "steps": ss_sampling_steps,
                        "cfg_strength": ss_guidance_strength,
                    },
                    slat_sampler_params={
                        "steps": slat_sampling_steps,
                        "cfg_strength": slat_guidance_strength,
                    }
                )
            else:
                outputs = pipe.run(
                    processed_images[0],
                    seed=seed,
                    formats=["gaussian", "mesh"],
                    preprocess_image=False,
                    sparse_structure_sampler_params={
                        "steps": ss_sampling_steps,
                        "cfg_strength": ss_guidance_strength,
                    },
                    slat_sampler_params={
                        "steps": slat_sampling_steps,
                        "cfg_strength": slat_guidance_strength,
                    }
                )

            logger.info(f"Pipeline complete! Output formats: {list(outputs.keys())}")

            # ================================================================
            # Render Videos
            # ================================================================
            if generate_color or generate_normal:
                logger.info("Starting video rendering...")

                if generate_color and generate_normal:
                    # Generate both and combine side by side
                    logger.info("Generating combined color + normal video...")
                    color_renders = render_utils.render_video(outputs['gaussian'][0], num_frames=120)
                    normal_renders = render_utils.render_video(outputs['mesh'][0], num_frames=120)

                    if 'color' in color_renders and 'normal' in normal_renders:
                        color_video = color_renders['color']
                        normal_video = normal_renders['normal']
                        combined_video = [
                            np.concatenate([color_video[i], normal_video[i]], axis=1)
                            for i in range(len(color_video))
                        ]

                        combined_path = os.path.join(temp_dir, "combined.mp4")
                        imageio.mimsave(combined_path, combined_video, fps=15)
                        output["combined_video"] = storage.upload(combined_path, "combined.mp4")
                        logger.info("Combined video generated successfully")
                else:
                    if generate_color:
                        logger.info("Generating color video...")
                        color_renders = render_utils.render_video(outputs['gaussian'][0], num_frames=120)
                        if 'color' in color_renders:
                            color_path = os.path.join(temp_dir, "color.mp4")
                            imageio.mimsave(color_path, color_renders['color'], fps=15)
                            output["color_video"] = storage.upload(color_path, "color.mp4")
                            logger.info("Color video generated successfully")

                    if generate_normal:
                        logger.info("Generating normal video...")
                        normal_renders = render_utils.render_video(outputs['mesh'][0], num_frames=120)
                        if 'normal' in normal_renders:
                            normal_path = os.path.join(temp_dir, "normal.mp4")
                            imageio.mimsave(normal_path, normal_renders['normal'], fps=15)
                            output["normal_video"] = storage.upload(normal_path, "normal.mp4")
                            logger.info("Normal video generated successfully")

            # ================================================================
            # Generate GLB Model
            # ================================================================
            if generate_model:
                logger.info("Generating GLB model...")
                glb = postprocessing_utils.to_glb(
                    outputs['gaussian'][0],
                    outputs['mesh'][0],
                    simplify=mesh_simplify,
                    texture_size=texture_size,
                    verbose=False
                )
                model_path = os.path.join(temp_dir, "model.glb")
                glb.export(model_path)
                output["model_file"] = storage.upload(model_path, "model.glb")
                logger.info("GLB model generated successfully")

            # ================================================================
            # Save Gaussian PLY
            # ================================================================
            if save_gaussian_ply:
                logger.info("Saving Gaussian PLY...")
                gaussian_path = os.path.join(temp_dir, "gaussian.ply")
                outputs['gaussian'][0].save_ply(gaussian_path)
                output["gaussian_ply"] = storage.upload(gaussian_path, "gaussian.ply")
                logger.info("Gaussian PLY saved successfully")

            logger.info(f"Job {job_id} completed successfully!")
            return output

        except Exception as e:
            logger.error(f"Error processing job {job_id}: {str(e)}")
            import traceback
            traceback.print_exc()
            return {"error": str(e)}


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    logger.info("Starting TRELLIS RunPod Serverless Worker...")

    # Preload model on startup for faster cold starts
    if os.environ.get("PRELOAD_MODEL", "true").lower() == "true":
        logger.info("Preloading model...")
        load_model()

    runpod.serverless.start({"handler": handler})
