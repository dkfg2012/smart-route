#!/usr/bin/env sh
set -eu

ollama pull "${OLLAMA_DEVICE_MODEL:-qwen3:1.5b}"
ollama pull nomic-embed-text

