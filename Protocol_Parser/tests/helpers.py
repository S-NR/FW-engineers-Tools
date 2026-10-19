"""Shared test fixtures."""
from __future__ import annotations

import tests  # noqa: F401  (sets sys.path)
from core.protocol import ProtocolDefinition
from protocols.protocol_manager import EXAMPLES_DIR, ProtocolManager

EXAMPLE_PACKET_HEX = "02 01 08 00 67 65 74 20 64 69 61 67 DD 1F 03"
EXAMPLE_PACKET = bytes.fromhex(EXAMPLE_PACKET_HEX.replace(" ", ""))


def load_example(name: str) -> ProtocolDefinition:
    manager = ProtocolManager(search_dirs=[EXAMPLES_DIR])
    manager.reload()
    assert not manager.load_errors, manager.load_errors
    proto = manager.get(name)
    assert proto is not None, f"example protocol {name!r} missing"
    return proto


def dextens() -> ProtocolDefinition:
    return load_example("Dextens USB Protocol")


def proto_from(fields: list[dict], **kw) -> ProtocolDefinition:
    return ProtocolDefinition.from_dict({"name": kw.pop("name", "Test"), "fields": fields, **kw})
