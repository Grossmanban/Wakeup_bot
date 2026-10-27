import { createContext, ReactNode, useContext, useEffect, useState } from "react";
import { backButton } from "./telegram";

// Экран, открытый поверх вкладки: карточка сотрудника, продукта, задания, раздел управления.
export type Screen =
  | { type: "section"; id: string }
  | { type: "user"; id: number }
  | { type: "product"; id: number }
  | { type: "task"; id: number }
  | { type: "myTask"; id: number }
  | { type: "custom"; key: string; render: () => ReactNode };

interface Nav {
  stack: Screen[];
  push: (s: Screen) => void;
  pop: () => void;
  reset: () => void;
}

const NavContext = createContext<Nav | null>(null);

export function NavProvider({ children }: { children: ReactNode }) {
  const [stack, setStack] = useState<Screen[]>([]);
  const nav: Nav = {
    stack,
    push: (s) => {
      setStack((prev) => [...prev, s]);
      window.scrollTo(0, 0);
    },
    pop: () => setStack((prev) => prev.slice(0, -1)),
    reset: () => setStack([]),
  };

  useEffect(() => {
    const bb = backButton();
    if (!bb) return;
    if (stack.length === 0) {
      bb.hide();
      return;
    }
    const onBack = () => setStack((prev) => prev.slice(0, -1));
    bb.show();
    bb.onClick(onBack);
    return () => bb.offClick(onBack);
  }, [stack.length]);

  return <NavContext.Provider value={nav}>{children}</NavContext.Provider>;
}

export function useNav(): Nav {
  const nav = useContext(NavContext);
  if (!nav) throw new Error("NavProvider missing");
  return nav;
}

/** Кнопка «Назад» для клиентов Telegram без нативной кнопки. */
export function FallbackBack() {
  const nav = useNav();
  if (backButton() || nav.stack.length === 0) return null;
  return (
    <button onClick={nav.pop} style={{ marginBottom: 8 }}>
      ← Назад
    </button>
  );
}
