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

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers: {
        Authorization: `tma ${initData()}`,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
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
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
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
