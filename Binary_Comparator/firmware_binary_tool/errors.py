"""Exception types shared by all layers of the tool."""


class FirmwareToolError(Exception):
    """Base class for all errors raised deliberately by the tool."""


class FirmwareFormatError(FirmwareToolError):
    """A firmware file is malformed or does not match the requested format."""


class ConfigError(FirmwareToolError):
    """A region / expected-area configuration is invalid."""


class ComparisonCancelled(FirmwareToolError):
    """The user cancelled a running comparison."""
