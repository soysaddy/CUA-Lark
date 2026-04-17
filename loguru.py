import logging
import sys


class _LoggerShim:
    def __init__(self) -> None:
        self._logger = logging.getLogger("cua_lark")
        if not self._logger.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
            self._logger.addHandler(handler)
        self._logger.setLevel(logging.INFO)

    def remove(self, *args, **kwargs) -> None:
        for handler in list(self._logger.handlers):
            self._logger.removeHandler(handler)

    def add(self, sink=None, level="INFO", format=None, **kwargs) -> None:
        handler = logging.StreamHandler(sink or sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        self._logger.addHandler(handler)
        self._logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))

    def debug(self, message: str, *args, **kwargs) -> None:
        self._logger.debug(message)

    def info(self, message: str, *args, **kwargs) -> None:
        self._logger.info(message)

    def warning(self, message: str, *args, **kwargs) -> None:
        self._logger.warning(message)

    def error(self, message: str, *args, **kwargs) -> None:
        self._logger.error(message)


logger = _LoggerShim()
