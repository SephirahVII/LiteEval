class NotFound(ValueError):
    pass


class Conflict(ValueError):
    pass


class LeaseLost(RuntimeError):
    pass
