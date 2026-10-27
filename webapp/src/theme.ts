import { load, save } from "./storage";
import { telegramScheme, tg } from "./telegram";

export type ThemeChoice = "system" | "light" | "dark";

export function getTheme(): ThemeChoice {
  return load<ThemeChoice>("theme", "system");
}

export function applyTheme(choice: ThemeChoice = getTheme()) {
  const scheme = choice === "system" ? telegramScheme() : choice;
  document.documentElement.dataset.theme = scheme;
}

export function setTheme(choice: ThemeChoice) {
  save("theme", choice);
  applyTheme(choice);
}

export function watchTelegramTheme() {
  tg?.onEvent?.("themeChanged", () => {
    if (getTheme() === "system") applyTheme("system");
  });
}
