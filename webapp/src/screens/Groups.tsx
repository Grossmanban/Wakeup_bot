import { useState } from "react";
import { api, formatMsk, Me } from "../api";
import { Empty, ErrorBox, Field, Loader, useAction, useLoad } from "../components/ui";
import { confirmAction } from "../telegram";

export interface TgGroup {
  id: number;
  chat_id: number;
  title: string;
  chat_type: string | null;
  is_forum: boolean;
  purpose: string;
  status: string;
  bot_rights: { status?: string; can_invite_users?: boolean; can_manage_topics?: boolean };
  last_checked_at: string | null;
  check_error: string | null;
  invite_cooldown_hours: number | null;
}

interface Invite {
  id: number;
  group_id: number;
  user_id: number;
  invite_link: string | null;
  status: "prepared" | "sent" | "send_failed" | "joined" | "error";
  error: string | null;
  created_at: string;
}

export const PURPOSE_TITLES: Record<string, string> = {
  training: "Группа стажировки / предзакреп",
  main: "Основная рабочая группа",
  photoreports: "Фотоотчёты",
  summary: "Дневная сводка",
  audit: "Архив аудита",
  other: "Прочее",
};

export const INVITE_STATUS: Record<string, { title: string; cls: string }> = {
  prepared: { title: "Приглашение подготовлено", cls: "warn" },
  sent: { title: "Отправка подтверждена", cls: "ok" },
  send_failed: { title: "Ошибка отправки", cls: "bad" },
  joined: { title: "Состоит в группе", cls: "ok" },
  error: { title: "Ошибка", cls: "bad" },
};

const MEMBER: Record<string, { title: string; cls: string }> = {
  member: { title: "Состоит в группе", cls: "ok" },
  not_member: { title: "Не состоит", cls: "bad" },
  unknown: { title: "Не удалось проверить", cls: "warn" },
};

export function GroupsSettings() {
  const list = useLoad(() => api.get<TgGroup[]>("/groups"), []);
  const [chatId, setChatId] = useState("");
  const action = useAction();
  const [test, setTest] = useState<Record<number, string>>({});
  return (
    <div>
      <h1>Telegram-группы</h1>
      <p className="muted small">
        Добавьте бота в группу администратором с правом приглашать участников — группа появится здесь сама. Можно
        добавить и по ID чата (например, -100…).
      </p>
      <ErrorBox error={list.error ?? action.error} />
      <form
        className="row"
        style={{ flexWrap: "nowrap", marginBottom: 12 }}
        onSubmit={async (e) => {
          e.preventDefault();
          const r = await action.run(() => api.post("/groups", { chat_id: Number(chatId) }));
          if (r.ok) {
            setChatId("");
            list.reload();
          }
        }}
      >
        <input placeholder="ID чата" value={chatId} onChange={(e) => setChatId(e.target.value)} inputMode="numeric" />
        <button className="primary" disabled={!/^-?\d+$/.test(chatId) || action.busy}>
          Подключить
        </button>
      </form>
      {list.data && list.data.length === 0 && <Empty>Групп пока нет</Empty>}
      {list.data?.map((g) => (
        <div className="card stack" key={g.id}>
          <div className="row spread">
            <b>{g.title}</b>
            <span className={`chip ${g.status === "connected" ? "ok" : "bad"}`}>
              {g.status === "connected" ? "Бот в группе" : g.status === "left" ? "Бота нет в группе" : "Ошибка проверки"}
            </span>
          </div>
          <div className="muted small">
            ID {g.chat_id} · права бота: {g.bot_rights.status ?? "—"}
            {g.bot_rights.can_invite_users ? ", может приглашать" : ", не может приглашать"}
            {g.is_forum ? `, темы ${g.bot_rights.can_manage_topics ? "доступны" : "недоступны"}` : ""}
            {g.last_checked_at ? ` · проверено ${formatMsk(g.last_checked_at)}` : ""}
          </div>
          {g.check_error && <div className="error small">{g.check_error}</div>}
          <Field label="Назначение">
            <select
              value={g.purpose}
              onChange={async (e) => {
                const r = await action.run(() => api.patch(`/groups/${g.id}`, { purpose: e.target.value }));
                if (r.ok) list.reload();
              }}
            >
              {Object.entries(PURPOSE_TITLES).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </Field>
          <div className="row">
            <button
              disabled={action.busy}
              onClick={async () => {
                const r = await action.run(() => api.post(`/groups/${g.id}/check-rights`));
                if (r.ok) list.reload();
              }}
            >
              Проверить права
            </button>
            <button
              disabled={action.busy}
              onClick={async () => {
                const r = await action.run(() => api.post<{ ok: boolean; error?: string }>(`/groups/${g.id}/test`));
                if (r.ok) setTest({ ...test, [g.id]: r.value.ok ? "Telegram подтвердил отправку" : `Ошибка: ${r.value.error}` });
              }}
            >
              Тестовое сообщение
            </button>
          </div>
          {test[g.id] && <div className="small">{test[g.id]}</div>}
        </div>
      ))}
    </div>
  );
}

interface PersonRow {
  user_id: number;
  full_name: string;
  can_message: boolean;
  progress: { days: number; label: string; units: number; is_regular: boolean; transition_date: string | null };
  became_regular_on: string | null;
  memberships: Record<string, { status: string; checked_at: string | null; invite: Invite | null }>;
  recommendations: string[];
}

export function Membership({ me }: { me: Me }) {
  const ov = useLoad(() => api.get<{ groups: TgGroup[]; people: PersonRow[] }>("/groups/overview"), []);
  const action = useAction();
  const [inviteFor, setInviteFor] = useState<number[] | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [onlyProblems, setOnlyProblems] = useState(false);
  const canInvite = me.is_owner || me.permissions.includes("groups.invite");
  if (ov.loading && !ov.data) return <Loader />;
  if (!ov.data) return <ErrorBox error={ov.error} onRetry={ov.reload} />;
  const { groups, people } = ov.data;
  const shown = people.filter((p) => !onlyProblems || p.recommendations.some((r) => r.startsWith("Необходимо") || r.includes("не удалось")));
  return (
    <div>
      <h1>Членство в группах</h1>
      <p className="muted small">
        Telegram не даёт боту полный список участников: членство проверяется по Telegram ID и по событиям вступления.
        «Не удалось проверить» не означает, что человека нет в группе.
      </p>
      <ErrorBox error={action.error} />
      <div className="row" style={{ marginBottom: 10 }}>
        {groups
          .filter((g) => g.status === "connected")
          .map((g) => (
            <button
              key={g.id}
              disabled={action.busy}
              onClick={async () => {
                const r = await action.run(() => api.post(`/groups/${g.id}/check-members`, {}));
                if (r.ok) ov.reload();
              }}
            >
              Проверить: {g.title}
            </button>
          ))}
      </div>
      <label className="check">
        <input type="checkbox" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} />
        Только требующие действий
      </label>
      {canInvite && selected.size > 0 && (
        <button className="primary block" style={{ marginBottom: 10 }} onClick={() => setInviteFor([...selected])}>
          Пригласить выбранных ({selected.size})
        </button>
      )}
      {shown.length === 0 && <Empty>Нет сотрудников</Empty>}
      {shown.map((p) => (
        <div className="card stack small" key={p.user_id}>
          <div className="row spread">
            <label className="check" style={{ padding: 0 }}>
              {canInvite && (
                <input
                  type="checkbox"
                  checked={selected.has(p.user_id)}
                  onChange={() => {
                    const s = new Set(selected);
                    if (s.has(p.user_id)) s.delete(p.user_id);
                    else s.add(p.user_id);
                    setSelected(s);
                  }}
                />
              )}
              <b>{p.full_name}</b>
            </label>
            <span className={`chip ${p.progress.is_regular ? "ok" : "warn"}`}>
              {p.progress.is_regular ? `Действующий с ${p.became_regular_on ?? p.progress.transition_date}` : `Новичок ${p.progress.label}`}
            </span>
          </div>
          <div className="muted">
            Рабочих дней {p.progress.label}, единиц за них {p.progress.units}
            {!p.can_message && " · бот не может написать первым"}
          </div>
          {groups.map((g) => {
            const m = p.memberships[String(g.id)];
            if (!m) return null;
            return (
              <div key={g.id}>
                {g.title}: <span className={`chip ${MEMBER[m.status]?.cls}`}>{MEMBER[m.status]?.title}</span>
                {m.invite && (
                  <span className={`chip ${INVITE_STATUS[m.invite.status]?.cls}`}> {INVITE_STATUS[m.invite.status]?.title}</span>
                )}
              </div>
            );
          })}
          {p.recommendations.map((r, i) => (
            <div key={i} className={r.startsWith("Необходимо") ? "error" : "muted"}>
              {r}
            </div>
          ))}
          {canInvite && (
            <button onClick={() => setInviteFor([p.user_id])} style={{ alignSelf: "flex-start" }}>
              Пригласить в группу
            </button>
          )}
        </div>
      ))}
      {inviteFor && (
        <InviteDialog
          userIds={inviteFor}
          groups={groups}
          onClose={() => {
            setInviteFor(null);
            setSelected(new Set());
            ov.reload();
          }}
        />
      )}
    </div>
  );
}

export function InviteDialog({ userIds, groups, onClose }: { userIds: number[]; groups?: TgGroup[]; onClose: () => void }) {
  const loaded = useLoad(() => (groups ? Promise.resolve(groups) : api.get<TgGroup[]>("/groups")), []);
  const [group, setGroup] = useState<string>("");
  const [result, setResult] = useState<Invite[] | null>(null);
  const action = useAction();
  const list = (loaded.data ?? []).filter((g) => g.status === "connected");
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2 style={{ marginTop: 0 }}>Пригласить в группу</h2>
        <ErrorBox error={loaded.error ?? action.error} />
        {!result ? (
          <>
            {list.length === 0 && <Empty>Нет подключённых групп</Empty>}
            {list.map((g) => (
              <label className="check" key={g.id}>
                <input type="radio" name="g" checked={group === String(g.id)} onChange={() => setGroup(String(g.id))} />
                <span>
                  {g.title} <span className="muted small">· {PURPOSE_TITLES[g.purpose]}</span>
                </span>
              </label>
            ))}
            <div className="row">
              <button
                className="primary"
                disabled={!group || action.busy}
                onClick={async () => {
                  const title = list.find((g) => String(g.id) === group)?.title;
                  if (!(await confirmAction(`Пригласить ${userIds.length > 1 ? `${userIds.length} чел.` : "сотрудника"} в «${title}»?`))) return;
                  const r = await action.run(() =>
                    userIds.length > 1
                      ? api.post<Invite[]>(`/groups/${group}/invite-bulk`, { user_ids: userIds })
                      : api.post<Invite>(`/groups/${group}/invite`, { user_id: userIds[0] }).then((x) => [x]),
                  );
                  if (r.ok) setResult(r.value);
                }}
              >
                Пригласить
              </button>
              <button onClick={onClose}>Отмена</button>
            </div>
          </>
        ) : (
          <>
            {result.map((r, i) => (
              <div className="card small" key={i}>
                <span className={`chip ${INVITE_STATUS[r.status]?.cls}`}>{INVITE_STATUS[r.status]?.title ?? r.status}</span>
                {r.status === "prepared" && <div className="muted">Сообщение в очереди; статус обновится после ответа Telegram.</div>}
                {r.error && <div>{r.error}</div>}
                {r.invite_link && r.status !== "sent" && (
                  <div className="row" style={{ flexWrap: "nowrap", marginTop: 6 }}>
                    <input readOnly value={r.invite_link} onFocus={(e) => e.target.select()} />
                    <button onClick={() => navigator.clipboard?.writeText(r.invite_link!)}>Копировать</button>
                  </div>
                )}
              </div>
            ))}
            <button className="primary" onClick={onClose}>
              Готово
            </button>
          </>
        )}
      </div>
    </div>
  );
}
