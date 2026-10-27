import { useEffect, useState } from "react";
import { api, formatMsk, Me, ProductListItem, Task, TASK_STATUS_TITLES, TaskListItem, TaskStatus } from "../api";
import { Empty, ErrorBox, Loader, ReasonDialog, useLoad } from "../components/ui";
import { useNav } from "../nav";
import { load, save } from "../storage";

interface Filters {
  product: string;
  status: TaskStatus | "";
  q: string;
}

export function TasksAdmin() {
  const nav = useNav();
  const products = useLoad(() => api.get<ProductListItem[]>("/products"), []);
  const [f, setF] = useState<Filters>(() => load("tasks.filters", { product: "", status: "", q: "" }));
  const [q, setQ] = useState(f.q);
  useEffect(() => {
    const t = setTimeout(() => setF((x) => (x.q === q ? x : { ...x, q })), 300);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => save("tasks.filters", f), [f]);

  const list = useLoad(() => {
    const p = new URLSearchParams({ limit: "100" });
    if (f.product) p.set("product_id", f.product);
    if (f.status) p.set("status", f.status);
    if (f.q.trim()) p.set("q", f.q.trim());
    return api.get<{ items: TaskListItem[]; total: number }>(`/tasks?${p}`);
  }, [f.product, f.status, f.q]);

  return (
    <div>
      <h1>Задания</h1>
      <input placeholder="Поиск по ID задания" value={q} onChange={(e) => setQ(e.target.value)} style={{ marginBottom: 8 }} />
      <div className="row" style={{ flexWrap: "nowrap", marginBottom: 12 }}>
        <select value={f.product} onChange={(e) => setF({ ...f, product: e.target.value })}>
          <option value="">Все продукты</option>
          {products.data?.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value as TaskStatus | "" })}>
          <option value="">Все статусы</option>
          {(Object.keys(TASK_STATUS_TITLES) as TaskStatus[]).map((s) => (
            <option key={s} value={s}>
              {TASK_STATUS_TITLES[s]}
            </option>
          ))}
        </select>
      </div>
      <ErrorBox error={list.error} onRetry={list.reload} />
      {list.loading && !list.data && <Loader />}
      {list.data && list.data.items.length === 0 && <Empty>Заданий не найдено</Empty>}
      {list.data && list.data.total > list.data.items.length && (
        <p className="muted small">
          Показаны последние {list.data.items.length} из {list.data.total}. Уточните фильтр.
        </p>
      )}
      {list.data?.items.map((t) => (
        <button className="list-item" key={t.id} onClick={() => nav.push({ type: "task", id: t.id })}>
          <div className="row spread">
            <b>
              {t.product_name} · {t.external_id}
            </b>
            <span className="chip">{TASK_STATUS_TITLES[t.status]}</span>
          </div>
          <div className="muted small">
            {t.assignee_name ?? "Не назначено"} · единиц {t.units}
          </div>
        </button>
      ))}
    </div>
  );
}

interface TaskDetail {
  task: Task;
  product_name: string;
  assignee_name: string | null;
  events: { id: number; kind: string; stage: number | null; data: Record<string, unknown>; created_at: string; user_name: string | null }[];
}

export const EVENT_TITLES: Record<string, string> = {
  assigned: "Выдано",
  opened: "Открыта ссылка",
  found: "Нажато «Найдено»",
  stage_done: "Этап пройден",
  timer_reset: "Сброс таймера",
  photo_uploaded: "Загружено фото",
  ocr_attempt: "Проверка OCR",
  error_reported: "Сообщение об ошибке",
  completed: "Завершено",
  manual_review: "Передано на ручную проверку",
  manual_approved: "Ручная проверка: одобрено",
  manual_returned: "Ручная проверка: возвращено сотруднику",
  moderation: "Решение модерации",
  returned_to_pool: "Возвращено в пул",
  cancelled: "Отменено",
  paid: "Оплачено",
};

export function TaskAdminCard({ me, id }: { me: Me; id: number }) {
  const d = useLoad(() => api.get<TaskDetail>(`/tasks/${id}`), [id]);
  const [dialog, setDialog] = useState<"pool" | "cancel" | null>(null);
  if (d.loading && !d.data) return <Loader />;
  if (!d.data) return <ErrorBox error={d.error} onRetry={d.reload} />;
  const { task: t } = d.data;
  const canManage = me.is_owner || me.permissions.includes("tasks.manage");
  return (
    <div>
      <h1>
        {d.data.product_name} · {t.external_id}
      </h1>
      <div className="card kv">
        <span>Статус</span>
        <span>{TASK_STATUS_TITLES[t.status]}</span>
        <span>Исполнитель</span>
        <span>{d.data.assignee_name ?? "—"}</span>
        <span>Единиц</span>
        <span>{t.units}</span>
        <span>Ссылка</span>
        <span>{t.link ? <a href={t.link} target="_blank" rel="noreferrer">{t.link}</a> : "—"}</span>
        {t.expected_text && (
          <>
            <span>Ожидаемый текст</span>
            <span>{t.expected_text}</span>
          </>
        )}
        {t.manual_review_reason && (
          <>
            <span>Причина проверки</span>
            <span>{t.manual_review_reason}</span>
          </>
        )}
        {t.moderation_reason && (
          <>
            <span>Причина решения</span>
            <span>{t.moderation_reason}</span>
          </>
        )}
        {Object.entries(t.extra).map(([k, v]) => (
          <span key={k} style={{ display: "contents" }}>
            <span>{k}</span>
            <span>{v}</span>
          </span>
        ))}
      </div>
      {canManage && (t.status === "active" || t.status === "manual_review") && (
        <div className="row" style={{ marginBottom: 10 }}>
          <button onClick={() => setDialog("pool")}>Вернуть в пул</button>
          <button className="danger" onClick={() => setDialog("cancel")}>
            Отменить
          </button>
        </div>
      )}
      <h2>История</h2>
      {d.data.events.map((e) => (
        <div className="card small" key={e.id}>
          <div className="row spread">
            <b>
              {EVENT_TITLES[e.kind] ?? e.kind}
              {e.stage !== null ? ` · этап ${e.stage + 1}` : ""}
            </b>
            <span className="muted">{formatMsk(e.created_at)}</span>
          </div>
          {e.user_name && <div className="muted">{e.user_name}</div>}
          {Object.keys(e.data).length > 0 && <div className="muted">{JSON.stringify(e.data)}</div>}
        </div>
      ))}
      {dialog && (
        <ReasonDialog
          title={dialog === "pool" ? "Вернуть задание в общий пул" : "Отменить задание"}
          confirmText={dialog === "pool" ? "Вернуть" : "Отменить задание"}
          danger={dialog === "cancel"}
          reasonRequired={dialog === "cancel"}
          reasonLabel={dialog === "cancel" ? "Причина" : "Причина (необязательно)"}
          onClose={() => setDialog(null)}
          onConfirm={async (reason) => {
            await api.post(`/tasks/${id}/${dialog === "pool" ? "return-to-pool" : "cancel"}`, { reason: reason || null });
            d.reload();
          }}
        />
      )}
    </div>
  );
}
