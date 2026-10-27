// Локальное хранение удобств (тема, вкладка, фильтры). Может быть недоступно —
// тогда приложение просто работает со значениями по умолчанию.

export function load<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(`wakeup:${key}`);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function save(key: string, value: unknown) {
  try {
    localStorage.setItem(`wakeup:${key}`, JSON.stringify(value));
  } catch {
    /* хранилище недоступно */
  }
}
