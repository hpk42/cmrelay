"""The one exception the command line reports as a message."""


class CmrelayError(Exception):
    """A refusal or failure whose text is the whole report to the operator."""
