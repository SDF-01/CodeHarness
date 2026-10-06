"""Errors the CLI can explain in one line."""


class HarnessError(Exception):
    """Base error for problems the CLI can explain."""


class ConfigError(HarnessError):
    """The configuration file or values cannot be used."""


class ModelError(HarnessError):
    """The model server could not complete a request."""


class ToolInputError(HarnessError):
    """A tool received arguments it cannot run."""


class PathEscape(HarnessError):
    """A tool path resolved outside the project root."""


class TurnStopped(HarnessError):
    """The user answered an approval with a sentence, so the turn ends."""
