import { Me } from "../api";
import { useNav } from "../nav";

export interface Section {
  id: string;
  title: string;
  hint: string;
}

/** Разделы управления, доступные пользователю. Сервер всё равно проверяет права. */
export function manageSections(me: Me): Section[] {
  const can = (p: string) => me.is_owner || me.permissions.includes(p);
  const lead = me.user.role === "coach" || me.user.role === "instructor";
  const s: Section[] = [];
  if (can("registrations.review") || can("name_changes.review"))
    s.push({ id: "requests", title: "Заявки", hint: "Регистрация и смена ФИО" });
  if (can("users.view") || lead) s.push({ id: "users", title: "Сотрудники", hint: "Профили, роли, права" });
  if (me.is_owner || can("tasks.manage") || can("tasks.import"))
    s.push({ id: "products", title: "Продукты", hint: "Настройки, ставки, типы заданий" });
  if (can("tasks.import")) s.push({ id: "import", title: "Импорт заданий", hint: "Загрузка из Excel" });
  if (can("tasks.manage")) s.push({ id: "tasks", title: "Задания", hint: "Пул, статусы, история" });
  if (can("moderation.review")) s.push({ id: "moderation", title: "Модерация", hint: "Допуск к оплате" });
  if (can("ocr.manual_review")) s.push({ id: "manual", title: "Ручная проверка", hint: "Фото и ошибки" });
  if (can("org.manage")) s.push({ id: "networks", title: "Сети", hint: "Оргструктура" });
  return s;
}

export function Manage({ me }: { me: Me }) {
  const nav = useNav();
  return (
    <div>
      <h1>Управление</h1>
      {manageSections(me).map((s) => (
        <button className="list-item" key={s.id} onClick={() => nav.push({ type: "section", id: s.id })}>
          <b>{s.title}</b>
          <div className="muted small">{s.hint}</div>
        </button>
      ))}
    </div>
  );
}
