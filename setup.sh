#!/bin/bash
set -euo pipefail

REPO_URL=${OPENASR_REPO:-https://github.com/QuintinShaw/openasr.git}
DIR=${OPENASR_DIR:-openasr}

if [ ! -d "$DIR/.git" ]; then
    git clone "$REPO_URL" "$DIR"
fi
cd "$DIR"
git submodule update --init --recursive

# Pick the ggml GPU backend that matches this platform's toolkit.
# Order: explicit override -> ROCm -> CUDA -> SYCL -> Vulkan -> CPU (OpenMP).
# On macOS the default build already enables Metal, so no feature is needed.
detect_backend() {
    if [ -n "${OPENASR_BACKEND:-}" ]; then
        case "$OPENASR_BACKEND" in
            hip|cuda|sycl|vulkan) printf '%s' "$OPENASR_BACKEND" ;;
            auto|"") : ;;
            *) echo "unknown OPENASR_BACKEND '$OPENASR_BACKEND' (expected hip|cuda|sycl|vulkan|auto)" >&2; exit 1 ;;
        esac
        return
    fi
    if command -v rocm-smi >/dev/null 2>&1 || [ -d /opt/rocm ]; then
        printf 'hip'
    elif command -v sycl-ls >/dev/null 2>&1 || [ -d /opt/intel/oneapi ]; then
        printf 'sycl'
    elif command -v nvidia-smi >/dev/null 2>&1; then
        printf 'cuda'
    elif command -v vulkaninfo >/dev/null 2>&1 || { command -v ldconfig >/dev/null 2>&1 && ldconfig -p 2>/dev/null | grep -q 'libvulkan'; }; then
        printf 'vulkan'
    else
        printf 'cpu'
    fi
}

backend=$(detect_backend)
case "$backend" in
    hip|cuda|sycl|vulkan)
        echo "Building with $backend backend..."
        cargo build --release -p openasr-cli --features "$backend"
        ;;
    *)
        echo "Building with CPU backend (no GPU toolkit detected)..."
        cargo build --release -p openasr-cli
        ;;
esac

echo "Done. Binary at $(pwd)/target/release/openasr"
