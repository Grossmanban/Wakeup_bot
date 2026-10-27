import { useState } from "react";
import { Me } from "../api";
import { FallbackBack, NavProvider, Screen, useNav } from "../nav";
import { load, save } from "../storage";
import { Candidates, Trainees } from "./CandidatesAdmin";
import { MyFinance, Payouts } from "./Finance";
import { BonusRules, LazyRules } from "./FinanceRules";
import { GoalsAdmin } from "./Goals";
import { GroupsSettings, Membership } from "./Groups";
import { Import } from "./Import";
import { Manage, manageSections } from "./Manage";
import { MyTask } from "./MyTask";
import { Networks } from "./Networks";
import { ProductEditor } from "./ProductEditor";
import { Products } from "./Products";
import { Profile } from "./Profile";
import { Requests } from "./Requests";
import { ManualReview, Moderation } from "./Review";
import { Schedule } from "./Schedule";
import { StatsAdmin } from "./Stats";
import { DaySummary, Distribution, Points, SettingsScreen, ShiftRequests } from "./ScheduleAdmin";
import { TaskAdminCard, TasksAdmin } from "./TasksAdmin";
import { UserCard } from "./UserCard";
import { Users } from "./Users";
import { Work } from "./Work";

type Tab = "work" | "schedule" | "money" | "profile" | "manage";

export function Home(props: { me: Me; onReload: () => void }) {
  return (
    <NavProvider>
      <HomeInner {...props} />
    </NavProvider>
  );
}

function HomeInner({ me, onReload }: { me: Me; onReload: () => void }) {
  const nav = useNav();
  const sections = manageSections(me);
  const tabs: { id: Tab; title: string }[] = [];
  tabs.push({ id: "work", title: "Работа" });
  tabs.push({ id: "schedule", title: "График" });
  tabs.push({ id: "money", title: "Финансы" });
  tabs.push({ id: "profile", title: "Профиль" });
  if (sections.length) tabs.push({ id: "manage", title: "Управление" });

  const [tab, setTabState] = useState<Tab>(() => {
    const saved = load<Tab>("tab", "work");
    return tabs.some((t) => t.id === saved) ? saved : "work";
  });
  const setTab = (t: Tab) => {
    setTabState(t);
    nav.reset();
    save("tab", t);
  };
  const top = nav.stack[nav.stack.length - 1];

  return (
    <div>
      <div className="tabs">
        {tabs.map((t) => (
          <button key={t.id} className={tab === t.id && !top ? "active" : ""} onClick={() => setTab(t.id)}>
            {t.title}
          </button>
        ))}
      </div>
      <FallbackBack />
      {top ? (
        <ScreenView key={nav.stack.length} me={me} screen={top} />
      ) : (
        <>
          {tab === "work" && <Work me={me} />}
          {tab === "schedule" && <Schedule me={me} />}
          {tab === "money" && <MyFinance />}
          {tab === "profile" && <Profile me={me} onReload={onReload} />}
          {tab === "manage" && <Manage me={me} />}
        </>
      )}
    </div>
  );
}

function ScreenView({ me, screen }: { me: Me; screen: Screen }) {
  const nav = useNav();
  const openUser = (id: number) => nav.push({ type: "user", id });
  switch (screen.type) {
    case "user":
      return <UserCard me={me} userId={screen.id} onOpen={openUser} />;
    case "product":
      return <ProductEditor me={me} id={screen.id} />;
    case "task":
      return <TaskAdminCard me={me} id={screen.id} />;
    case "myTask":
      return <MyTask me={me} id={screen.id} />;
    case "custom":
      return <>{screen.render()}</>;
    case "section":
      switch (screen.id) {
        case "requests":
          return <Requests me={me} />;
        case "users":
          return <Users onOpen={openUser} />;
        case "products":
          return <Products me={me} />;
        case "import":
          return <Import />;
        case "tasks":
          return <TasksAdmin />;
        case "networks":
          return <Networks />;
        case "moderation":
          return <Moderation me={me} />;
        case "manual":
          return <ManualReview />;
        case "shiftRequests":
          return <ShiftRequests />;
        case "daySummary":
          return <DaySummary me={me} />;
        case "distribution":
          return <Distribution me={me} />;
        case "points":
          return <Points />;
        case "settings":
          return <SettingsScreen />;
        case "payouts":
          return <Payouts />;
        case "bonusRules":
          return <BonusRules />;
        case "lazyRules":
          return <LazyRules />;
        case "goals":
          return <GoalsAdmin />;
        case "stats":
          return <StatsAdmin me={me} />;
        case "membership":
          return <Membership me={me} />;
        case "groups":
          return <GroupsSettings />;
        case "candidates":
          return <Candidates me={me} />;
        case "trainees":
          return <Trainees />;
      }
  }
  return null;
}
