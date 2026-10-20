export type Account = {
  user_id: string;
  email: string;
  display_name: string;
  avatar_url: string | null;
};

export class ApiError extends Error {
  constructor(readonly status: number, message?: string) {
    super(message ?? `Personal OS API returned ${status}`);
  }
}

function apiUrl(path: string): string {
  const base = process.env.NEXT_PUBLIC_API_URL;
  if (!base) throw new Error("API browser configuration is missing");
  return `${base.replace(/\/$/, "")}${path}`;
}

async function accountRequest(path: string, method: "GET" | "POST", accessToken: string) {
  const response = await fetch(apiUrl(path), {
    method,
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw new ApiError(response.status);
  return (await response.json()) as Account;
}

export async function apiRequest<T>(
  path: string,
  accessToken: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...init,
    headers: {
      Authorization: `Bearer ${accessToken}`,
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    let message: string | undefined;
    try {
      const body = (await response.json()) as { detail?: unknown };
      // FastAPI returns a list of field errors for 422 validation failures.
      message = typeof body.detail === "string"
        ? body.detail
        : Array.isArray(body.detail)
          ? body.detail
              .map((item) => {
                const issue = item as { loc?: unknown[]; msg?: string };
                const field = Array.isArray(issue.loc) ? issue.loc.slice(1).join(".") : "";
                return field ? `${field}: ${issue.msg ?? "invalid"}` : issue.msg ?? "invalid";
              })
              .join("; ")
          : undefined;
    } catch {
      message = undefined;
    }
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function apiBlobRequest(path: string, accessToken: string): Promise<Blob> {
  const response = await fetch(apiUrl(path), {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw new ApiError(response.status);
  return response.blob();
}

export async function authenticateAndBootstrap(accessToken: string): Promise<Account> {
  await accountRequest("/v1/me", "GET", accessToken);
  return accountRequest("/v1/bootstrap", "POST", accessToken);
}

export type DeviceRun = { company_name: string; title: string };

export type Device = {
  id: string;
  name: string;
  url: string;
  commit: string;
  local_changes: boolean;
  ready: boolean;
  problem: string | null;
  active_run_id: string | null;
  run: DeviceRun | null;
  last_seen_at: string;
  online: boolean;
};

export function listDevices(accessToken: string): Promise<Device[]> {
  return apiRequest<Device[]>("/v1/jobs/devices", accessToken);
}

type Fact = { key: string; value: unknown };

// The address the worker signs into ATS accounts and reads their mail with.
export async function contactEmail(accessToken: string): Promise<string | null> {
  const facts = await apiRequest<Fact[]>("/v1/jobs/facts", accessToken);
  const value = facts.find((fact) => fact.key === "contact.email")?.value;
  return typeof value === "string" ? value : null;
}
