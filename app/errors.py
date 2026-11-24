"""Domain errors that map to HTTP status codes in one exception handler."""


class ReqTraceError(Exception):
    """Base class: carries a user-facing message and an HTTP status code."""

    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(ReqTraceError):
    status_code = 404


class ConflictError(ReqTraceError):
    status_code = 409


class BadRequestError(ReqTraceError):
    status_code = 400


class PayloadTooLargeError(ReqTraceError):
    status_code = 413


class UnsupportedMediaTypeError(ReqTraceError):
    status_code = 415


class ServiceUnavailableError(ReqTraceError):
    status_code = 503


class UnprocessableError(ReqTraceError):
    """A well-formed request that refers to something it cannot apply to."""

    status_code = 422
