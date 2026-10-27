import { initData } from "./telegram";

export type Role = "owner" | "admin" | "coach" | "instructor" | "employee";
export type AccountStatus =
  | "unregistered"
  | "pending"
  | "active"
  | "rejected"
  | "disabled"
  | "blocked";
export type RequestStatus = "pending" | "approved" | "rejected" | "cancelled";

export interface UserShort {
  id: number;
  telegram_id: number;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  full_name: string;
  role: Role;
  status: AccountStatus;
}

export interface UserOut extends UserShort {
  tg_first_name: string | null;
  tg_last_name: string | null;
  status_reason: string | null;
  app_access: boolean;
  can_message: boolean;
  created_at: string;
}

export interface Registration {
  id: number;
  user_id: number;
  first_name: string;
  last_name: string;
  status: RequestStatus;
  reason: string | null;
  created_at: string;
  decided_at: string | null;
}

export interface NameChange {
  id: number;
  user_id: number;
  old_first_name: string | null;
  old_last_name: string | null;
  new_first_name: string;
  new_last_name: string;
  status: RequestStatus;
  reason: string | null;
  created_at: string;
  decided_at: string | null;
}

export type Screen =
  | "register"
  | "pending"
  | "rejected"
  | "disabled"
  | "blocked"
  | "no_app_access"
  | "ready";

export interface Me {
  user: UserOut;
  screen: Screen;
  message: string | null;
  registration: Registration | null;
  name_change: NameChange | null;
  is_owner: boolean;
  permissions: string[];
  has_product_access: boolean;
  product_access_message: string | null;
}

export interface Network {
  id: number;
  name: string;
  is_archived: boolean;
}

export interface Grant {
  permission: string;
  network_id: number | null;
}

export interface UserProfile {
  user: UserOut;
  networks: Network[];
  supervisors: UserShort[];
  subordinates: UserShort[];
  grants: Grant[] | null;
  pending_name_change: NameChange | null;
}

export interface AuditEvent {
  id: number;
  occurred_at: string;
  category: string;
  action: string;
  actor_id: number | null;
  target_user_id: number | null;
  object_type: string | null;
  object_id: string | null;
  old_value: unknown;
  new_value: unknown;
  reason: string | null;
  result: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  extraHeaders: Record<string, string> = {},
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers: {
        Authorization: `tma ${initData()}`,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...extraHeaders,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "network", "Нет связи с сервером. Проверьте интернет и повторите");
  }
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.message ?? (Array.isArray(data?.detail) ? "Проверьте введённые данные" : null);
    throw new ApiError(res.status, data?.code ?? "error", detail ?? `Ошибка сервера (${res.status})`);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown, headers?: Record<string, string>) =>
    request<T>("POST", path, body, headers),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string, body?: unknown) => request<T>("DELETE", path, body),
};

export const ROLE_TITLES: Record<Role, string> = {
  owner: "Владелец",
  admin: "Администратор",
  coach: "Тренер",
  instructor: "Инструктор",
  employee: "Сотрудник",
};

export const STATUS_TITLES: Record<AccountStatus, string> = {
  unregistered: "Не зарегистрирован",
  pending: "Ожидает рассмотрения",
  active: "Активен",
  rejected: "Заявка отклонена",
  disabled: "Отключён",
  blocked: "Заблокирован",
};

export function formatMsk(iso: string): string {
  return (
    new Date(iso).toLocaleString("ru-RU", {
      timeZone: "Europe/Moscow",
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }) + " МСК"
  );
}

// --- продукты и задания (этап 3) ---------------------------------------------------------

export type ProductStatus = "active" | "hidden" | "archived" | "deleted";
export type TaskStatus =
  | "pool"
  | "active"
  | "manual_review"
  | "moderation"
  | "approved"
  | "rejected"
  | "paid"
  | "cancelled"
  | "archived";

export interface LinkItem {
  title: string;
  url: string;
}

export interface FlowStage {
  title: string;
  instructions?: string | null;
  checklist: string[];
  min_seconds: number;
  photo: "none" | "optional" | "required";
}

export interface ProductSettings {
  description: string | null;
  active_task_limit: number;
  instructions: string | null;
  links: LinkItem[];
  materials: LinkItem[];
  required_fields: string[];
  gender: "m" | "f" | null;
  expected_text_default: string | null;
  flow: FlowStage[];
  final_photo_required: boolean;
  timer_reset_reason_required: boolean;
  error_reasons: string[];
  allow_return_for_fix: boolean;
  ocr_enabled: boolean;
  ocr_rule: "contains" | "exact" | "fuzzy";
  ocr_threshold: number;
  ocr_attempts: number;
  training_volume: number;
  min_sample_units: number;
}

export interface Product extends ProductSettings {
  id: number;
  name: string;
  status: ProductStatus;
  created_at: string;
}

export interface ProductListItem {
  id: number;
  name: string;
  status: ProductStatus;
  pool: number;
  active: number;
  on_moderation: number;
}

export interface TaskType {
  id?: number;
  name: string;
  excel_names: string[];
  units: number;
  is_active: boolean;
}

export interface Rates {
  roles: Record<string, number>;
  users: { user_id: number; full_name: string; amount_kop: number }[];
  history: {
    id: number;
    role: Role | null;
    user_id: number | null;
    user_name: string | null;
    amount_kop: number | null;
    created_at: string;
  }[];
}

export interface Task {
  id: number;
  product_id: number;
  task_type_id: number | null;
  external_id: string;
  link: string | null;
  expected_text: string | null;
  rating: string | null;
  instructions: string | null;
  comment: string | null;
  materials: string[];
  gender: string | null;
  extra: Record<string, string>;
  units: number;
  status: TaskStatus;
  assignee_id: number | null;
  assigned_at: string | null;
  opened_at: string | null;
  found_at: string | null;
  current_stage: number;
  stage_started_at: string | null;
  completed_at: string | null;
  ocr_attempts_used: number;
  manual_review_reason: string | null;
  moderated_at: string | null;
  moderation_reason: string | null;
  created_at: string;
}

export interface TaskListItem extends Task {
  product_name: string;
  assignee_name: string | null;
}

export interface MyProduct {
  id: number;
  name: string;
  description: string | null;
  instructions: string | null;
  links: LinkItem[];
  materials: LinkItem[];
  status: ProductStatus;
  pool: number;
  active: number;
  active_task_limit: number;
  rate_kop: number | null;
  rate_error: string | null;
}

export interface ImportRow {
  row: number;
  status: "ok" | "error" | "duplicate" | "skipped";
  errors: string[];
  product?: string | null;
  external_id?: string;
  type?: string | null;
  units?: number;
}

export interface ImportBatch {
  id: number;
  file_name: string;
  status: string;
  summary: { total: number; ok: number; errors: number; duplicates: number; skipped: number; extra_columns: string[] };
  rows: ImportRow[];
  result: { added: number; skipped: number; duplicates: number; errors: number; available: Record<string, number> } | null;
}

export const PRODUCT_STATUS_TITLES: Record<ProductStatus, string> = {
  active: "Активен",
  hidden: "Скрыт",
  archived: "В архиве",
  deleted: "Удалён",
};

export const TASK_STATUS_TITLES: Record<TaskStatus, string> = {
  pool: "В общем пуле",
  active: "Активное",
  manual_review: "Ожидает ручного решения",
  moderation: "На модерации",
  approved: "Допущено к оплате",
  rejected: "Отклонено",
  paid: "Оплачено",
  cancelled: "Отменено",
  archived: "В архиве",
};

/** Копейки → «1 234,50 ₽». */
export function rub(kop: number | null | undefined): string {
  if (kop === null || kop === undefined) return "—";
  const sign = kop < 0 ? "−" : "";
  const abs = Math.abs(kop);
  const r = Math.floor(abs / 100);
  const k = abs % 100;
  const whole = r.toLocaleString("ru-RU");
  return `${sign}${whole}${k ? "," + String(k).padStart(2, "0") : ""} ₽`;
}

/** «12,5» → 1250 копеек; пустая строка → null; некорректный ввод → NaN. */
export function parseRub(input: string): number | null {
  const s = input.replace(/\s/g, "").replace(",", ".");
  if (!s) return null;
  if (!/^\d+(\.\d{1,2})?$/.test(s)) return NaN;
  return Math.round(parseFloat(s) * 100);
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export async function upload<T>(path: string, form: FormData): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method: "POST",
      headers: { Authorization: `tma ${initData()}` },
      body: form,
    });
  } catch {
    throw new ApiError(0, "network", "Нет связи с сервером. Проверьте интернет и повторите");
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, data?.code ?? "error", data?.message ?? `Ошибка сервера (${res.status})`);
  return data as T;
}
