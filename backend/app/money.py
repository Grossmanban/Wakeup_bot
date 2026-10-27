"""Деньги хранятся в целых копейках (ТЗ 0.1 п.7)."""


def rub(kop: int | None) -> str:
    if kop is None:
        return "—"
    sign = "−" if kop < 0 else ""
    kop = abs(kop)
    r, k = divmod(kop, 100)
    whole = f"{r:,}".replace(",", " ")
    return f"{sign}{whole},{k:02d} ₽" if k else f"{sign}{whole} ₽"
