class AppError(Exception):
    """Ошибка бизнес-логики с кодом для клиента и текстом для человека."""

    status_code = 400

    def __init__(self, code: str, message: str, status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        if status_code is not None:
            self.status_code = status_code


class Forbidden(AppError):
    status_code = 403


class NotFound(AppError):
    status_code = 404

    def __init__(self, message: str = "Не найдено"):
        super().__init__("not_found", message)


class Conflict(AppError):
    status_code = 409
