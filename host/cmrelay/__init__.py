"""Install and maintain a chatmail relay from one file."""


class CmrelayError(Exception):
    """A refusal or failure whose text is the whole report to the operator."""
