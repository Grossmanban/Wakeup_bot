import { useState } from "react";
import { api, formatMsk, Network, upload, UserShort } from "../api";
import { Empty, ErrorBox, Field, Loader, useAction, useLoad } from "../components/ui";
import { useNav } from "../nav";
import { confirmAction } from "../telegram";

interface Audience {
  all: boolean;
  user_ids: number[];
  network_ids: number[];
  coach_id: number | null;
  instructor_id: number | null;
  working_today: boolean | null;
  scheduled_tomorrow: boolean | null;
  no_status_tomorrow: boolean;
}

interface Broadcast {
  id: number;
  text: string;
  audience: Audience;
  require_ack: boolean;
  allow_replies: boolean;
  status: "draft" | "sent";
  recipients_count: number;
  created_at: string;
  sent_at: string | null;
  has_photo: boolean;
}

const EMPTY_AUD: Audience = {
  all: false,
  user_ids: [],
  network_ids: [],
  coach_id: null,
  instructor_id: null,
  working_today: null,
  scheduled_tomorrow: null,
  no_status_tomorrow: false,
};

export function Broadcasts() {
  const nav = useNav();
  const list = useLoad(() => api.get<Broadcast[]>("/broadcasts"), []);
  const open = (id?: number) =>
    nav.push({ type: "custom", key: `bc-${id ?? "new"}`, render: () => <BroadcastEditor id={id} onDone={list.reload} /> });
  return (
    <div>
      <h1>Рассылки</h1>
      <button className="primary" onClick={() => open()}>
        + Новая рассылка
      </button>
      <ErrorBox error={list.error} />
      {list.data && list.data.length === 0 && <Empty>Рассылок нет</Empty>}
      {list.data?.map((b) => (
        <button className="list-item" key={b.id} onClick={() => open(b.id)}>
          <div className="row spread">
            <b dangerouslySetInnerHTML={{ __html: b.text.slice(0, 80) }} />
            <span className={`chip ${b.status === "sent" ? "ok" : ""}`}>{b.status === "sent" ? "Отправлена" : "Черновик"}</span>
          </div>
          <div className="muted small">
            {b.sent_at ? `${formatMsk(b.sent_at)} · получателей ${b.recipients_count}` : formatMsk(b.created_at)}
          </div>
        </button>
      ))}
    </div>
  );
}

interface Preview {
  broadcast: Broadcast;
  count: number;
  cannot_message: number;
  recipients: { user_id: number; full_name: string; can_message: boolean }[];
}

interface Details {
  broadcast: Broadcast;
  counts: { queued: number; sent: number; failed: number; acked: number; total: number };
  recipients: { user_id: number; full_name: string; status: string; error: string | null; acked_at: string | null }[];
  replies: { id: number; full_name: string; text: string | null; media_kind: string | null; created_at: string }[];
}

function BroadcastEditor({ id, onDone }: { id?: number; onDone: () => void }) {
  const [bid, setBid] = useState<number | undefined>(id);
  const existing = useLoad(() => (bid ? api.get<Details>(`/broadcasts/${bid}`) : Promise.resolve(null)), [bid]);
  if (bid && existing.loading && !existing.data) return <Loader />;
  if (existing.data?.broadcast.status === "sent") return <Stats d={existing.data} onReload={existing.reload} />;
  return (
    <Draft
      initial={existing.data?.broadcast}
      onSent={(sentId) => {
        onDone();
        if (sentId === bid) existing.reload();
        else setBid(sentId);
      }}
    />
  );
}

function Draft({ initial, onSent }: { initial?: Broadcast; onSent: (id: number) => void }) {
  const [text, setText] = useState(initial?.text ?? "");
  const [aud, setAud] = useState<Audience>({ ...EMPTY_AUD, ...(initial?.audience ?? {}) });
  const [ack, setAck] = useState(initial?.require_ack ?? false);
  const [replies, setReplies] = useState(initial?.allow_replies ?? false);
  const [id, setId] = useState(initial?.id);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [hasPhoto, setHasPhoto] = useState(initial?.has_photo ?? false);
  const people = useLoad(() => api.get<{ items: UserShort[] }>("/users?limit=200&status=active").then((r) => r.items).catch(() => []), []);
  const networks = useLoad(() => api.get<Network[]>("/networks").catch(() => []), []);
  const action = useAction();

  const save = async () => {
    const body = { text, audience: aud, require_ack: ack, allow_replies: replies };
    const b = id ? await api.put<Broadcast>(`/broadcasts/${id}`, body) : await api.post<Broadcast>("/broadcasts", body);
    setId(b.id);
    return b;
  };
  const tri = (label: string, value: boolean | null, set: (v: boolean | null) => void) => (
    <Field label={label}>
      <select value={value === null ? "" : String(value)} onChange={(e) => set(e.target.value === "" ? null : e.target.value === "true")}>
        <option value="">Не важно</option>
        <option value="true">Да</option>
        <option value="false">Нет</option>
      </select>
    </Field>
  );
  const byRole = (r: string) => (people.data ?? []).filter((p) => p.role === r);

  return (
    <div>
      <h1>{id ? "Черновик рассылки" : "Новая рассылка"}</h1>
      <ErrorBox error={action.error} />
      <Field label="Текст (можно <b>жирный</b>, <i>курсив</i>, <u>подчёркнутый</u>, <a href=&quot;https://…&quot;>ссылка</a>)">
        <textarea value={text} onChange={(e) => { setText(e.target.value); setPreview(null); }} style={{ minHeight: 140 }} />
      </Field>
      {id && (
        <div className="row" style={{ marginBottom: 8 }}>
          <label className="list-item" style={{ width: "auto", marginBottom: 0 }}>
            {hasPhoto ? "Заменить фото" : "Прикрепить фото"}
            <input
              type="file"
              accept="image/*"
              style={{ display: "none" }}
              onChange={async (e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                const form = new FormData();
                form.append("file", f);
                const r = await action.run(() => upload<Broadcast>(`/broadcasts/${id}/photo`, form));
                if (r.ok) setHasPhoto(true);
              }}
            />
          </label>
          {hasPhoto && (
            <button
              onClick={async () => {
                const r = await action.run(() => api.del(`/broadcasts/${id}/photo`));
                if (r.ok) setHasPhoto(false);
              }}
            >
              Убрать фото
            </button>
          )}
        </div>
      )}
      <h2>Аудитория</h2>
      <label className="check">
        <input type="checkbox" checked={aud.all} onChange={(e) => setAud({ ...aud, all: e.target.checked })} />
        Все пользователи (в пределах вашей области)
      </label>
      <Field label="Конкретные сотрудники">
        <select multiple value={aud.user_ids.map(String)} style={{ minHeight: 100 }} onChange={(e) => setAud({ ...aud, user_ids: [...e.target.selectedOptions].map((o) => Number(o.value)) })}>
          {people.data?.map((p) => (
            <option key={p.id} value={p.id}>
              {p.full_name}
            </option>
          ))}
        </select>
      </Field>
      {(networks.data?.length ?? 0) > 0 && (
        <Field label="Сети">
          <select multiple value={aud.network_ids.map(String)} onChange={(e) => setAud({ ...aud, network_ids: [...e.target.selectedOptions].map((o) => Number(o.value)) })}>
            {networks.data!.map((n) => (
              <option key={n.id} value={n.id}>
                {n.name}
              </option>
            ))}
          </select>
        </Field>
      )}
      {(["coach", "instructor"] as const).map(
        (role) =>
          byRole(role).length > 0 && (
            <Field key={role} label={role === "coach" ? "Команда тренера" : "Команда инструктора"}>
              <select
                value={(role === "coach" ? aud.coach_id : aud.instructor_id) ?? ""}
                onChange={(e) => setAud({ ...aud, [`${role}_id`]: e.target.value ? Number(e.target.value) : null })}
              >
                <option value="">—</option>
                {byRole(role).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.full_name}
                  </option>
                ))}
              </select>
            </Field>
          ),
      )}
      {tri("Работает сегодня", aud.working_today, (v) => setAud({ ...aud, working_today: v }))}
      {tri("В графике на завтра", aud.scheduled_tomorrow, (v) => setAud({ ...aud, scheduled_tomorrow: v }))}
      <label className="check">
        <input type="checkbox" checked={aud.no_status_tomorrow} onChange={(e) => setAud({ ...aud, no_status_tomorrow: e.target.checked })} />
        Не указали статус на завтра
      </label>
      <p className="muted small">Заданные условия объединяются: получат те, кто подходит под все сразу.</p>
      <h2>Параметры</h2>
      <label className="check">
        <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
        Кнопка «Я ознакомился» (без неё нельзя утверждать, что сообщение прочитано)
      </label>
      <label className="check">
        <input type="checkbox" checked={replies} onChange={(e) => setReplies(e.target.checked)} />
        Разрешить ответы на рассылку
      </label>
      <div className="row">
        <button
          disabled={action.busy || !text.trim()}
          onClick={async () => {
            const r = await action.run(async () => {
              const b = await save();
              return api.get<Preview>(`/broadcasts/${b.id}/preview`);
            });
            if (r.ok) setPreview(r.value);
          }}
        >
          Сохранить и предпросмотр
        </button>
      </div>
      {preview && (
        <div className="card stack">
          <b>Предпросмотр</b>
          {/* Показывается текст, очищенный сервером: только теги, которые понимает Telegram. */}
          <div className="card" style={{ whiteSpace: "pre-wrap" }} dangerouslySetInnerHTML={{ __html: preview.broadcast.text }} />
          <div>
            Получателей: <b>{preview.count}</b>
            {preview.cannot_message > 0 && <span className="muted"> (из них {preview.cannot_message} не запускали бота — им не дойдёт)</span>}
          </div>
          <details className="small">
            <summary>Список</summary>
            {preview.recipients.map((r) => (
              <div key={r.user_id} className={r.can_message ? "" : "muted"}>
                {r.full_name}
              </div>
            ))}
          </details>
          <button
            className="primary"
            disabled={action.busy || preview.count === 0}
            onClick={async () => {
              if (!(await confirmAction(`Отправить рассылку ${preview.count} получателям?`))) return;
              const r = await action.run(() => api.post(`/broadcasts/${id}/send`, { expected_count: preview.count }));
              if (r.ok) onSent(id!);
            }}
          >
            Отправить {preview.count} получателям
          </button>
        </div>
      )}
    </div>
  );
}

const ST: Record<string, string> = { queued: "в очереди", sent: "отправлено", failed: "ошибка" };

function Stats({ d, onReload }: { d: Details; onReload: () => void }) {
  return (
    <div>
      <h1>Рассылка</h1>
      <div className="card" style={{ whiteSpace: "pre-wrap" }} dangerouslySetInnerHTML={{ __html: d.broadcast.text }} />
      <div className="card kv small">
        <span>Получателей</span>
        <span>{d.counts.total}</span>
        <span>Отправка подтверждена Telegram</span>
        <span>{d.counts.sent}</span>
        <span>В очереди</span>
        <span>{d.counts.queued}</span>
        <span>Ошибки доставки</span>
        <span>{d.counts.failed}</span>
        {d.broadcast.require_ack && (
          <>
            <span>Ознакомились</span>
            <span>{d.counts.acked}</span>
          </>
        )}
      </div>
      <button onClick={onReload} style={{ marginBottom: 8 }}>
        Обновить
      </button>
      {d.recipients.map((r) => (
        <div className="small" key={r.user_id}>
          {r.full_name} — {ST[r.status] ?? r.status}
          {r.acked_at ? `, ознакомился ${formatMsk(r.acked_at)}` : ""}
          {r.error ? <span className="muted"> ({r.error})</span> : ""}
        </div>
      ))}
      {d.replies.length > 0 && <h2>Ответы</h2>}
      {d.replies.map((r) => (
        <div className="card small" key={r.id}>
          <b>{r.full_name}</b> · {formatMsk(r.created_at)}
          <div>{r.text ?? (r.media_kind ? `[${r.media_kind}]` : "")}</div>
        </div>
      ))}
    </div>
  );
}

export function MyMessages() {
  const list = useLoad(() => api.get<{ id: number; text: string; sent_at: string | null; require_ack: boolean; acked_at: string | null }[]>("/my/broadcasts"), []);
  const action = useAction();
  const pending = list.data?.filter((m) => m.require_ack && !m.acked_at) ?? [];
  if (!list.data?.length) return null;
  return (
    <>
      <h1>Сообщения{pending.length ? ` (${pending.length})` : ""}</h1>
      <ErrorBox error={action.error} />
      {list.data.slice(0, 10).map((m) => (
        <div className="card small stack" key={m.id}>
          <div style={{ whiteSpace: "pre-wrap" }} dangerouslySetInnerHTML={{ __html: m.text }} />
          <div className="muted">{m.sent_at ? formatMsk(m.sent_at) : ""}</div>
          {m.require_ack &&
            (m.acked_at ? (
              <span className="chip ok">Вы ознакомились</span>
            ) : (
              <button
                className="primary"
                disabled={action.busy}
                onClick={async () => {
                  const r = await action.run(() => api.post(`/my/broadcasts/${m.id}/ack`));
                  if (r.ok) list.reload();
                }}
              >
                Я ознакомился
              </button>
            ))}
        </div>
      ))}
    </>
  );
}

interface AuditRow {
  id: number;
  occurred_at: string;
  category: string;
  action: string;
  actor: string | null;
  target: string | null;
  object_type: string | null;
  object_id: string | null;
  old_value: unknown;
  new_value: unknown;
  reason: string | null;
  result: string;
}

const CATS: Record<string, string> = {
  users: "Пользователи и доступы",
  penalties: "Штрафы и удержания",
  bonuses: "Бонусы",
  tasks: "Задания и модерация",
  photos: "Фото",
  schedule: "График и точки",
  finance: "Финансы и выплаты",
  settings: "Настройки",
  groups: "Группы и приглашения",
  broadcasts: "Рассылки",
  system: "Системные события",
};

export function AuditLog() {
  const [cat, setCat] = useState("");
  const [rows, setRows] = useState<AuditRow[]>([]);
  const first = useLoad(async () => {
    const r = await api.get<AuditRow[]>(`/audit?limit=100${cat ? `&category=${cat}` : ""}`);
    setRows(r);
    return r;
  }, [cat]);
  const action = useAction();
  return (
    <div>
      <h1>Журнал аудита</h1>
      <select value={cat} onChange={(e) => setCat(e.target.value)} style={{ marginBottom: 10 }}>
        <option value="">Все категории</option>
        {Object.entries(CATS).map(([k, v]) => (
          <option key={k} value={k}>
            {v}
          </option>
        ))}
      </select>
      <ErrorBox error={first.error ?? action.error} />
      {first.loading && !rows.length && <Loader />}
      {rows.map((e) => (
        <div className="card small" key={e.id}>
          <div className="row spread">
            <b>{e.action}</b>
            <span className="muted">#{e.id}</span>
          </div>
          <div className="muted">
            {formatMsk(e.occurred_at)} · {CATS[e.category] ?? e.category} · {e.actor}
            {e.target ? ` → ${e.target}` : ""}
          </div>
          {e.object_type && (
            <div className="muted">
              {e.object_type} {e.object_id}
            </div>
          )}
          {(e.old_value != null || e.new_value != null) && (
            <div style={{ wordBreak: "break-word" }}>
              {JSON.stringify(e.old_value)} → {JSON.stringify(e.new_value)}
            </div>
          )}
          {e.reason && <div>Причина: {e.reason}</div>}
          {e.result !== "success" && <span className="chip bad">{e.result}</span>}
        </div>
      ))}
      {rows.length >= 100 && (
        <button
          disabled={action.busy}
          onClick={async () => {
            const r = await action.run(() =>
              api.get<AuditRow[]>(`/audit?limit=100&before_id=${rows[rows.length - 1].id}${cat ? `&category=${cat}` : ""}`),
            );
            if (r.ok) setRows([...rows, ...r.value]);
          }}
        >
          Показать ещё
        </button>
      )}
    </div>
  );
}

