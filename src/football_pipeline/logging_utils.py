"""Small logging helper so every entry point logs consistently."""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def configure(verbose: bool = False) -> None:
    """Configure root logging once. Safe to call repeatedly."""
    global _CONFIGURED
    if _CONFIGURED:
        logging.getLogger().setLevel(logging.DEBUG if verbose else logging.INFO)
        return
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
