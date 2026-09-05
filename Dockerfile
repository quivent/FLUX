# syntax=docker/dockerfile:1.7
# Tea surface for the Blackwell rig. GPU engines stay in their own containers.
#
#   docker build -t ghcr.io/quivent/tea:latest .
#   docker run --network host -v tea-outputs:/runs/flux-output ghcr.io/quivent/tea:latest

FROM golang:1.25-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod \
	go mod download
COPY . .
RUN --mount=type=cache,target=/go/pkg/mod \
	--mount=type=cache,target=/root/.cache/go-build \
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
	go build -trimpath -ldflags "-s -w" -o /out/flux ./cmd/flux

FROM alpine:3.21
RUN apk add --no-cache ca-certificates tzdata wget \
	&& adduser -D -u 65532 -h /data tea \
	&& mkdir -p /opt/flux /runs/flux-output
WORKDIR /opt/flux
COPY --from=build /out/flux /usr/local/bin/flux
COPY generate.py worker.py check_flux.py VERSION /opt/flux/
COPY apps /opt/flux/apps
COPY web /opt/flux/web
RUN chown -R tea:tea /opt/flux /runs /data
USER tea
ENV HOME=/data \
	OUT_DIR=/runs/flux-output \
	FLUX_BACKEND=cuda
EXPOSE 7861
HEALTHCHECK --interval=15s --timeout=3s --start-period=8s --retries=3 \
	CMD wget -qO- http://127.0.0.1:7861/tea.css >/dev/null || wget -qO- http://127.0.0.1:7861/ >/dev/null || exit 1
ENTRYPOINT ["/usr/local/bin/flux"]
CMD ["tea", "serve", "--addr", "0.0.0.0:7861", "--backend", "cuda", "--unsafe-no-auth"]
