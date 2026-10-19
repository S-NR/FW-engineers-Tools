"""Transport abstraction for future live capture (serial, USB-CDC, CAN, TCP).

Version 1 ships no hardware drivers. A transport delivers received bytes to a
callback; the GUI's ``MainWindow.ingest_packet(data, direction)`` is the
hook that decodes and records them. Note that serial transports deliver a byte
*stream* - a framer (start/end bytes or length-based) must split it into
packets before calling ``ingest_packet``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

__all__ = ["TransportError", "TransportInterface", "LoopbackTransport"]

RxCallback = Callable[[bytes], None]


class TransportError(IOError):
    pass


class TransportInterface(ABC):
    name: str = "transport"

    def __init__(self) -> None:
        self._rx_callback: RxCallback | None = None

    def set_rx_callback(self, callback: RxCallback | None) -> None:
        """Called (possibly from a worker thread) with each received chunk."""
        self._rx_callback = callback

    def _emit(self, data: bytes) -> None:
        if self._rx_callback and data:
            self._rx_callback(bytes(data))

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    @abstractmethod
    def write(self, data: bytes) -> None: ...


class LoopbackTransport(TransportInterface):
    """Echoes written data back as received data. Useful for tests and demos."""

    name = "loopback"

    def __init__(self) -> None:
        super().__init__()
        self._open = False

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def write(self, data: bytes) -> None:
        if not self._open:
            raise TransportError("Transport is not open")
        self._emit(data)
