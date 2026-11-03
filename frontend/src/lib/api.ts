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

export type Fact = {
  key: string;
  label: string;
  value: unknown;
  value_type: "text" | "boolean" | "number" | "date" | "choice" | "list" | "object";
  verified_at: string | null;
  updated_at: string;
};

export function deleteFact(accessToken: string, key: string): Promise<void> {
  return apiRequest<void>(`/v1/jobs/facts/${encodeURIComponent(key)}`, accessToken, { method: "DELETE" });
}

export type JobApplication = {
  id: string;
  posting_id: string;
  company_name: string;
  title: string;
  location: string | null;
  application_url: string | null;
  status: string;
  applied_at: string | null;
  next_action: string | null;
  next_action_at: string | null;
  unanswered_required_questions: string[];
  updated_at: string;
};

export type Prose = {
  id: string;
  question_label: string | null;
  company_name: string | null;
  text: string;
  approved: boolean;
  origin: "manual" | "model" | "archive";
  submitted: boolean;
};

export type Pattern = {
  id: string;
  question_id: string | null;
  question_label: string | null;
  expression: string | null;
  fact_key: string;
  origin: string;
  formatter: string | null;
  status: string;
};

export type AnswerSeen = { company_name: string; title: string; answer_text: string; submitted: boolean };
export type InboxQuestion = {
  question: { id: string; sample_label: string };
  answers: AnswerSeen[];
};
export type Change = {
  id: string;
  subject: "pattern" | "fact" | "prose" | "question" | "strategy" | "code" | "library";
  subject_id: string;
  action: "create" | "update" | "retire" | "reject" | "delete";
  before: unknown;
  after: unknown;
  origin: "you" | "inbox" | "model" | "repair" | "worker";
  run_id: string | null;
  question_key: string | null;
  failure_issue_id: string | null;
  status: "pending" | "passed" | "failed" | null;
  sampled: number | null;
  failed: number | null;
  created_at: string;
  verified_at: string | null;
};

export type CodeChangeDetail = {
  commit?: string;
  cause?: string;
  question?: string;
  summary?: string;
  attempts?: number;
  files?: string[];
  probe?: unknown;
  pushed?: boolean;
  diff?: string;
};

export type InboxFact = {
  reason: "unverified" | "missing" | "contradicted";
  key: string | null;
  label: string | null;
  value: unknown;
  value_type: Fact["value_type"] | null;
  question: string | null;
  missing_fact: string | null;
  issue_id: string | null;
  check: Change | null;
};

export type InboxUnsolved = {
  issue_id: string;
  category: string;
  question: string | null;
  control_kind: string | null;
  note: string | null;
  occurrence_count: number;
  last_seen_at: string;
};

export type InboxCandidate = { pattern: Pattern; answers: AnswerSeen[]; check: Change | null };

export function loadHuntChanges(accessToken: string, subject: Change["subject"], subjectId?: string): Promise<Change[]> {
  const query = new URLSearchParams({ subject });
  if (subjectId !== undefined) query.set("subject_id", subjectId);
  query.set("limit", "100");
  return apiRequest<Change[]>(`/v1/jobs/changes?${query}`, accessToken);
}
export type ProseGap = {
  id: string;
  question_id: string;
  question: string | null;
  application_id: string | null;
  company_name: string | null;
  title: string | null;
  family: string;
  coverage: "partial" | "missing";
  missing_detail: string;
  created_at: string;
  resolved_at: string | null;
};

export type Inbox = {
  candidates: InboxCandidate[];
  // Learned patterns whose replay changed an answer you accepted.
  checks: InboxCandidate[];
  facts: InboxFact[];
  unsolved: InboxUnsolved[];
  questions: InboxQuestion[];
  gaps?: ProseGap[];
};

export type Question = {
  id: string;
  sample_label: string;
  form: "short" | "long";
  disposition: "unbound" | "pattern" | "one_off" | "long_form";
  pattern_id: string | null;
};

export type HuntLibrary = {
  questions: Question[];
  facts: Fact[];
  prose: Prose[];
  patterns: Pattern[];
};

export async function loadHuntLibrary(accessToken: string): Promise<HuntLibrary> {
  const [questions, facts, prose, patterns] = await Promise.all([
    apiRequest<Question[]>("/v1/jobs/questions", accessToken),
    apiRequest<Fact[]>("/v1/jobs/facts", accessToken),
    apiRequest<Prose[]>("/v1/jobs/prose", accessToken),
    apiRequest<Pattern[]>("/v1/jobs/patterns?status=active&status=candidate&status=repaired&status=validated&status=rejected&status=retired", accessToken),
  ]);
  return { questions, facts, prose, patterns };
}

export type HuntOverview = {
  applications: JobApplication[];
  inbox: Inbox;
  facts: Fact[];
  available: number;
};

export async function loadHuntOverview(accessToken: string): Promise<HuntOverview> {
  const [applications, inbox, facts, listings] = await Promise.all([
    apiRequest<JobApplication[]>("/v1/jobs/applications", accessToken),
    apiRequest<Inbox>("/v1/jobs/inbox", accessToken),
    apiRequest<Fact[]>("/v1/jobs/facts", accessToken),
    apiRequest<{ total: number }>("/v1/jobs/listings?limit=1", accessToken),
  ]);
  return { applications, inbox, facts, available: listings.total };
}

// The address the worker signs into ATS accounts and reads their mail with.
export async function contactEmail(accessToken: string): Promise<string | null> {
  const facts = await apiRequest<Fact[]>("/v1/jobs/facts", accessToken);
  const value = facts.find((fact) => fact.key === "contact.email")?.value;
  return typeof value === "string" ? value : null;
}

export function resolveProseGap(accessToken: string, id: string): Promise<ProseGap> {
  return apiRequest<ProseGap>(`/v1/jobs/prose/gaps/${id}/resolve`, accessToken, { method: "POST" });
}

// The career record: education, schools, entries, and skills rows plus the
// v2 resume document rendered from them. Field shapes inside `document` stay
// loose; it is shown and sent back as-is.
export type CareerRecord = {
  education: Record<string, unknown>;
  schools: Record<string, unknown>[];
  entries: Record<string, unknown>[];
  skills: { category: string; items: string }[];
  document: Record<string, unknown>;
};

export function loadCareer(accessToken: string): Promise<CareerRecord> {
  return apiRequest<CareerRecord>("/v1/jobs/career", accessToken);
}

export function saveCareer(accessToken: string, document: unknown): Promise<CareerRecord> {
  return apiRequest<CareerRecord>("/v1/jobs/career", accessToken, {
    method: "PUT",
    body: JSON.stringify(document),
  });
}

export type LibrarySection = "interest" | "direction" | "evidence" | "story" | "entry" | "note";

export type LibraryEntry = {
  id: string;
  code: string;
  section: LibrarySection;
  entry_id: string | null;
  subject: string | null;
  prompt: string;
  guidance: string;
  answer: string | null;
  display_order: number;
  human_percent: number | null;
  created_at: string;
  updated_at: string;
};

export type LibraryEntryWrite = {
  code: string;
  section: LibrarySection;
  entry_id: string | null;
  subject: string | null;
  prompt: string;
  guidance: string;
  answer: string | null;
  display_order: number;
};

export function loadLibrary(accessToken: string): Promise<LibraryEntry[]> {
  return apiRequest<LibraryEntry[]>("/v1/jobs/library", accessToken);
}

export function saveLibraryEntry(accessToken: string, entry: LibraryEntryWrite): Promise<LibraryEntry> {
  return apiRequest<LibraryEntry>(`/v1/jobs/library/${entry.code}`, accessToken, {
    method: "PUT",
    body: JSON.stringify(entry),
  });
}

// The Learned section: the run's kept fixes, the branches a model taught,
// the problems waiting for Retry, and each run's model usage by lane.
export type LearnedFix = {
  change_id: string;
  commit: string;
  file: string;
  cause: string | null;
  question: string | null;
  site: string | null;
  issue_id: string | null;
  checks: Record<string, unknown> | null;
  probe: Record<string, unknown> | null;
  summary: string | null;
  status: "trial" | "proven" | "retired";
  created_at: string;
};

export type LearnedBranch = {
  pattern_id: string | null;
  fact_key: string | null;
  expression: string | null;
  question: string | null;
  created_at: string;
};

export type LearnedUnsolved = {
  issue_id: string;
  cause: string;
  site: string | null;
  label: string | null;
  attempts: string | null;
  created_at: string;
};

export type LearnedRun = {
  run_id: string;
  site: string | null;
  started_at: string;
  model_calls: number;
  model_usage: Record<string, { calls?: number; input_tokens?: number; output_tokens?: number }>;
};

export type LearnedReport = {
  fixes: LearnedFix[];
  branches: LearnedBranch[];
  unsolved: LearnedUnsolved[];
  runs: LearnedRun[];
};

export function loadLearned(accessToken: string): Promise<LearnedReport> {
  return apiRequest<LearnedReport>("/v1/jobs/learned", accessToken);
}

export function retireCodeChange(accessToken: string, changeId: string): Promise<{ retired: boolean }> {
  return apiRequest<{ retired: boolean }>(`/v1/jobs/learned/changes/code/${changeId}/retire`, accessToken, { method: "POST" });
}

export function retirePattern(accessToken: string, patternId: string): Promise<Pattern> {
  return apiRequest<Pattern>(`/v1/jobs/patterns/${patternId}`, accessToken, {
    method: "PATCH",
    body: JSON.stringify({ status: "retired" }),
  });
}

export function retryFailure(accessToken: string, issueId: string): Promise<void> {
  return apiRequest<void>(`/v1/jobs/autofill/failures/${issueId}`, accessToken, {
    method: "PATCH",
    body: JSON.stringify({ status: "open", verification_notes: "" }),
  });
}
