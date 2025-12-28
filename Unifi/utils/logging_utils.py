import logging
import sys

def setup_logger(name="camera_app", level=logging.DEBUG):
    """
    Create or retrieve a logger with the specified name and level.
    Ensures each logger only has one handler.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    root_logger = logging.getLogger()
    if logger is not root_logger and root_logger.handlers:
        logger.propagate = True
        return logger

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s [%(threadName)s] [%(levelname)s] %(name)s: %(message)s",
        "%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger
