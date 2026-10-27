import { useState } from "react";
import { api, Me, MyProduct, newIdempotencyKey, rub, TASK_STATUS_TITLES, TaskListItem } from "../api";
import { Empty, ErrorBox, Loader, useAction, useLoad } from "../components/ui";
import { useNav } from "../nav";

export function Work({ me }: { me: Me }) {
  const nav = useNav();
  const products = useLoad(() => api.get<MyProduct[]>("/my/products"), []);
  const tasks = useLoad(() => api.get<TaskListItem[]>("/my/tasks"), []);
  const action = useAction();
  const [takingId, setTakingId] = useState<number | null>(null);

  const reload = () => {
    products.reload();
    tasks.reload();
  };

  return (
    <div>
      {!me.has_product_access && me.product_access_message && (
        <div className="notice">{me.product_access_message}</div>
      )}

      <h1>Мои задания</h1>
      <ErrorBox error={tasks.error} onRetry={tasks.reload} />
      {tasks.loading && !tasks.data && <Loader />}
      {tasks.data && tasks.data.length === 0 && <Empty>Активных заданий нет</Empty>}
      {tasks.data?.map((t) => (
        <button className="list-item" key={t.id} onClick={() => nav.push({ type: "myTask", id: t.id })}>
          <div className="row spread">
            <b>{t.product_name}</b>
            <span className={`chip ${t.status === "manual_review" ? "warn" : ""}`}>
              {TASK_STATUS_TITLES[t.status]}
            </span>
          </div>
          <div className="muted small">
            Задание {t.external_id} · единиц: {t.units}
          </div>
        </button>
      ))}

      <h1>Продукты</h1>
      <ErrorBox error={products.error} onRetry={products.reload} />
      <ErrorBox error={action.error} />
      {products.loading && !products.data && <Loader />}
      {products.data && products.data.length === 0 && me.has_product_access && <Empty>Нет доступных продуктов</Empty>}
      {products.data?.map((p) => (
        <div className="card stack" key={p.id}>
          <div className="row spread">
            <b>{p.name}</b>
            <span className="muted small">{p.rate_kop !== null ? `${rub(p.rate_kop)} за единицу` : ""}</span>
          </div>
          {p.description && <div className="small">{p.description}</div>}
          {p.rate_error && <div className="error small">{p.rate_error}</div>}
          <div className="muted small">
            {p.status === "active" ? `Свободно заданий: ${p.pool}` : "Продукт временно недоступен"} · активных{" "}
            {p.active} из {p.active_task_limit}
          </div>
          {(p.links.length > 0 || p.materials.length > 0) && (
            <div className="small">
              {[...p.links, ...p.materials].map((l, i) => (
                <span key={i}>
                  {i > 0 && " · "}
                  <a href={l.url} target="_blank" rel="noreferrer">
                    {l.title}
                  </a>
                </span>
              ))}
            </div>
          )}
          {p.instructions && (
            <details className="small">
              <summary>Инструкция</summary>
              <div style={{ whiteSpace: "pre-wrap" }}>{p.instructions}</div>
            </details>
          )}
          <button
            className="primary"
            disabled={
              action.busy || p.status !== "active" || p.pool === 0 || p.active >= p.active_task_limit || !!p.rate_error
            }
            onClick={async () => {
              setTakingId(p.id);
              const r = await action.run(() =>
                api.post<TaskListItem>(`/my/products/${p.id}/take`, undefined, {
                  "Idempotency-Key": newIdempotencyKey(),
                }),
              );
              setTakingId(null);
              reload();
              if (r.ok) nav.push({ type: "myTask", id: r.value.id });
            }}
          >
            {takingId === p.id ? "Получение…" : "Получить задание"}
          </button>
        </div>
      ))}
    </div>
  );
}
