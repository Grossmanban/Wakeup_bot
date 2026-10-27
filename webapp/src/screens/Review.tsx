import { useState } from "react";
import { api, formatMsk, Me, ProductListItem, Task } from "../api";
import { AuthImage } from "../components/photo";
import { Empty, ErrorBox, Loader, ReasonDialog, useAction, useLoad } from "../components/ui";
import type { TaskPhoto } from "./MyTask";

interface ReviewItem {
  task: Task;
  product_name: string;
  assignee: { id: number; full_name: string; telegram_id: number };
  photos: TaskPhoto[];
  events: { kind: string; data: Record<string, unknown>; created_at: string }[];
}

function ProductFilter({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const products = useLoad(() => api.get<ProductListItem[]>("/products").catch(() => []), []);
  if (!products.data?.length) return null;
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} style={{ marginBottom: 12 }}>
      <option value="">Все продукты</option>
      {products.data.map((p) => (
        <option key={p.id} value={p.id}>
          {p.name}
        </option>
      ))}
    </select>
  );
}

export function Moderation({ me }: { me: Me }) {
  const [product, setProduct] = useState("");
  const list = useLoad(
    () => api.get<ReviewItem[]>(`/moderation?limit=50${product ? `&product_id=${product}` : ""}`),
    [product],
  );
  const action = useAction();
  const [dialog, setDialog] = useState<{ item: ReviewItem; decision: "reject" | "return" } | null>(null);
  const canSeeProducts = me.is_owner || me.permissions.includes("tasks.manage") || me.permissions.includes("tasks.import");

  return (
    <div>
      <h1>Модерация</h1>
      {canSeeProducts && <ProductFilter value={product} onChange={setProduct} />}
      <ErrorBox error={list.error} onRetry={list.reload} />
      <ErrorBox error={action.error} />
      {list.loading && !list.data && <Loader />}
      {list.data && list.data.length === 0 && <Empty>Нет заданий на модерации</Empty>}
      {list.data?.map((it) => (
        <div className="card stack" key={it.task.id}>
          <div className="row spread">
            <b>
              {it.product_name} · {it.task.external_id}
            </b>
            <span className="chip">{it.task.units} ед.</span>
          </div>
          <div className="muted small">
            {it.assignee.full_name} · завершено {it.task.completed_at ? formatMsk(it.task.completed_at) : "—"}
          </div>
          {it.task.link && (
            <a className="small" href={it.task.link} target="_blank" rel="noreferrer">
              {it.task.link}
            </a>
          )}
          {it.task.expected_text && <div className="small">Ожидаемый текст: {it.task.expected_text}</div>}
          {it.photos.map((p) => (
            <div key={p.id}>
              <div className="muted small">
                {p.kind === "final" ? "Финальное фото" : `Этап ${(p.stage ?? 0) + 1}`}
                {p.ocr_status ? ` · OCR: ${p.ocr_detail ?? p.ocr_status}` : ""}
              </div>
              <AuthImage id={p.id} style={{ maxHeight: 320, objectFit: "contain" }} />
            </div>
          ))}
          <div className="row">
            <button
              className="primary"
              disabled={action.busy}
              onClick={async () => {
                const r = await action.run(() => api.post(`/moderation/${it.task.id}`, { decision: "approve" }));
                if (r.ok) list.reload();
              }}
            >
              Допустить к оплате
            </button>
            <button className="danger" disabled={action.busy} onClick={() => setDialog({ item: it, decision: "reject" })}>
              Отклонить
            </button>
            <button disabled={action.busy} onClick={() => setDialog({ item: it, decision: "return" })}>
              На исправление
            </button>
          </div>
        </div>
      ))}
      {dialog && (
        <ReasonDialog
          title={dialog.decision === "reject" ? "Отклонить задание" : "Вернуть на исправление"}
          confirmText={dialog.decision === "reject" ? "Отклонить" : "Вернуть"}
          danger={dialog.decision === "reject"}
          reasonRequired
          reasonLabel={dialog.decision === "reject" ? "Причина" : "Что исправить"}
          onClose={() => setDialog(null)}
          onConfirm={async (reason) => {
            await api.post(`/moderation/${dialog.item.task.id}`, { decision: dialog.decision, reason });
            list.reload();
          }}
        />
      )}
    </div>
  );
}

const MANUAL_EVENT: Record<string, string> = {
  ocr_attempt: "Попытка OCR",
  error_reported: "Сообщение об ошибке",
  manual_review: "Передано на проверку",
};

export function ManualReview() {
  const [product, setProduct] = useState("");
  const list = useLoad(
    () => api.get<ReviewItem[]>(`/manual-review?limit=50${product ? `&product_id=${product}` : ""}`),
    [product],
  );
  const [dialog, setDialog] = useState<{ item: ReviewItem; action: "approve" | "return" | "cancel" } | null>(null);

  return (
    <div>
      <h1>Ручная проверка</h1>
      <ProductFilter value={product} onChange={setProduct} />
      <ErrorBox error={list.error} onRetry={list.reload} />
      {list.loading && !list.data && <Loader />}
      {list.data && list.data.length === 0 && <Empty>Очередь пуста</Empty>}
      {list.data?.map((it) => (
        <div className="card stack" key={it.task.id}>
          <div className="row spread">
            <b>
              {it.product_name} · {it.task.external_id}
            </b>
            <span className="chip warn">{it.assignee.full_name}</span>
          </div>
          <div className="notice small">{it.task.manual_review_reason ?? "Причина не указана"}</div>
          {it.task.expected_text && <div className="small">Ожидаемый текст: <b>{it.task.expected_text}</b></div>}
          {it.events.map((e, i) => (
            <div className="small muted" key={i}>
              {formatMsk(e.created_at)} · {MANUAL_EVENT[e.kind] ?? e.kind}
              {e.data.attempt ? ` №${e.data.attempt}` : ""}
              {e.data.detail ? ` · ${e.data.detail}` : ""}
              {e.data.reason ? ` · ${e.data.reason}` : ""}
              {e.data.comment ? ` · ${e.data.comment}` : ""}
            </div>
          ))}
          {it.photos.map((p) => (
            <div key={p.id}>
              <AuthImage id={p.id} style={{ maxHeight: 320, objectFit: "contain" }} />
              {p.ocr_text !== null && (
                <div className="small muted" style={{ whiteSpace: "pre-wrap" }}>
                  Распознано: {p.ocr_text || "(пусто)"}
                </div>
              )}
            </div>
          ))}
          <div className="row">
            <button className="primary" onClick={() => setDialog({ item: it, action: "approve" })}>
              Одобрить
            </button>
            <button onClick={() => setDialog({ item: it, action: "return" })}>Вернуть сотруднику</button>
            <button className="danger" onClick={() => setDialog({ item: it, action: "cancel" })}>
              Отменить задание
            </button>
          </div>
        </div>
      ))}
      {dialog && (
        <ReasonDialog
          title={
            dialog.action === "approve" ? "Одобрить вручную" : dialog.action === "return" ? "Вернуть сотруднику" : "Отменить задание"
          }
          confirmText="Подтвердить"
          danger={dialog.action === "cancel"}
          reasonRequired={dialog.action !== "approve"}
          reasonLabel={dialog.action === "approve" ? "Комментарий (необязательно)" : "Причина"}
          onClose={() => setDialog(null)}
          onConfirm={async (reason) => {
            await api.post(`/manual-review/${dialog.item.task.id}/${dialog.action}`, { reason: reason || null });
            list.reload();
          }}
        />
      )}
    </div>
  );
}
