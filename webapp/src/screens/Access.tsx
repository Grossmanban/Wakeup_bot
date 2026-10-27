import { useState } from "react";
import { api, formatMsk } from "../api";
import { Empty, ErrorBox, Field, Loader, useAction, useLoad } from "../components/ui";
import { useNav } from "../nav";
import { confirmAction, haptic } from "../telegram";

interface Claim {
  telegram_id: number;
  claimed_at: string;
  used_at: string | null;
  user_id: number | null;
  full_name: string | null;
}

interface Invite {
  id: number;
  kind_title: string;
  note: string | null;
  created_at: string;
  created_by: string | null;
  expires_at: string | null;
  max_uses: number;
  uses: number;
  status: "active" | "used" | "expired" | "revoked";
  status_title: string;
  link: string | null;
  claims: Claim[];
}

interface Attempt {
  telegram_id: number;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  first_at: string;
  last_at: string;
  attempts: number;
  source: string | null;
  status: string;
  status_title: string;
  user_id: number | null;
}

interface AttemptCard extends Attempt {
  events: { at: string; kind: string; result: string; source: string | null; text: string | null }[];
}

const EVENT_TITLES: Record<string, string> = {
  start: "Нажал /start",
  message: "Написал боту",
  callback: "Нажал кнопку",
  app: "Открыл приложение",
  referral: "Перешёл по реферальной ссылке",
  invite_claimed: "Открыл приглашение сотрудника",
  invite_used: "Зарегистрировался по приглашению",
};
const RESULT_TITLES: Record<string, string> = {
  ignored: "бот промолчал",
  allowed: "доступ разрешён",
  known: "уже в системе",
};

function chipClass(status: string) {
  if (status === "active" || status === "registered" || status === "allowed") return "chip ok";
  if (status === "no_invite" || status === "revoked" || status === "blocked") return "chip bad";
  return "chip";
}

function who(a: { first_name: string | null; last_name: string | null; username: string | null; telegram_id: number }) {
  const name = [a.first_name, a.last_name].filter(Boolean).join(" ");
  return name || (a.username ? `@${a.username}` : `ID ${a.telegram_id}`);
}

/** «Пригласить сотрудника»: ссылки для действующих сотрудников без анкеты и стажировки. */
export function StaffInvites() {
  const list = useLoad(() => api.get<Invite[]>("/staff-invites"), []);
  const action = useAction();
  const [days, setDays] = useState("3");
  const [uses, setUses] = useState("1");
  const [note, setNote] = useState("");
  const [created, setCreated] = useState<Invite | null>(null);

  const create = async () => {
    const d = Number(days);
    const r = await action.run(() =>
      api.post<Invite>("/staff-invites", {
        expires_in_hours: d > 0 ? Math.round(d * 24) : null,
        max_uses: Number(uses) || 1,
        note: note.trim() || null,
      }),
    );
    if (r.ok) {
      setCreated(r.value);
      setNote("");
      haptic("success");
      list.reload();
    }
  };

  return (
    <div>
      <h1>Пригласить сотрудника</h1>
      <p className="muted small">
        Ссылка для уже работающего сотрудника: он укажет фамилию и имя и сразу попадёт в рабочий интерфейс — без анкеты
        кандидата и стажировки. Новых людей приглашайте реферальной ссылкой.
      </p>
      <ErrorBox error={action.error} />
      <div className="card">
        <Field label="Для кого (заметка, видна только вам)">
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={200} placeholder="Например: Иван, точка на Соколе" />
        </Field>
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <Field label="Срок, дней (0 — бессрочно)">
            <input type="number" min={0} max={30} value={days} onChange={(e) => setDays(e.target.value)} />
          </Field>
          <Field label="Использований">
            <input type="number" min={1} max={50} value={uses} onChange={(e) => setUses(e.target.value)} />
          </Field>
        </div>
        <button className="primary" disabled={action.busy} onClick={create}>
          Создать ссылку
        </button>
      </div>

      {created?.link && (
        <div className="card">
          <b>Ссылка готова</b>
          <input readOnly value={created.link} onFocus={(e) => e.target.select()} />
          <button
            onClick={async () => {
              await navigator.clipboard?.writeText(created.link!);
              haptic("success");
            }}
          >
            Копировать
          </button>
          <p className="muted small">Отправьте её сотруднику в личные сообщения.</p>
        </div>
      )}

      <h2>Приглашения</h2>
      {list.loading && !list.data && <Loader />}
      <ErrorBox error={list.error} onRetry={list.reload} />
      {list.data?.length === 0 && <Empty>Приглашений пока нет</Empty>}
      {list.data?.map((i) => (
        <div className="card" key={i.id}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <b>{i.note || `Приглашение №${i.id}`}</b>
            <span className={chipClass(i.status)}>{i.status_title}</span>
          </div>
          <div className="kv small">
            <span>Тип</span>
            <span>{i.kind_title}</span>
            <span>Создано</span>
            <span>
              {formatMsk(i.created_at)}
              {i.created_by ? `, ${i.created_by}` : ""}
            </span>
            <span>Действует до</span>
            <span>{i.expires_at ? formatMsk(i.expires_at) : "бессрочно"}</span>
            <span>Использовано</span>
            <span>
              {i.uses} из {i.max_uses}
            </span>
          </div>
          {i.claims.map((c) => (
            <div className="muted small" key={c.telegram_id}>
              {c.used_at
                ? `Зарегистрирован: ${c.full_name ?? ""} (ID ${c.telegram_id}), ${formatMsk(c.used_at)}`
                : `Открыл ссылку: ID ${c.telegram_id}, ${formatMsk(c.claimed_at)}`}
            </div>
          ))}
          {i.link && (
            <div className="row">
              <button onClick={() => navigator.clipboard?.writeText(i.link!)}>Копировать ссылку</button>
              <button
                className="danger"
                disabled={action.busy}
                onClick={async () => {
                  if (!(await confirmAction("Отозвать приглашение? Ссылка перестанет работать."))) return;
                  const r = await action.run(() => api.post(`/staff-invites/${i.id}/revoke`));
                  if (r.ok) list.reload();
                }}
              >
                Отозвать
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

const ATTEMPT_FILTERS: [string, string][] = [
  ["", "Все"],
  ["no_invite", "Нет приглашения"],
  ["allowed", "Доступ разрешён"],
  ["in_review", "На рассмотрении"],
  ["registered", "Зарегистрированы"],
];

/** Журнал: кто и когда пытался взаимодействовать с ботом. */
export function AccessAttempts() {
  const nav = useNav();
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const list = useLoad(
    () => api.get<Attempt[]>(`/access-attempts?${new URLSearchParams({ status, q }).toString()}`),
    [status, q],
  );
  return (
    <div>
      <h1>Попытки доступа</h1>
      <p className="muted small">
        Все, кто писал боту. Бот видит только то, что передаёт Telegram: ID, имя, username и параметр ссылки.
      </p>
      <div className="row" style={{ flexWrap: "nowrap" }}>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          {ATTEMPT_FILTERS.map(([v, t]) => (
            <option key={v} value={v}>
              {t}
            </option>
          ))}
        </select>
        <input placeholder="Имя, @username или ID" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {list.loading && !list.data && <Loader />}
      <ErrorBox error={list.error} onRetry={list.reload} />
      {list.data?.length === 0 && <Empty>Записей нет</Empty>}
      {list.data?.map((a) => (
        <button
          className="list-item"
          key={a.telegram_id}
          onClick={() =>
            nav.push({ type: "custom", key: `attempt-${a.telegram_id}`, render: () => <AttemptView id={a.telegram_id} /> })
          }
        >
          <div className="row" style={{ justifyContent: "space-between" }}>
            <b>{who(a)}</b>
            <span className={chipClass(a.status)}>{a.status_title}</span>
          </div>
          <div className="muted small">
            ID {a.telegram_id}
            {a.username ? ` · @${a.username}` : ""} · попыток: {a.attempts} · последняя {formatMsk(a.last_at)}
          </div>
        </button>
      ))}
    </div>
  );
}

function AttemptView({ id }: { id: number }) {
  const v = useLoad(() => api.get<AttemptCard>(`/access-attempts/${id}`), [id]);
  if (v.loading && !v.data) return <Loader />;
  if (!v.data) return <ErrorBox error={v.error} onRetry={v.reload} />;
  const a = v.data;
  return (
    <div>
      <h1>{who(a)}</h1>
      <div className="card">
        <div className="kv">
          <span>Telegram ID</span>
          <span>{a.telegram_id}</span>
          <span>Username</span>
          <span>{a.username ? `@${a.username}` : "—"}</span>
          <span>Имя в Telegram</span>
          <span>{[a.first_name, a.last_name].filter(Boolean).join(" ") || "—"}</span>
          <span>Статус</span>
          <span>
            <span className={chipClass(a.status)}>{a.status_title}</span>
          </span>
          <span>Первое обращение</span>
          <span>{formatMsk(a.first_at)}</span>
          <span>Последнее</span>
          <span>{formatMsk(a.last_at)}</span>
          <span>Попыток</span>
          <span>{a.attempts}</span>
          <span>Источник</span>
          <span>{a.source ?? "—"}</span>
        </div>
      </div>
      <h2>История</h2>
      {a.events.map((e, n) => (
        <div className="card small" key={n}>
          <b>{EVENT_TITLES[e.kind] ?? e.kind}</b> — {RESULT_TITLES[e.result] ?? e.result}
          <div className="muted">
            {formatMsk(e.at)}
            {e.source ? ` · ${e.source}` : ""}
          </div>
          {e.text && <div>«{e.text}»</div>}
        </div>
      ))}
    </div>
  );
}
