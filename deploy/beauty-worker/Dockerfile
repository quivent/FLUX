# Model-free FLUX Beauty worker. Weights are hydrated from R2 into /models at runtime.
FROM nvidia/cuda:12.9.1-cudnn-runtime-ubuntu24.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip python3-venv git curl rclone && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN python3 -m pip install --break-system-packages --index-url https://download.pytorch.org/whl/cu128 torch torchvision && \
    python3 -m pip install --break-system-packages -r requirements.txt
COPY worker.py flux_paths.py flux_ane.py ./
COPY deploy/beauty-worker/models.manifest.json /etc/aons/models.manifest.json
COPY deploy/beauty-worker/bootstrap.py /usr/local/bin/aons-beauty-bootstrap
RUN chmod 755 /usr/local/bin/aons-beauty-bootstrap
VOLUME ["/models", "/outputs", "/state"]
ENTRYPOINT ["aons-beauty-bootstrap"]
