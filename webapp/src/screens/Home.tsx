import { useEffect, useState } from "react";
import { Me } from "../api";
import { load, save } from "../storage";
import { backButton } from "../telegram";
import { Networks } from "./Networks";
import { Profile } from "./Profile";
import { Requests } from "./Requests";
import { UserCard } from "./UserCard";
import { Users } from "./Users";

type Tab = "profile" | "requests" | "users" | "networks";

export function Home({ me, onReload }: { me: Me; onReload: () => void }) {
  const can = (p: string) => me.is_owner || me.permissions.includes(p);
  const tabs: { id: Tab; title: string }[] = [{ id: "profile", title: "Профиль" }];
  if (can("registrations.review") || can("name_changes.review")) tabs.push({ id: "requests", title: "Заявки" });
  if (can("users.view") || me.user.role === "coach" || me.user.role === "instructor")
    tabs.push({ id: "users", title: "Сотрудники" });
  if (can("org.manage")) tabs.push({ id: "networks", title: "Сети" });

  const [tab, setTabState] = useState<Tab>(() => {
    const saved = load<Tab>("tab", "profile");
    return tabs.some((t) => t.id === saved) ? saved : "profile";
  });
  const setTab = (t: Tab) => {
    setTabState(t);
    setStack([]);
    save("tab", t);
  };
  // Стек открытых карточек сотрудников — для кнопки «Назад» Telegram.
  const [stack, setStack] = useState<number[]>([]);
  const openUser = (id: number) => setStack((s) => [...s, id]);

  useEffect(() => {
    const bb = backButton();
    if (!bb) return;
    if (stack.length === 0) {
      bb.hide();
      return;
    }
    const onBack = () => setStack((s) => s.slice(0, -1));
    bb.show();
    bb.onClick(onBack);
    return () => bb.offClick(onBack);
  }, [stack.length]);

  const current = stack[stack.length - 1];

  return (
    <div>
      {tabs.length > 1 && (
        <div className="tabs">
          {tabs.map((t) => (
            <button key={t.id} className={tab === t.id && current === undefined ? "active" : ""} onClick={() => setTab(t.id)}>
              {t.title}
            </button>
          ))}
        </div>
      )}
      {current !== undefined ? (
        <>
          {!backButton() && (
            <button onClick={() => setStack((s) => s.slice(0, -1))} style={{ marginBottom: 8 }}>
              ← Назад
            </button>
          )}
          <UserCard key={current} me={me} userId={current} onOpen={openUser} />
        </>
      ) : (
        <>
          {/* Списки скрываются, а не размонтируются, чтобы при возврате сохранялись фильтры и прокрутка. */}
          {tab === "profile" && <Profile me={me} onReload={onReload} />}
          {tab === "requests" && <Requests me={me} />}
          {tab === "networks" && <Networks />}
        </>
      )}
      {tab === "users" && (
        <div style={{ display: current === undefined ? "block" : "none" }}>
          <Users onOpen={openUser} />
        </div>
      )}
    </div>
  );
}
