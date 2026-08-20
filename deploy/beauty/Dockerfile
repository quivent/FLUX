# syntax=docker/dockerfile:1.7
#
# Beauty Studies stack — FLUX Tea surface + governor gateway + aesthetic jury.
#
# This file is the source of truth for the image that was previously only a
# 10.5 GiB tarball in R2 (containers/h200-beauty-studies-latest.tar.zst). That
# tarball was built 2026-08-20T14:51:54Z from a supervisord.conf that
# lowercased a case-sensitive HuggingFace repo id and over-subscribed VRAM;
# see deploy/beauty/beauty.manifest.json knownDefects.
#
# Two targets:
#   runtime  weights stay outside the image, on a mounted cache (small image,
#            cold first boot)
#   warm     weights are downloaded into the image at build time (large image,
#            instant boot, reproducible)
#
# Build the warm image:
#   docker buildx build -f deploy/beauty/Dockerfile --target warm \
#     --secret id=hf_token,env=HF_TOKEN -t h200-beauty-studies:latest .
#
# Model ids are ARGs, never literals in the process table, so a wrong id is a
# one-line build override instead of a rebuilt tarball.

ARG BASE_IMAGE=vllm/vllm-openai:cu129-nightly-65b7662d3fcb773afaf751ab29ac6960a0cf011d

# ──────────────────────────────────────────────────────────────────────────
FROM ${BASE_IMAGE} AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/models/hf \
    HF_HUB_ENABLE_HF_TRANSFER=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      supervisor curl git procps net-tools iproute2 jq \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
      diffusers transformers accelerate sentencepiece protobuf \
      safetensors huggingface_hub hf_transfer

# Model coordinates. Defaults track .gemstone/governor.json for the a100 card
# profile. Override at build or run time; nothing downstream hardcodes them.
ARG FLUX_MODEL=black-forest-labs/FLUX.1-dev
ARG GOVERNOR_MODEL=RedHatAI/gemma-4-31B-it-FP8-dynamic
ARG GOVERNOR_DRAFTER=google/gemma-4-31B-it-assistant
ARG VISION_MODEL=mistralai/Pixtral-12B-2409
# Deliberately empty: the shipped container named Qwen/Qwen3.8-27B-FP8, which
# does not match Qwen's published naming. Set to a verified id to enable.
ARG CODER_MODEL=""

ENV BEAUTY_FLUX_MODEL=${FLUX_MODEL} \
    BEAUTY_GOVERNOR_MODEL=${GOVERNOR_MODEL} \
    BEAUTY_GOVERNOR_DRAFTER=${GOVERNOR_DRAFTER} \
    BEAUTY_VISION_MODEL=${VISION_MODEL} \
    BEAUTY_CODER_MODEL=${CODER_MODEL}

# VRAM budget. The entrypoint refuses to start an over-subscribed stack rather
# than letting the FLUX worker OOM behind three resident vLLM servers.
ENV BEAUTY_GOVERNOR_UTIL=0.40 \
    BEAUTY_CODER_UTIL=0.0 \
    BEAUTY_VISION_UTIL=0.0 \
    BEAUTY_FLUX_RESERVE_GIB=35 \
    BEAUTY_GOVERNOR_CTX=131072 \
    BEAUTY_SPEC_TOKENS=3 \
    BEAUTY_MAX_NUM_SEQS=16 \
    BEAUTY_TEA_ADDR=127.0.0.1:7861

COPY deploy/beauty/entrypoint.sh /entrypoint.sh
COPY deploy/beauty/render-supervisord.sh /usr/local/bin/render-supervisord
COPY deploy/beauty/warm-models.sh /usr/local/bin/warm-models
RUN chmod +x /entrypoint.sh /usr/local/bin/render-supervisord /usr/local/bin/warm-models

# The Tea surface and its assets. flux tea check fails closed without these.
COPY apps/tea /root/FLUX/apps/tea
COPY web /root/FLUX/web

# Built binaries. Produced by `make go-build` (flux) and the gemstone repo.
COPY deploy/beauty/bin/flux /usr/local/bin/flux
COPY deploy/beauty/bin/gemstone /usr/local/bin/gemstone
RUN chmod +x /usr/local/bin/flux /usr/local/bin/gemstone

RUN mkdir -p /models/hf /root/Models/flux-output /root/renders /var/log

EXPOSE 7861 8000 8001 8002 9000
WORKDIR /root
ENTRYPOINT ["/entrypoint.sh"]
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]

# ──────────────────────────────────────────────────────────────────────────
# warm — same stack, weights resolved and cached at build time.
#
# Each model is its own RUN so a changed id re-downloads only that model's
# layer. Requires BuildKit; HF_TOKEN is passed as a secret and never lands in
# a layer or in the image history.
FROM runtime AS warm

RUN --mount=type=secret,id=hf_token,required=false \
    warm-models "${BEAUTY_FLUX_MODEL}"

RUN --mount=type=secret,id=hf_token,required=false \
    warm-models "${BEAUTY_GOVERNOR_MODEL}" "${BEAUTY_GOVERNOR_DRAFTER}"

RUN --mount=type=secret,id=hf_token,required=false \
    warm-models "${BEAUTY_VISION_MODEL}" "${BEAUTY_CODER_MODEL}"

ENV BEAUTY_MODELS_WARM=1
