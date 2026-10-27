import { useState } from "react";
import { api, formatMsk, Me, newIdempotencyKey, parseRub, ProductListItem, rub } from "../api";
import { Empty, ErrorBox, Field, Loader, ReasonDialog, useAction, useLoad } from "../components/ui";
import { useNav } from "../nav";
import { confirmAction } from "../telegram";

interface Entry {
  id: number;
  kind: string;
  title: string;
  product: string | null;
  task_id: number | null;
  units: number | null;
  rate_kop: number | null;
  amount_kop: number;
  occurred_at: string;
  reason: string | null;
  paid: boolean;
  cancelled: boolean;
}

interface Snapshot {
  period_from: string;
  period_to: string;
  products: { product: string; units: number; amount_kop: number; rates: number[]; reduced_units: number; normal_units: number }[];
  earnings_kop: number;
  bonuses_kop: number;
  other_credits_kop: number;
  gross_kop: number;
  lazy: { product_id: number; day: string; units: number; normal_rate_kop: number; reduced_rate_kop: number; minus_kop: number }[];
  lazy_kop: number;
  debts: { entry_id: number; title: string; reason: string | null; date: string; outstanding_kop: number; deducted_kop: number }[];
  deduction_kop: number;
  debt_left_kop: number;
  total_kop: number;
}

interface Overview {
  unpaid_kop: number;
  debt_kop: number;
  entries: Entry[];
  payouts: { id: number; period_from: string; period_to: string; amount_kop: number; created_at: string; snapshot: Snapshot }[];
  lazy: { accrued: number; used: number; left: number; items: { day: string; product: string; done_units: number; units: number; used: number; left: number; reduced_rate_kop: number }[] };
}

function Totals({ o }: { o: Overview }) {
  return (
    <div className="card kv">
      <span>Начислено, не выплачено</span>
      <span>
        <b>{rub(o.unpaid_kop)}</b>
      </span>
      <span>Долг к удержанию</span>
      <span>{rub(o.debt_kop)}</span>
      {o.lazy.accrued > 0 && (
        <>
          <span>Единицы по сниженной ставке</span>
          <span>
            накоплено {o.lazy.accrued}, учтено {o.lazy.used}, остаток {o.lazy.left}
          </span>
        </>
      )}
    </div>
  );
}

function EntryList({ entries, onCancelBonus }: { entries: Entry[]; onCancelBonus?: (e: Entry) => void }) {
  if (!entries.length) return <Empty>Операций нет</Empty>;
  return (
    <>
      {entries.map((e) => (
        <div className="card small" key={e.id}>
          <div className="row spread">
            <b>
              {e.title}
              {e.product ? ` · ${e.product}` : ""}
            </b>
            <b style={{ color: e.amount_kop < 0 ? "var(--danger)" : undefined }}>{rub(e.amount_kop)}</b>
          </div>
          <div className="muted">
            {formatMsk(e.occurred_at)}
            {e.units ? ` · ${e.units} ед.` : ""}
            {e.rate_kop !== null ? ` × ${rub(e.rate_kop)}` : ""}
            {e.paid ? " · выплачено" : ""}
            {e.cancelled ? " · отменён" : ""}
          </div>
          {e.reason && <div>{e.reason}</div>}
          {onCancelBonus && (e.kind === "bonus" || e.kind === "referral_bonus") && !e.cancelled && (
            <button style={{ marginTop: 6 }} onClick={() => onCancelBonus(e)}>
              Отменить бонус
            </button>
          )}
        </div>
      ))}
    </>
  );
}

export function SnapshotView({ s }: { s: Snapshot }) {
  return (
    <div className="stack small">
      {s.products.map((p) => (
        <div key={p.product}>
          <b>{p.product}</b>: {p.units} ед. по {p.rates.map(rub).join(" / ")} = {rub(p.amount_kop)}
          {p.reduced_units > 0 && (
            <div className="muted">
              из них по сниженной ставке {p.reduced_units}, по обычной {p.normal_units}
            </div>
          )}
        </div>
      ))}
      <div className="kv">
        <span>Заработок</span>
        <span>{rub(s.earnings_kop)}</span>
        <span>Бонусы</span>
        <span>{rub(s.bonuses_kop)}</span>
        {s.other_credits_kop !== 0 && (
          <>
            <span>Прочие начисления</span>
            <span>{rub(s.other_credits_kop)}</span>
          </>
        )}
        {s.lazy_kop > 0 && (
          <>
            <span>Сниженная ставка</span>
            <span>−{rub(s.lazy_kop)}</span>
          </>
        )}
        <span>Штрафы и долги</span>
        <span>{rub(s.debts.reduce((a, d) => a + d.outstanding_kop, 0))}</span>
        <span>Удержано</span>
        <span>{s.deduction_kop ? `−${rub(s.deduction_kop)}` : rub(0)}</span>
        <span>Остаток долга</span>
        <span>{rub(s.debt_left_kop)}</span>
        <span>
          <b>К выплате</b>
        </span>
        <span>
          <b>{rub(s.total_kop)}</b>
        </span>
      </div>
      {s.debts.length > 0 && (
        <details>
          <summary>Долги</summary>
          {s.debts.map((d) => (
            <div key={d.entry_id} className="muted">
              {d.date} · {d.title}
              {d.reason ? ` (${d.reason})` : ""}: долг {rub(d.outstanding_kop)}, удержано {rub(d.deducted_kop)}
            </div>
          ))}
        </details>
      )}
      {s.lazy.length > 0 && (
        <details>
          <summary>Единицы по сниженной ставке</summary>
          {s.lazy.map((l, i) => (
            <div key={i} className="muted">
              за {l.day}: {l.units} ед. по {rub(l.reduced_rate_kop)} вместо {rub(l.normal_rate_kop)}
            </div>
          ))}
        </details>
      )}
    </div>
  );
}

export function MyFinance() {
  const o = useLoad(() => api.get<Overview>("/my/finance"), []);
  if (o.loading && !o.data) return <Loader />;
  if (!o.data) return <ErrorBox error={o.error} onRetry={o.reload} />;
  return (
    <div>
      <h1>Финансы</h1>
      <Totals o={o.data} />
      {o.data.payouts.length > 0 && (
        <>
          <h2>Выплаты</h2>
          {o.data.payouts.map((p) => (
            <details className="card" key={p.id}>
              <summary>
                {p.period_from} — {p.period_to}: <b>{rub(p.amount_kop)}</b>
              </summary>
              <SnapshotView s={p.snapshot} />
            </details>
          ))}
        </>
      )}
      <h2>Операции</h2>
      <EntryList entries={o.data.entries} />
    </div>
  );
}

// --- управление ----------------------------------------------------------------------

function isoDay(offset = 0) {
  return new Date(Date.now() + offset * 86400000).toLocaleDateString("sv-SE", { timeZone: "Europe/Moscow" });
}

function periodPreset(p: string): { start: string; end: string } {
  const today = isoDay();
  const d = new Date(today + "T00:00:00");
  switch (p) {
    case "yesterday":
      return { start: isoDay(-1), end: isoDay(-1) };
    case "week": {
      const wd = (d.getDay() + 6) % 7;
      return { start: isoDay(-wd), end: today };
    }
    case "month":
      return { start: today.slice(0, 8) + "01", end: today };
    default:
      return { start: today, end: today };
  }
}

export function UserFinance({ me, userId }: { me: Me; userId: number }) {
  const o = useLoad(() => api.get<Overview>(`/users/${userId}/finance`), [userId]);
  const can = (p: string) => me.is_owner || me.permissions.includes(p);
  const [dialog, setDialog] = useState<"penalty" | "bonus" | "adjustment" | null>(null);
  const [cancelling, setCancelling] = useState<Entry | null>(null);
  if (o.loading && !o.data) return <Loader />;
  if (!o.data) return <ErrorBox error={o.error} onRetry={o.reload} />;
  return (
    <div>
      <h2>Финансы</h2>
      <Totals o={o.data} />
      {can("finance.manage") && (
        <div className="row" style={{ marginBottom: 10 }}>
          <button onClick={() => setDialog("penalty")}>Штраф</button>
          <button onClick={() => setDialog("bonus")}>Бонус</button>
          <button onClick={() => setDialog("adjustment")}>Корректировка</button>
        </div>
      )}
      {can("payouts.manage") && <PayoutBlock userId={userId} onDone={o.reload} />}
      <details>
        <summary>Операции ({o.data.entries.length})</summary>
        <EntryList entries={o.data.entries} onCancelBonus={can("finance.manage") ? setCancelling : undefined} />
      </details>
      {o.data.payouts.length > 0 && (
        <details>
          <summary>Выплаты ({o.data.payouts.length})</summary>
          {o.data.payouts.map((p) => (
            <details className="card" key={p.id}>
              <summary>
                {p.period_from} — {p.period_to}: <b>{rub(p.amount_kop)}</b> · {formatMsk(p.created_at)}
              </summary>
              <SnapshotView s={p.snapshot} />
            </details>
          ))}
        </details>
      )}
      {dialog && <OperationDialog kind={dialog} userId={userId} onClose={() => setDialog(null)} onDone={o.reload} />}
      {cancelling && (
        <ReasonDialog
          title={`Отменить бонус ${rub(cancelling.amount_kop)}`}
          confirmText="Отменить бонус"
          danger
          reasonRequired
          reasonLabel="Причина"
          onClose={() => setCancelling(null)}
          onConfirm={async (reason) => {
            await api.post(`/ledger/${cancelling.id}/cancel-bonus`, { reason });
            o.reload();
          }}
        />
      )}
    </div>
  );
}

function OperationDialog({
  kind,
  userId,
  onClose,
  onDone,
}: {
  kind: "penalty" | "bonus" | "adjustment";
  userId: number;
  onClose: () => void;
  onDone: () => void;
}) {
  const products = useLoad(() => api.get<ProductListItem[]>("/products").catch(() => []), []);
  const [mode, setMode] = useState<"rub" | "units">("rub");
  const [amount, setAmount] = useState("");
  const [units, setUnits] = useState("");
  const [product, setProduct] = useState("");
  const [sign, setSign] = useState<1 | -1>(1);
  const [reason, setReason] = useState("");
  const [requestKey] = useState(newIdempotencyKey);
  const { busy, error, run, setError } = useAction();
  const title = { penalty: "Штраф", bonus: "Бонус", adjustment: "Корректировка" }[kind];

  const submit = async () => {
    let body: Record<string, unknown> = { reason, request_key: requestKey };
    if (kind === "penalty" && mode === "units") {
      body = { ...body, units: Number(units), product_id: Number(product) };
    } else {
      const kop = parseRub(amount);
      if (!kop || Number.isNaN(kop)) {
        setError(new Error("Укажите сумму в рублях, например 500 или 99,90"));
        return;
      }
      body.amount_kop = kind === "adjustment" ? sign * kop : kop;
    }
    const path = { penalty: "penalties", bonus: "bonuses", adjustment: "adjustments" }[kind];
    if (!(await confirmAction(`${title}: подтвердить операцию?`))) return;
    const r = await run(() => api.post(`/users/${userId}/${path}`, body));
    if (r.ok) {
      onDone();
      onClose();
    }
  };

  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2 style={{ marginTop: 0 }}>{title}</h2>
        <ErrorBox error={error} />
        {kind === "penalty" && (
          <div className="row" style={{ marginBottom: 8 }}>
            <button className={mode === "rub" ? "primary" : ""} onClick={() => setMode("rub")}>
              В рублях
            </button>
            <button className={mode === "units" ? "primary" : ""} onClick={() => setMode("units")}>
              В единицах
            </button>
          </div>
        )}
        {kind === "penalty" && mode === "units" ? (
          <>
            <Field label="Продукт (ставка фиксируется на момент штрафа)">
              <select value={product} onChange={(e) => setProduct(e.target.value)}>
                <option value="">—</option>
                {products.data?.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Единиц">
              <input type="number" min={1} value={units} onChange={(e) => setUnits(e.target.value)} />
            </Field>
          </>
        ) : (
          <>
            {kind === "adjustment" && (
              <div className="row" style={{ marginBottom: 8 }}>
                <button className={sign === 1 ? "primary" : ""} onClick={() => setSign(1)}>
                  Начислить
                </button>
                <button className={sign === -1 ? "primary" : ""} onClick={() => setSign(-1)}>
                  Удержать
                </button>
              </div>
            )}
            <Field label="Сумма, ₽">
              <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
            </Field>
          </>
        )}
        <Field label="Причина">
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} />
        </Field>
        <div className="row">
          <button
            className="primary"
            disabled={busy || !reason.trim() || (kind === "penalty" && mode === "units" && (!product || !units))}
            onClick={submit}
          >
            {busy ? "Сохранение…" : "Провести"}
          </button>
          <button onClick={onClose} disabled={busy}>
            Отмена
          </button>
        </div>
      </div>
    </div>
  );
}

function PayoutBlock({ userId, onDone }: { userId: number; onDone: () => void }) {
  const [preset, setPreset] = useState("month");
  const [range, setRange] = useState(periodPreset("month"));
  const [preview, setPreview] = useState<Snapshot | null>(null);
  const [key, setKey] = useState(newIdempotencyKey);
  const action = useAction();
  const [done, setDone] = useState<number | null>(null);

  const load = async (r = range) => {
    setDone(null);
    const res = await action.run(() => api.get<Snapshot>(`/users/${userId}/payout-preview?start=${r.start}&end=${r.end}`));
    if (res.ok) setPreview(res.value);
  };

  return (
    <div className="card stack">
      <b>Выплата</b>
      <div className="row">
        {[
          ["today", "Сегодня"],
          ["yesterday", "Вчера"],
          ["week", "Неделя"],
          ["month", "Месяц"],
          ["custom", "Период"],
        ].map(([id, t]) => (
          <button
            key={id}
            className={preset === id ? "primary" : ""}
            onClick={() => {
              setPreset(id);
              setPreview(null);
              if (id !== "custom") setRange(periodPreset(id));
            }}
          >
            {t}
          </button>
        ))}
      </div>
      {preset === "custom" && (
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <input type="date" value={range.start} onChange={(e) => setRange({ ...range, start: e.target.value })} />
          <input type="date" value={range.end} onChange={(e) => setRange({ ...range, end: e.target.value })} />
        </div>
      )}
      <ErrorBox error={action.error} />
      <button disabled={action.busy} onClick={() => load()}>
        Рассчитать {range.start} — {range.end}
      </button>
      {preview && (
        <>
          <SnapshotView s={preview} />
          {done ? (
            <div className="notice">Выплата №{done} подтверждена</div>
          ) : (
            <button
              className="primary"
              disabled={action.busy}
              onClick={async () => {
                if (!(await confirmAction(`Подтвердить выплату ${rub(preview.total_kop)}?`))) return;
                const r = await action.run(() =>
                  api.post<{ id: number }>(`/users/${userId}/payouts`, {
                    start: range.start,
                    end: range.end,
                    idempotency_key: key,
                    expected_total_kop: preview.total_kop,
                  }),
                );
                if (r.ok) {
                  setDone(r.value.id);
                  setKey(newIdempotencyKey());
                  onDone();
                }
              }}
            >
              Подтвердить выплату {rub(preview.total_kop)}
            </button>
          )}
        </>
      )}
    </div>
  );
}

export function Payouts() {
  const nav = useNav();
  const list = useLoad(
    () => api.get<{ user_id: number; full_name: string; unpaid_kop: number; debt_kop: number }[]>("/finance/unpaid"),
    [],
  );
  return (
    <div>
      <h1>Выплаты</h1>
      <p className="muted small">Сотрудники с невыплаченными начислениями или долгом. Откройте карточку, чтобы рассчитать выплату.</p>
      <ErrorBox error={list.error} onRetry={list.reload} />
      {list.loading && !list.data && <Loader />}
      {list.data && list.data.length === 0 && <Empty>Невыплаченных начислений нет</Empty>}
      {list.data?.map((r) => (
        <button className="list-item" key={r.user_id} onClick={() => nav.push({ type: "user", id: r.user_id })}>
          <div className="row spread">
            <b>{r.full_name}</b>
            <b>{rub(r.unpaid_kop)}</b>
          </div>
          {r.debt_kop > 0 && <div className="muted small">долг {rub(r.debt_kop)}</div>}
        </button>
      ))}
    </div>
  );
}
