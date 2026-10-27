import { useEffect, useState } from "react";
import {
  AccountStatus,
  api,
  parseRub,
  ProductListItem,
  rub,
  AuditEvent,
  formatMsk,
  Grant,
  Me,
  Network,
  Role,
  ROLE_TITLES,
  STATUS_TITLES,
  UserProfile,
  UserShort,
} from "../api";
import { Empty, ErrorBox, Loader, ReasonDialog, StatusChip, useAction, useLoad } from "../components/ui";
import { confirmAction } from "../telegram";
import { UserFinance } from "./Finance";
import { InviteDialog } from "./Groups";

export function UserCard({ me, userId, onOpen }: { me: Me; userId: number; onOpen: (id: number) => void }) {
  const { data, error, loading, reload } = useLoad(() => api.get<UserProfile>(`/users/${userId}`), [userId]);
  const can = (p: string) => me.is_owner || me.permissions.includes(p);
  const [inviting, setInviting] = useState(false);

  if (loading && !data) return <Loader />;
  if (error || !data) return <ErrorBox error={error ?? "Не удалось загрузить"} onRetry={reload} />;
  const u = data.user;
  const isSelf = u.id === me.user.id;
  const registered = ["active", "disabled", "blocked"].includes(u.status);

  return (
    <div>
      <h1>{u.full_name}</h1>
      <div className="card">
        <div className="kv">
          <span>Telegram ID</span>
          <span>{u.telegram_id}</span>
          <span>Username</span>
          <span>{u.username ? `@${u.username}` : "—"}</span>
          <span>Роль</span>
          <span>{ROLE_TITLES[u.role]}</span>
          <span>Статус</span>
          <span>
            <StatusChip status={u.status} />
            {u.status_reason && <span className="muted small"> {u.status_reason}</span>}
          </span>
          <span>Приложение</span>
          <span>{u.app_access ? "Доступ открыт" : "Доступ закрыт"}</span>
          <span>Сообщения от бота</span>
          <span>{u.can_message ? "Может получать" : "Не запускал бота или заблокировал его"}</span>
          <span>В системе с</span>
          <span>{formatMsk(u.created_at)}</span>
        </div>
      </div>
      {data.pending_name_change && (
        <div className="notice">
          Ожидает смены ФИО на {data.pending_name_change.new_last_name} {data.pending_name_change.new_first_name}
        </div>
      )}

      <UserSummary userId={u.id} />

      <h2>Оргструктура</h2>
      <div className="card stack">
        <div>
          <span className="muted">Сети: </span>
          {data.networks.length ? data.networks.map((n) => n.name).join(", ") : "—"}
        </div>
        <PeopleLine title="Руководители" people={data.supervisors} onOpen={onOpen} />
        <PeopleLine title="Подчинённые" people={data.subordinates} onOpen={onOpen} />
      </div>
      {can("org.manage") && registered && <OrgEditor profile={data} onChanged={reload} />}
      {can("groups.invite") && u.status === "active" && (
        <button style={{ marginBottom: 10 }} onClick={() => setInviting(true)}>
          Пригласить в группу
        </button>
      )}
      {inviting && <InviteDialog userIds={[u.id]} onClose={() => setInviting(false)} />}

      {me.is_owner && u.role !== "owner" && registered && <ProductsEditor userId={u.id} />}
      {me.is_owner && u.role !== "owner" && registered && <RoleEditor user={data} onChanged={reload} />}
      {can("users.status") && !isSelf && u.role !== "owner" && registered && (
        <StatusEditor userId={u.id} status={u.status} onChanged={reload} />
      )}
      {me.is_owner && u.role !== "owner" && registered && (
        <AppAccessEditor userId={u.id} enabled={u.app_access} onChanged={reload} />
      )}
      {me.is_owner && u.role !== "owner" && u.status === "active" && data.grants && (
        <PermissionsEditor userId={u.id} grants={data.grants} onChanged={reload} />
      )}
      {(can("finance.view") || can("finance.manage") || can("payouts.manage")) && registered && (
        <UserFinance me={me} userId={u.id} />
      )}
      {can("audit.view") && <History userId={u.id} />}
    </div>
  );
}

function PeopleLine({ title, people, onOpen }: { title: string; people: UserShort[]; onOpen: (id: number) => void }) {
  return (
    <div>
      <span className="muted">{title}: </span>
      {people.length === 0
        ? "—"
        : people.map((p, i) => (
            <span key={p.id}>
              {i > 0 && ", "}
              <a href="#" onClick={(e) => { e.preventDefault(); onOpen(p.id); }}>
                {p.full_name}
              </a>
            </span>
          ))}
    </div>
  );
}

function RoleEditor({ user, onChanged }: { user: UserProfile; onChanged: () => void }) {
  const [role, setRole] = useState<Role>(user.user.role);
  const { busy, error, run } = useAction();
  return (
    <>
      <h2>Роль</h2>
      <ErrorBox error={error} />
      <div className="row" style={{ flexWrap: "nowrap" }}>
        <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
          {(["admin", "coach", "instructor", "employee"] as Role[]).map((r) => (
            <option key={r} value={r}>
              {ROLE_TITLES[r]}
            </option>
          ))}
        </select>
        <button
          className="primary"
          disabled={busy || role === user.user.role}
          onClick={async () => {
            if (!(await confirmAction(`Назначить роль «${ROLE_TITLES[role]}»?`))) return;
            const r = await run(() => api.put(`/users/${user.user.id}/role`, { role }));
            if (r.ok) onChanged();
          }}
        >
          Сохранить
        </button>
      </div>
      {role === "admin" && role !== user.user.role && (
        <p className="muted small">Администратор не получает прав автоматически — выдайте их ниже.</p>
      )}
    </>
  );
}

const STATUS_ACTIONS: { status: AccountStatus; label: string; danger: boolean }[] = [
  { status: "active", label: "Включить", danger: false },
  { status: "disabled", label: "Отключить", danger: true },
  { status: "blocked", label: "Заблокировать", danger: true },
];

function StatusEditor({ userId, status, onChanged }: { userId: number; status: AccountStatus; onChanged: () => void }) {
  const [target, setTarget] = useState<AccountStatus | null>(null);
  return (
    <>
      <h2>Состояние аккаунта</h2>
      <div className="row">
        {STATUS_ACTIONS.filter((a) => a.status !== status).map((a) => (
          <button key={a.status} className={a.danger ? "danger" : "primary"} onClick={() => setTarget(a.status)}>
            {a.label}
          </button>
        ))}
      </div>
      {target && (
        <ReasonDialog
          title={`${STATUS_ACTIONS.find((a) => a.status === target)!.label}: новый статус «${STATUS_TITLES[target]}»`}
          confirmText="Подтвердить"
          danger={target !== "active"}
          onClose={() => setTarget(null)}
          onConfirm={async (reason) => {
            await api.put(`/users/${userId}/status`, { status: target, reason: reason || null });
            onChanged();
          }}
        />
      )}
    </>
  );
}

function AppAccessEditor({ userId, enabled, onChanged }: { userId: number; enabled: boolean; onChanged: () => void }) {
  const { busy, error, run } = useAction();
  return (
    <>
      <h2>Доступ к приложению</h2>
      <ErrorBox error={error} />
      <button
        className={enabled ? "danger" : "primary"}
        disabled={busy}
        onClick={async () => {
          if (!(await confirmAction(enabled ? "Закрыть доступ к приложению?" : "Открыть доступ к приложению?"))) return;
          const r = await run(() => api.put(`/users/${userId}/app-access`, { enabled: !enabled }));
          if (r.ok) onChanged();
        }}
      >
        {enabled ? "Закрыть доступ" : "Открыть доступ"}
      </button>
    </>
  );
}

interface CatalogItem {
  code: string;
  title: string;
}

function PermissionsEditor({ userId, grants, onChanged }: { userId: number; grants: Grant[]; onChanged: () => void }) {
  const catalog = useLoad(() => api.get<CatalogItem[]>("/permissions/catalog"), []);
  const networks = useLoad(() => api.get<Network[]>("/networks"), []);
  // permission -> набор сетей; null — все сети
  const toMap = (gs: Grant[]) => {
    const m = new Map<string, Set<number | null>>();
    gs.forEach((g) => {
      if (!m.has(g.permission)) m.set(g.permission, new Set());
      m.get(g.permission)!.add(g.network_id);
    });
    return m;
  };
  const [sel, setSel] = useState(() => toMap(grants));
  useEffect(() => setSel(toMap(grants)), [grants]);
  const { busy, error, run } = useAction();
  const [saved, setSaved] = useState(false);

  const toggle = (code: string, network: number | null) => {
    setSaved(false);
    setSel((prev) => {
      const next = new Map(prev);
      const set = new Set(next.get(code) ?? []);
      if (set.has(network)) set.delete(network);
      else if (network === null) {
        set.clear();
        set.add(null);
      } else {
        set.delete(null);
        set.add(network);
      }
      if (set.size) next.set(code, set);
      else next.delete(code);
      return next;
    });
  };

  if (catalog.loading || networks.loading) return <Loader />;
  if (catalog.error || networks.error) return <ErrorBox error={catalog.error ?? networks.error} />;

  return (
    <>
      <h2>Разрешения</h2>
      <p className="muted small">
        По умолчанию прав нет. «Все сети» — без ограничения; иначе действие доступно только для людей из
        выбранных сетей. Тренер и инструктор в любом случае видят только свою команду.
      </p>
      <div className="card">
        {catalog.data!.map((p) => {
          const set = sel.get(p.code);
          return (
            <div key={p.code} style={{ borderBottom: "1px solid var(--border)", padding: "4px 0" }}>
              <label className="check">
                <input
                  type="checkbox"
                  checked={!!set}
                  onChange={() => (set ? setSel((prev) => { const n = new Map(prev); n.delete(p.code); setSaved(false); return n; }) : toggle(p.code, null))}
                />
                <span>{p.title}</span>
              </label>
              {set && networks.data!.length > 0 && (
                <div className="row small" style={{ paddingLeft: 26, paddingBottom: 6 }}>
                  <label className="check">
                    <input type="checkbox" checked={set.has(null)} onChange={() => toggle(p.code, null)} />
                    Все сети
                  </label>
                  {networks.data!.map((n) => (
                    <label className="check" key={n.id}>
                      <input type="checkbox" checked={set.has(n.id)} onChange={() => toggle(p.code, n.id)} />
                      {n.name}
                    </label>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <ErrorBox error={error} />
      {saved && <div className="notice">Разрешения сохранены</div>}
      <button
        className="primary"
        disabled={busy}
        onClick={async () => {
          const body: Grant[] = [];
          sel.forEach((set, code) => set.forEach((n) => body.push({ permission: code, network_id: n })));
          const r = await run(() => api.put(`/users/${userId}/permissions`, { grants: body }));
          if (r.ok) {
            setSaved(true);
            onChanged();
          }
        }}
      >
        {busy ? "Сохранение…" : "Сохранить разрешения"}
      </button>
    </>
  );
}

function OrgEditor({ profile, onChanged }: { profile: UserProfile; onChanged: () => void }) {
  const networks = useLoad(() => api.get<Network[]>("/networks"), []);
  const [chosen, setChosen] = useState<Set<number>>(new Set(profile.networks.map((n) => n.id)));
  useEffect(() => setChosen(new Set(profile.networks.map((n) => n.id))), [profile]);
  const [q, setQ] = useState("");
  const [found, setFound] = useState<UserShort[]>([]);
  const action = useAction();
  const u = profile.user;
  const canLead = u.role === "coach" || u.role === "instructor";

  useEffect(() => {
    if (!canLead || q.trim().length < 2) {
      setFound([]);
      return;
    }
    const t = setTimeout(() => {
      api
        .get<{ items: UserShort[] }>(`/users?limit=10&status=active&q=${encodeURIComponent(q.trim())}`)
        .then((r) =>
          setFound(
            r.items.filter(
              (x) =>
                x.id !== u.id &&
                (u.role === "coach" ? ["instructor", "employee"] : ["employee"]).includes(x.role) &&
                !profile.subordinates.some((s) => s.id === x.id),
            ),
          ),
        )
        .catch(() => setFound([]));
    }, 300);
    return () => clearTimeout(t);
  }, [q, canLead, u.id, u.role, profile.subordinates]);

  const sameNetworks =
    chosen.size === profile.networks.length && profile.networks.every((n) => chosen.has(n.id));

  if (!canLead && !networks.data?.length) return null;

  return (
    <div className="card stack">
      <ErrorBox error={action.error} />
      {networks.data && networks.data.length > 0 && (
        <div>
          <div className="muted small">Сети</div>
          <div className="row">
            {networks.data.map((n) => (
              <label className="check" key={n.id}>
                <input
                  type="checkbox"
                  checked={chosen.has(n.id)}
                  onChange={() => {
                    const s = new Set(chosen);
                    if (s.has(n.id)) s.delete(n.id);
                    else s.add(n.id);
                    setChosen(s);
                  }}
                />
                {n.name}
              </label>
            ))}
          </div>
          <button
            disabled={action.busy || sameNetworks}
            onClick={async () => {
              const r = await action.run(() => api.put(`/users/${u.id}/networks`, { network_ids: [...chosen] }));
              if (r.ok) onChanged();
            }}
          >
            Сохранить сети
          </button>
        </div>
      )}
      {profile.subordinates.length > 0 && canLead && (
        <div>
          <div className="muted small">Убрать подчинённого</div>
          <div className="row">
            {profile.subordinates.map((s) => (
              <button
                key={s.id}
                disabled={action.busy}
                onClick={async () => {
                  if (!(await confirmAction(`Убрать ${s.full_name} из команды?`))) return;
                  const r = await action.run(() =>
                    api.del("/supervisions", { supervisor_id: u.id, subordinate_id: s.id }),
                  );
                  if (r.ok) onChanged();
                }}
              >
                {s.full_name} ✕
              </button>
            ))}
          </div>
        </div>
      )}
      {canLead && (
        <div>
          <div className="muted small">Добавить в команду</div>
          <input placeholder="Начните вводить ФИО" value={q} onChange={(e) => setQ(e.target.value)} />
          {found.map((x) => (
            <button
              className="list-item"
              key={x.id}
              disabled={action.busy}
              onClick={async () => {
                const r = await action.run(() =>
                  api.post("/supervisions", { supervisor_id: u.id, subordinate_id: x.id }),
                );
                if (r.ok) {
                  setQ("");
                  onChanged();
                }
              }}
            >
              {x.full_name} <span className="muted small">· {ROLE_TITLES[x.role]}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

const ACTION_TITLES: Record<string, string> = {
  "owner.bootstrap": "Назначен владельцем",
  "profile.name_set": "Указано ФИО",
  "registration.submitted": "Подана заявка на регистрацию",
  "registration.updated": "Изменена заявка на регистрацию",
  "registration.approved": "Заявка одобрена",
  "registration.rejected": "Заявка отклонена",
  "name_change.requested": "Запрошена смена ФИО",
  "name_change.updated": "Изменена заявка на смену ФИО",
  "name_change.cancelled": "Заявка на смену ФИО отменена",
  "name_change.approved": "ФИО изменено",
  "name_change.rejected": "Смена ФИО отклонена",
  "role.changed": "Изменена роль",
  "status.changed": "Изменён статус",
  "app_access.changed": "Изменён доступ к приложению",
  "permissions.changed": "Изменены разрешения",
  "networks.changed": "Изменены сети",
  "supervision.added": "Добавлен в команду",
  "supervision.removed": "Убран из команды",
  "telegram.delivery_failed": "Сообщение не доставлено",
  "product_access.changed": "Изменены доступы к продуктам",
  "profile.gender_changed": "Изменён пол",
  "rate.changed": "Изменена индивидуальная ставка",
  "task.returned_to_pool": "Задание возвращено в пул",
  "task.cancelled": "Задание отменено",
  "moderation.approve": "Задание допущено к оплате",
  "moderation.reject": "Задание отклонено",
  "moderation.return": "Задание возвращено на исправление",
  "manual_review.approved": "Ручная проверка: одобрено",
  "manual_review.returned": "Ручная проверка: возвращено",
  "shift.created": "Смена в графике",
  "shift.updated": "Изменена смена",
  "shift.plans_changed": "Изменён план смены",
  "shift.cancelled": "Смена отменена",
  "shift.confirmed": "Смена подтверждена",
  "shift.rejected": "Смена отклонена",
  "penalty.created": "Штраф",
  "bonus.created": "Бонус",
  "bonus.auto": "Автоматический бонус",
  "bonus.cancelled": "Бонус отменён",
  "adjustment.created": "Корректировка",
  "offset.created": "Взаимозачёт",
  "payout.confirmed": "Выплата",
  "lazy_units.accrued": "Единицы по сниженной ставке",
  "candidate.approve": "Анкета одобрена",
  "candidate.reject": "Анкета отклонена",
  "training.started": "Начато обучение",
  "training.units_confirmed": "Подтверждены учебные единицы",
  "newbie.transition": "Переход из новичков",
  "newbie.recalculated": "Пересчёт рабочих дней",
  "invite.created": "Приглашение в группу",
  "invite.delivery": "Доставка приглашения",
  "membership.event": "Изменение членства в группе",
};

function History({ userId }: { userId: number }) {
  const { data, error, loading, reload } = useLoad(() => api.get<AuditEvent[]>(`/users/${userId}/history`), [userId]);
  return (
    <>
      <h2>История изменений</h2>
      <ErrorBox error={error} onRetry={reload} />
      {loading && !data && <Loader />}
      {data && data.length === 0 && <Empty>Записей нет</Empty>}
      {data?.map((e) => (
        <div className="card small" key={e.id}>
          <div className="row spread">
            <b>{ACTION_TITLES[e.action] ?? e.action}</b>
            <span className="muted">{formatMsk(e.occurred_at)}</span>
          </div>
          {(e.old_value != null || e.new_value != null) && (
            <div className="muted">
              {JSON.stringify(e.old_value)} → {JSON.stringify(e.new_value)}
            </div>
          )}
          {e.reason && <div>Причина: {e.reason}</div>}
          <div className="muted">
            {e.actor_id ? `Инициатор: ID ${e.actor_id}` : "Система"} · событие #{e.id}
          </div>
        </div>
      ))}
    </>
  );
}

interface UserProducts {
  product_ids: number[];
  gender: "m" | "f" | null;
  rates: { product_id: number; amount_kop: number }[];
}

function ProductsEditor({ userId }: { userId: number }) {
  const products = useLoad(() => api.get<ProductListItem[]>("/products"), []);
  const data = useLoad(() => api.get<UserProducts>(`/users/${userId}/products`), [userId]);
  const [sel, setSel] = useState<Set<number> | null>(null);
  const [rateInput, setRateInput] = useState<Record<number, string>>({});
  useEffect(() => {
    if (data.data) setSel(new Set(data.data.product_ids));
  }, [data.data]);
  const action = useAction();
  const [saved, setSaved] = useState(false);
  if (!products.data || !data.data || !sel) return products.error || data.error ? <ErrorBox error={products.error ?? data.error} /> : null;
  const rates = new Map(data.data.rates.map((r) => [r.product_id, r.amount_kop]));
  const live = products.data.filter((p) => p.status === "active" || p.status === "hidden" || sel.has(p.id));

  return (
    <>
      <h2>Продукты</h2>
      <ErrorBox error={action.error} />
      {live.length === 0 && <p className="muted small">Продуктов пока нет</p>}
      {live.map((p) => (
        <div key={p.id} className="card small">
          <label className="check">
            <input
              type="checkbox"
              checked={sel.has(p.id)}
              onChange={() => {
                const s = new Set(sel);
                if (s.has(p.id)) s.delete(p.id);
                else s.add(p.id);
                setSel(s);
                setSaved(false);
              }}
            />
            <span>
              <b>{p.name}</b>
              {rates.has(p.id) && <span className="muted"> · инд. ставка {rub(rates.get(p.id))}</span>}
            </span>
          </label>
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <input
              placeholder="Индивидуальная ставка, ₽"
              inputMode="decimal"
              value={rateInput[p.id] ?? ""}
              onChange={(e) => setRateInput({ ...rateInput, [p.id]: e.target.value })}
            />
            <button
              disabled={action.busy}
              onClick={async () => {
                const kop = parseRub(rateInput[p.id] ?? "");
                if (Number.isNaN(kop)) {
                  action.setError(new Error("Ставка: число рублей, например 50 или 12,5"));
                  return;
                }
                if (kop === null && !(await confirmAction("Снять индивидуальную ставку?"))) return;
                const r = await action.run(() => api.put(`/products/${p.id}/rates/users/${userId}`, { amount_kop: kop }));
                if (r.ok) {
                  setRateInput({ ...rateInput, [p.id]: "" });
                  data.reload();
                }
              }}
            >
              {rateInput[p.id] ? "Задать" : "Снять"}
            </button>
          </div>
        </div>
      ))}
      {saved && <div className="notice">Доступы сохранены</div>}
      <div className="row" style={{ marginBottom: 10 }}>
        <button
          className="primary"
          disabled={action.busy}
          onClick={async () => {
            const r = await action.run(() => api.put(`/users/${userId}/products`, { ids: [...sel] }));
            if (r.ok) {
              setSaved(true);
              data.reload();
            }
          }}
        >
          Сохранить доступы
        </button>
        <select
          style={{ width: "auto" }}
          value={data.data.gender ?? ""}
          onChange={async (e) => {
            const gender = e.target.value || null;
            const r = await action.run(() => api.put<UserProducts>(`/users/${userId}/gender`, { gender }));
            if (r.ok) data.setData(r.value);
          }}
        >
          <option value="">Пол не указан</option>
          <option value="m">Мужской</option>
          <option value="f">Женский</option>
        </select>
      </div>
    </>
  );
}

interface Summary {
  tasks: { total: number; active: number; manual_review: number; moderation: number; approved: number; paid: number; rejected: number };
  quality: { percent: number | null; reviewed_units: number; small_sample: boolean };
  training_status: string;
  training: { product: string; volume: number; actual_units: number; confirmed_units: number; state: string }[];
  newbie: { label: string; is_regular: boolean; transition_date: string | null };
  memberships: { group: string; purpose: string; status: string }[];
  referrer: string | null;
}

const MS: Record<string, string> = { member: "состоит", not_member: "не состоит", unknown: "не удалось проверить" };

function UserSummary({ userId }: { userId: number }) {
  const d = useLoad(() => api.get<Summary>(`/users/${userId}/summary`), [userId]);
  if (!d.data) return null;
  const s = d.data;
  return (
    <>
      <h2>Показатели</h2>
      <div className="card kv small">
        <span>Заданий всего</span>
        <span>{s.tasks.total}</span>
        <span>Активные / ручная проверка</span>
        <span>
          {s.tasks.active} / {s.tasks.manual_review}
        </span>
        <span>На модерации</span>
        <span>{s.tasks.moderation}</span>
        <span>Допущено к оплате / оплачено</span>
        <span>
          {s.tasks.approved} / {s.tasks.paid}
        </span>
        <span>Отклонено</span>
        <span>{s.tasks.rejected}</span>
        <span>Качество</span>
        <span>
          {s.quality.percent === null ? "—" : `${s.quality.percent}%`}, {s.quality.reviewed_units} проверенных ед.
          {s.quality.small_sample ? " (малая выборка)" : ""}
        </span>
        <span>Рабочие дни</span>
        <span>{s.newbie.is_regular ? `действующий с ${s.newbie.transition_date}` : `новичок ${s.newbie.label}`}</span>
        {s.referrer && (
          <>
            <span>Пригласил</span>
            <span>{s.referrer}</span>
          </>
        )}
        {s.training_status !== "none" && (
          <>
            <span>Обучение</span>
            <span>
              {s.training_status === "completed" ? "завершено" : "идёт"}
              {s.training.map((t) => ` · ${t.product}: ${t.confirmed_units}/${t.volume}`).join("")}
            </span>
          </>
        )}
        {s.memberships.map((m) => (
          <span key={m.group} style={{ display: "contents" }}>
            <span>{m.group}</span>
            <span>{MS[m.status] ?? m.status}</span>
          </span>
        ))}
      </div>
    </>
  );
}
