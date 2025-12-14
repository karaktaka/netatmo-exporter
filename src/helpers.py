#!/usr/bin/env python3
# encoding=utf-8
"""
Helper utilities for the Netatmo exporter.
"""

import logging


def configure_logging(verbosity: int = 0, level: str = "INFO") -> logging.Logger:
    """
    Configure logging for the application and all modules.

    This function configures the root logger with a consistent format
    and level, which applies to all loggers in the application including
    those in imported modules.

    Args:
        verbosity: Verbosity level (0-3). Higher values increase verbosity.
                   0: Use the provided level parameter
                   1: WARNING
                   2: INFO
                   3: DEBUG
        level: Default logging level name (e.g., "INFO", "DEBUG", "WARNING")

    Returns:
        The configured root logger instance
    """
    level_map = {
        1: "WARNING",
        2: "INFO",
        3: "DEBUG",
    }

    # Use verbosity mapping if verbosity > 0, otherwise use provided level
    if verbosity > 0:
        level = level_map.get(verbosity, level)

    # Create formatter with consistent format across all loggers
    formatter = logging.Formatter(
        "%(asctime)s - %(module)s:%(lineno)d - %(levelname)s:%(message)s", datefmt="%d.%m.%Y %H:%M:%S"
    )

    # Configure root logger
    root_logger = logging.getLogger()

    # Clear any existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # Add stream handler with formatter
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    # Set the logging level
    root_logger.setLevel(level)
    root_logger.info(f"Logging configured with level: {level}")

    return root_logger
