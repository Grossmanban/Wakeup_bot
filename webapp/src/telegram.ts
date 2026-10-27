// Минимальная обёртка над Telegram WebApp API (telegram-web-app.js подключён в index.html).

interface TgBackButton {
  show(): void;
  hide(): void;
  onClick(cb: () => void): void;
  offClick(cb: () => void): void;
}

interface TgWebApp {
  initData: string;
  colorScheme: "light" | "dark";
  ready(): void;
  expand(): void;
  showConfirm?(message: string, cb: (ok: boolean) => void): void;
  openLink?(url: string): void;
  BackButton?: TgBackButton;
  HapticFeedback?: { notificationOccurred(t: "error" | "success" | "warning"): void };
  onEvent?(event: string, cb: () => void): void;
  version?: string;
  isVersionAtLeast?(v: string): boolean;
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TgWebApp };
  }
}

export const tg: TgWebApp | undefined = window.Telegram?.WebApp;

export function initData(): string {
  return tg?.initData ?? "";
}

export function telegramScheme(): "light" | "dark" {
  if (tg?.colorScheme) return tg.colorScheme;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function haptic(kind: "error" | "success" | "warning") {
  try {
    tg?.HapticFeedback?.notificationOccurred(kind);
  } catch {
    /* не критично */
  }
}

export function confirmAction(message: string): Promise<boolean> {
  const canNative = tg?.showConfirm && tg.isVersionAtLeast?.("6.2");
  if (canNative) {
    return new Promise((resolve) => tg!.showConfirm!(message, resolve));
  }
  return Promise.resolve(window.confirm(message));
}

export function backButton(): TgBackButton | undefined {
  return tg?.isVersionAtLeast?.("6.1") ? tg.BackButton : undefined;
}

export function openExternal(url: string) {
  if (tg?.openLink && tg.isVersionAtLeast?.("6.1")) tg.openLink(url);
  else window.open(url, "_blank", "noopener");
}
