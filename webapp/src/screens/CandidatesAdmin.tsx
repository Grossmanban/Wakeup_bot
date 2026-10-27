import { useEffect, useState } from "react";
import { api, formatMsk, initDataHeader, Me, rub, UserShort } from "../api";
import { Empty, ErrorBox, Field, Loader, ReasonDialog, useAction, useLoad } from "../components/ui";
import { useNav } from "../nav";
import { confirmAction } from "../telegram";
import { CANDIDATE_STATUS, CandidateData, Training } from "./Candidate";

interface ListItem extends CandidateData {
  username: string | null;
  telegram_id: number;
}

interface Progress {
  product_id: number;
  product: string;
  volume: number;
  actual_units: number;
  confirmed_units: number;
  paid_units: number;
  state: "in_progress" | "awaiting_confirmation" | "done";
}

interface Details extends ListItem {
  user_id: number;
  can_message: boolean;
  referrer: string | null;
  responsible: string | null;
  comment?: string | null;
  template_status: string | null;
  events: { kind: string; data: Record<string, unknown>; created_at: string }[];
  trainings: Training[];
  training_progress: Progress[] | null;
}

const DELIVERY: Record<string, string> = {
  pending: "в очереди на отправку",
  sent: "Telegram подтвердил отправку",
  failed: "не доставлено (кандидат не запускал бота или заблокировал его)",
};

export function Candidates({ me }: { me: Me }) {
  const nav = useNav();
  const [status, setStatus] = useState("");
  const list = useLoad(() => api.get<ListItem[]>(`/candidates${status ? `?status=${status}` : ""}`), [status]);
  return (
    <div>
      <h1>Кандидаты</h1>
      <select value={status} onChange={(e) => setStatus(e.target.value)} style={{ marginBottom: 12 }}>
        <option value="">Все (кроме черновиков)</option>
        {Object.entries(CANDIDATE_STATUS).map(([k, v]) => (
          <option key={k} value={k}>
            {v}
          </option>
        ))}
      </select>
      <ErrorBox error={list.error} onRetry={list.reload} />
      {list.loading && !list.data && <Loader />}
      {list.data && list.data.length === 0 && <Empty>Анкет нет</Empty>}
      {list.data?.map((c) => (
        <button
          className="list-item"
          key={c.id}
          onClick={() => nav.push({ type: "custom", key: `cand-${c.id}`, render: () => <CandidateCard me={me} id={c.id} /> })}
        >
          <div className="row spread">
            <b>
              {c.last_name} {c.first_name}
            </b>
            <span className="chip">{CANDIDATE_STATUS[c.status]}</span>
          </div>
          <div className="muted small">
            {c.location} · {c.days_per_week} дн./нед. {c.username ? `· @${c.username}` : ""}
          </div>
        </button>
      ))}
    </div>
  );
}

function CandidateCard({ me, id }: { me: Me; id: number }) {
  const d = useLoad(() => api.get<Details>(`/candidates/${id}`), [id]);
  const people = useLoad(() => api.get<{ items: UserShort[] }>("/users?limit=200&status=active").then((r) => r.items).catch(() => []), []);
  const action = useAction();
  const [dialog, setDialog] = useState<"reject" | "request_info" | "comment" | null>(null);
  const [cancelT, setCancelT] = useState<Training | null>(null);
  const [resp, setResp] = useState("");
  const [tr, setTr] = useState({ when: "", place: "", contact: "", comment: "" });
  const can = (p: string) => me.is_owner || me.permissions.includes(p);
  if (d.loading && !d.data) return <Loader />;
  if (!d.data) return <ErrorBox error={d.error} onRetry={d.reload} />;
  const c = d.data;
  const act = async (path: string, body: unknown = {}) => {
    const r = await action.run(() => api.post(`/candidates/${id}/${path}`, body));
    if (r.ok) d.reload();
  };
  return (
    <div>
      <h1>
        {c.last_name} {c.first_name}
      </h1>
      <div className="card kv">
        <span>Статус</span>
        <span>{CANDIDATE_STATUS[c.status]}</span>
        <span>Метро/район</span>
        <span>{c.location}</span>
        <span>Дней в неделю</span>
        <span>{c.days_per_week}</span>
        <span>Обучение</span>
        <span>{c.can_train ? "Может" : "Не может"}</span>
        {Object.entries(c.skills).map(([k, v]) => (
          <span key={k} style={{ display: "contents" }}>
            <span>{k}</span>
            <span>{v}/10</span>
          </span>
        ))}
        <span>Telegram</span>
        <span>
          {c.username ? `@${c.username}` : `ID ${c.telegram_id}`}
          {!c.can_message && " · бот не может написать"}
        </span>
        <span>Согласие ПДн</span>
        <span>
          версия {c.pd_consent_version}, {c.pd_consent_at ? formatMsk(c.pd_consent_at) : "—"}
        </span>
        {c.referrer && (
          <>
            <span>Пригласил</span>
            <span>{c.referrer}</span>
          </>
        )}
        <span>Ответственный</span>
        <span>{c.responsible ?? "—"}</span>
        {c.contact_status && (
          <>
            <span>Самоотчёт</span>
            <span>{c.contact_status === "wrote" ? "«Я написал»" : "«Не могу связаться»"}</span>
          </>
        )}
        {c.template_status && (
          <>
            <span>Сообщение кандидату</span>
            <span>{DELIVERY[c.template_status] ?? c.template_status}</span>
          </>
        )}
      </div>
      {c.info_request && <div className="notice">Запрошено уточнение: {c.info_request}</div>}
      {c.reason && <div className="notice">Причина отказа: {c.reason}</div>}
      <ErrorBox error={action.error} />
      {can("candidates.review") && (
        <div className="row" style={{ marginBottom: 10 }}>
          {c.status === "submitted" && <button onClick={() => act("review/take")}>Взять в работу</button>}
          {(c.status === "submitted" || c.status === "reviewing") && (
            <>
              <button className="primary" onClick={() => act("review/approve")}>
                Принять
              </button>
              <button onClick={() => setDialog("request_info")}>Запросить уточнение</button>
            </>
          )}
          {c.status !== "rejected" && c.status !== "training" && (
            <button className="danger" onClick={() => setDialog("reject")}>
              Отклонить
            </button>
          )}
          <button onClick={() => setDialog("comment")}>Комментарий</button>
        </div>
      )}
      {can("candidates.review") && c.status !== "rejected" && people.data && (
        <div className="card stack">
          <b>Ответственный</b>
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <select value={resp} onChange={(e) => setResp(e.target.value)}>
              <option value="">Выберите…</option>
              {people.data
                .filter((p) => p.role !== "employee" || p.id === me.user.id)
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.full_name}
                  </option>
                ))}
            </select>
            <button
              disabled={!resp || action.busy}
              onClick={async () => {
                const r = await action.run(() => api.put(`/candidates/${id}/responsible`, { responsible_id: Number(resp), send_template: true }));
                if (r.ok) d.reload();
              }}
            >
              Назначить и написать
            </button>
          </div>
          <div className="muted small">Кандидату уйдёт сообщение по шаблону из настроек.</div>
        </div>
      )}

      {can("training.manage") && (c.status === "approved" || c.status === "training") && (
        <>
          <h2>Обучение</h2>
          {c.trainings.map((t) => (
            <div className="card small" key={t.id}>
              <div className="row spread">
                <b>
                  {formatMsk(t.starts_at)} · {t.place}
                </b>
                <span className={`chip ${t.status === "confirmed" ? "ok" : t.status === "cancelled" ? "bad" : "warn"}`}>
                  {{ scheduled: "Назначено", confirmed: "Подтверждено кандидатом", cancelled: "Отменено" }[t.status]}
                </span>
              </div>
              {t.cancel_reason && <div className="muted">Причина: {t.cancel_reason}</div>}
              {t.status !== "cancelled" && (
                <button style={{ marginTop: 6 }} className="danger" onClick={() => setCancelT(t)}>
                  Отменить
                </button>
              )}
            </div>
          ))}
          {c.status === "approved" && (
            <div className="card">
              <Field label="Дата и время (МСК)">
                <input type="datetime-local" value={tr.when} onChange={(e) => setTr({ ...tr, when: e.target.value })} />
              </Field>
              <Field label="Место">
                <input value={tr.place} onChange={(e) => setTr({ ...tr, place: e.target.value })} />
              </Field>
              <Field label="Контактное лицо">
                <select value={tr.contact} onChange={(e) => setTr({ ...tr, contact: e.target.value })}>
                  <option value="">—</option>
                  {people.data?.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.full_name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Комментарий">
                <input value={tr.comment} onChange={(e) => setTr({ ...tr, comment: e.target.value })} />
              </Field>
              <div className="row">
                <button
                  disabled={!tr.when || !tr.place || action.busy}
                  onClick={async () => {
                    const r = await action.run(() =>
                      api.post(`/candidates/${id}/trainings`, {
                        starts_at: `${tr.when}:00+03:00`,
                        place: tr.place,
                        contact_user_id: tr.contact ? Number(tr.contact) : null,
                        comment: tr.comment || null,
                      }),
                    );
                    if (r.ok) {
                      setTr({ when: "", place: "", contact: "", comment: "" });
                      d.reload();
                    }
                  }}
                >
                  Назначить обучение
                </button>
                <button
                  className="primary"
                  disabled={action.busy}
                  onClick={async () => {
                    if (!(await confirmAction("Начать обучение? Будет создан профиль сотрудника."))) return;
                    await act("start-training");
                  }}
                >
                  Начать обучение
                </button>
              </div>
            </div>
          )}
          {c.training_progress && <TrainingProgress userId={c.user_id} progress={c.training_progress} onChanged={d.reload} />}
        </>
      )}

      <details>
        <summary>История ({c.events.length})</summary>
        {c.events.map((e, i) => (
          <div className="small muted" key={i}>
            {formatMsk(e.created_at)} · {e.kind}
            {e.data.text ? ` · ${e.data.text}` : ""}
          </div>
        ))}
      </details>

      {dialog && (
        <ReasonDialog
          title={{ reject: "Отклонить анкету", request_info: "Запросить уточнение", comment: "Комментарий" }[dialog]}
          confirmText="Сохранить"
          danger={dialog === "reject"}
          reasonRequired
          reasonLabel={{ reject: "Причина", request_info: "Что уточнить", comment: "Текст" }[dialog]}
          onClose={() => setDialog(null)}
          onConfirm={async (text) => {
            await api.post(`/candidates/${id}/review/${dialog}`, { text });
            d.reload();
          }}
        />
      )}
      {cancelT && (
        <ReasonDialog
          title="Отменить обучение"
          confirmText={cancelT.status === "confirmed" ? "Отменить (кандидат уже подтвердил)" : "Отменить"}
          danger
          reasonRequired
          reasonLabel="Причина"
          onClose={() => setCancelT(null)}
          onConfirm={async (reason) => {
            await api.post(`/trainings/${cancelT.id}/cancel`, { reason, force: cancelT.status === "confirmed" });
            d.reload();
          }}
        />
      )}
    </div>
  );
}

const STATE: Record<string, { title: string; cls: string }> = {
  in_progress: { title: "Идёт обучение", cls: "" },
  awaiting_confirmation: { title: "Ожидает подтверждения администратора", cls: "warn" },
  done: { title: "Подтверждено", cls: "ok" },
};

export function TrainingProgress({ userId, progress, onChanged }: { userId: number; progress: Progress[]; onChanged?: () => void }) {
  const [input, setInput] = useState<Record<number, string>>({});
  const action = useAction();
  if (!progress.length) return <p className="muted small">Нет продуктов со стажировкой у этого сотрудника.</p>;
  return (
    <>
      <ErrorBox error={action.error} />
      {progress.map((p) => (
        <div className="card small stack" key={p.product_id}>
          <div className="row spread">
            <b>{p.product}</b>
            <span className={`chip ${STATE[p.state].cls}`}>{STATE[p.state].title}</span>
          </div>
          <div className="kv">
            <span>Порог</span>
            <span>{p.volume}</span>
            <span>Выполнено фактически</span>
            <span>{p.actual_units}</span>
            <span>Подтверждено администратором</span>
            <span>{p.confirmed_units}</span>
            <span>Учитывается в оплате</span>
            <span>{p.paid_units}</span>
          </div>
          {onChanged && (
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <input
                type="number"
                min={0}
                max={p.actual_units}
                placeholder="Подтвердить единиц"
                value={input[p.product_id] ?? ""}
                onChange={(e) => setInput({ ...input, [p.product_id]: e.target.value })}
              />
              <button
                disabled={action.busy || input[p.product_id] === undefined || input[p.product_id] === ""}
                onClick={async () => {
                  const r = await action.run(() =>
                    api.post(`/trainees/${userId}/confirm-units`, { product_id: p.product_id, confirmed_units: Number(input[p.product_id]) }),
                  );
                  if (r.ok) onChanged();
                }}
              >
                Подтвердить
              </button>
            </div>
          )}
        </div>
      ))}
    </>
  );
}

export function Trainees() {
  const list = useLoad(
    () => api.get<{ user_id: number; full_name: string; training_status: string; training_started_at: string | null; progress: Progress[] }[]>("/trainees"),
    [],
  );
  return (
    <div>
      <h1>Стажёры</h1>
      <ErrorBox error={list.error} onRetry={list.reload} />
      {list.loading && !list.data && <Loader />}
      {list.data && list.data.length === 0 && <Empty>Стажёров нет</Empty>}
      {list.data?.map((t) => (
        <details className="card" key={t.user_id}>
          <summary>
            <b>{t.full_name}</b> · {t.training_status === "completed" ? "обучение завершено" : "проходит обучение"}
          </summary>
          <TrainingProgress userId={t.user_id} progress={t.progress} onChanged={list.reload} />
        </details>
      ))}
    </div>
  );
}

interface ReferralData {
  code: string;
  link: string;
  visits: number;
  forms_started: number;
  submitted: number;
  approved: number;
  rejected: number;
  training: number;
  training_done: number;
  bonus_kop: number;
  candidates: { id: number; name: string; status: string; training_status: string }[];
}

export function Referral() {
  const d = useLoad(() => api.get<ReferralData>("/my/referral"), []);
  const [qr, setQr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    let url: string | null = null;
    fetch("/api/my/referral/qr.svg", { headers: initDataHeader() })
      .then((r) => (r.ok ? r.blob() : null))
      .then((b) => {
        if (b) {
          url = URL.createObjectURL(b);
          setQr(url);
        }
      })
      .catch(() => undefined);
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, []);
  if (d.loading && !d.data) return <Loader />;
  if (!d.data) return <ErrorBox error={d.error} />;
  const r = d.data;
  return (
    <div>
      <h2>Реферальная ссылка</h2>
      <div className="card stack">
        <div className="small" style={{ wordBreak: "break-all" }}>
          {r.link}
        </div>
        <button
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(r.link);
              setCopied(true);
            } catch {
              window.prompt("Скопируйте ссылку", r.link);
            }
          }}
        >
          {copied ? "Скопировано" : "Скопировать ссылку"}
        </button>
        {qr && <img src={qr} alt="QR-код" style={{ width: 180, background: "#fff", borderRadius: 8 }} />}
      </div>
      <div className="card kv small">
        <span>Переходы</span>
        <span>{r.visits}</span>
        <span>Начатые анкеты</span>
        <span>{r.forms_started}</span>
        <span>Отправленные</span>
        <span>{r.submitted}</span>
        <span>Одобрено / отказ</span>
        <span>
          {r.approved} / {r.rejected}
        </span>
        <span>На обучении</span>
        <span>{r.training}</span>
        <span>Обучение пройдено</span>
        <span>{r.training_done}</span>
        <span>Реферальные бонусы</span>
        <span>{rub(r.bonus_kop)}</span>
      </div>
      {r.candidates.map((c) => (
        <div className="small" key={c.id}>
          {c.name} — {CANDIDATE_STATUS[c.status] ?? c.status}
        </div>
      ))}
    </div>
  );
}
