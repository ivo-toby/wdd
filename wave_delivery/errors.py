"""Domain errors with stable command-line exit codes."""


class WaveDeliveryError(Exception):
    exit_code = 1


class ValidationError(WaveDeliveryError):
    exit_code = 2


class RevisionConflict(WaveDeliveryError):
    exit_code = 3


class LockUnavailable(WaveDeliveryError):
    exit_code = 4


class IllegalTransition(WaveDeliveryError):
    exit_code = 5
