from __future__ import annotations

import logging

from classifier import _get_routellm_controller, _get_semantic_router, resolve_encoder_model_path
from token_estimator import count_tokens_text


logging.basicConfig(level=logging.INFO)
log = logging.getLogger("warmup")


def main() -> None:
    log.info("resolving semantic encoder with ModelScope support")
    log.info("encoder path: %s", resolve_encoder_model_path())

    log.info("warming semantic-router")
    _get_semantic_router().classify("warmup query")
    log.info("semantic-router ready")

    log.info("warming routellm mf router")
    _get_routellm_controller().classify("warmup query")
    log.info("routellm ready")

    count_tokens_text("warmup")
    log.info("tokenizer ready")


if __name__ == "__main__":
    main()
