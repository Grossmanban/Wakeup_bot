import { ReactNode, useCallback, useEffect, useState } from "react";
import { ApiError } from "../api";
import { haptic } from "../telegram";

export function Loader({ text = "Загрузка…" }: { text?: string }) {
  return <div className="center muted">{text}</div>;
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="error">
      {msg}
      {onRetry && (
        <div style={{ marginTop: 8 }}>
          <button onClick={onRetry}>Повторить</button>
        </div>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="center muted">{children}</div>;
}

/** Загрузка данных с состояниями «загрузка / ошибка / готово». */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    fn()
      .then(setData)
      .catch(setError)
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(reload, [reload]);
  return { data, error, loading, reload, setData };
}

export type ActionResult<T> = { ok: true; value: T } | { ok: false };

/** Выполнение действия: блокирует кнопку на время запроса и показывает ошибку. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const run = useCallback(async <T,>(fn: () => Promise<T>): Promise<ActionResult<T>> => {
    setBusy(true);
    setError(null);
    try {
      const value = await fn();
      haptic("success");
      return { ok: true, value };
    } catch (e) {
      haptic("error");
      setError(e instanceof ApiError || e instanceof Error ? e : new Error(String(e)));
      return { ok: false };
    } finally {
      setBusy(false);
    }
  }, []);
  return { busy, error, run, setError };
}

export function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

/** Подтверждение опасного действия с необязательной причиной. */
export function ReasonDialog({
  title,
  confirmText,
  danger,
  reasonLabel = "Причина (необязательно)",
  reasonRequired = false,
  onConfirm,
  onClose,
}: {
  title: string;
  confirmText: string;
  danger?: boolean;
  reasonLabel?: string;
  reasonRequired?: boolean;
  onConfirm: (reason: string) => Promise<unknown>;
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const { busy, error, run } = useAction();
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2 style={{ marginTop: 0 }}>{title}</h2>
        <ErrorBox error={error} />
        <Field label={reasonLabel}>
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} />
        </Field>
        <div className="row">
          <button
            className={danger ? "danger" : "primary"}
            disabled={busy || (reasonRequired && !reason.trim())}
            onClick={async () => {
              const r = await run(() => onConfirm(reason.trim()));
              if (r.ok) onClose();
            }}
          >
            {busy ? "Сохранение…" : confirmText}
          </button>
          <button onClick={onClose} disabled={busy}>
            Отмена
          </button>
        </div>
      </div>
    </div>
  );
}

export function StatusChip({ status }: { status: string }) {
  const cls =
    status === "active" || status === "approved"
      ? "ok"
      : status === "pending"
        ? "warn"
        : status === "blocked" || status === "rejected" || status === "disabled"
          ? "bad"
          : "";
  const titles: Record<string, string> = {
    active: "Активен",
    pending: "Ожидает",
    approved: "Одобрена",
    rejected: "Отклонена",
    cancelled: "Отменена",
    disabled: "Отключён",
    blocked: "Заблокирован",
    unregistered: "Без заявки",
  };
  return <span className={`chip ${cls}`}>{titles[status] ?? status}</span>;
}
