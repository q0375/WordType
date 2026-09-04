import logging
import sys

from .config import get_settings


def setup_logging() -> None:
    s = get_settings()
    logging.basicConfig(
        level=s.log_level,
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
        stream=sys.stdout,
        force=True,
    )


logger = logging.getLogger("wordtype")
