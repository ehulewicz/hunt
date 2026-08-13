import { apiRequest } from "@/lib/api";

export type CareerStage = "internship" | "new_grad";
export type RoleFamily =
  | "software_engineering"
  | "data_engineering"
  | "ml_engineering"
  | "data_science"
  | "other";
export type SourceCategory = "all" | "faang" | "quant";

export type JobListing = {
  id: string;
  company_name: string;
  title: string;
  location: string | null;
  salary_text: string | null;
  application_url: string | null;
  discovery_url: string | null;
  role_families: RoleFamily[];
  career_stage: CareerStage | null;
  feed_region: "usa" | "international" | null;
  source_posted_at: string | null;
  source_age_days: number | null;
  source_category: Exclude<SourceCategory, "all"> | "other";
  normalized_locations: string[];
  status: string;
  application_id: string | null;
};

export type ListingPage = {
  items: JobListing[];
  total: number;
  limit: number;
  offset: number;
  location_options: string[];
};

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
  notes: string | null;
  created_at: string;
  updated_at: string;
};

export type JobsProfile = {
  legal_name: string | null;
  preferred_name: string | null;
  email: string | null;
  phone: string | null;
  address_line_1: string | null;
  address_line_2: string | null;
  city: string | null;
  region: string | null;
  postal_code: string | null;
  country: string | null;
  linkedin_url: string | null;
  github_url: string | null;
  portfolio_url: string | null;
  work_authorization_status: string | null;
  visa_status: string | null;
  sponsorship_required: boolean | null;
  school_name: string | null;
  degree_type: string | null;
  field_of_study: string | null;
  graduation_date: string | null;
  gpa: string | null;
  earliest_start_date: string | null;
  earliest_start_text: string | null;
  years_professional_experience: string | null;
  remote_preference: string | null;
  willing_to_relocate: boolean | null;
  willing_to_travel: boolean | null;
  target_roles: string[];
  target_locations: string[];
  target_career_stages: CareerStage[];
  minimum_salary: string | null;
  professional_summary: string | null;
  eeo_gender_identity: string | null;
  eeo_race_ethnicities: string[];
  eeo_veteran_status: string | null;
  eeo_disability_status: string | null;
  completed_at: string | null;
  updated_at: string;
};

export type JobDocument = {
  id: string;
  application_id: string | null;
  document_type: string;
  title: string;
  family_id: string;
  version: number;
  source_document_id: string | null;
  content_text: string | null;
  storage_path: string | null;
  mime_type: string | null;
  status: string;
  is_current: boolean;
  created_at: string;
  updated_at: string;
};

export type AnswerLibraryItem = {
  id: string;
  key: string;
  normalized_question: string | null;
  answer_text: string;
  answer_type: string;
  approved: boolean;
  updated_at: string;
};

export type WorkerCredential = {
  id: string;
  name: string;
  scopes: string[];
  expires_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
};

export type WorkerCredentialIssued = WorkerCredential & { token: string };

export async function getListings(
  accessToken: string,
  filters: {
    category: SourceCategory;
    locations: string[];
  },
): Promise<ListingPage> {
  const params = new URLSearchParams({ limit: "200" });
  if (filters.category !== "all") params.set("category", filters.category);
  for (const location of filters.locations) params.append("locations", location);
  return apiRequest<ListingPage>(`/v1/jobs/listings?${params}`, accessToken);
}

export function batchPrepare(accessToken: string, postingIds: string[]) {
  return apiRequest<{ application_ids: string[]; queued_task_ids: string[] }>(
    "/v1/jobs/applications/batch-prepare",
    accessToken,
    { method: "POST", body: JSON.stringify({ posting_ids: postingIds }) },
  );
}

export function getApplications(accessToken: string) {
  return apiRequest<JobApplication[]>("/v1/jobs/applications", accessToken);
}

export function updateApplication(
  accessToken: string,
  applicationId: string,
  update: { status?: string; notes?: string | null },
) {
  return apiRequest<JobApplication>(`/v1/jobs/applications/${applicationId}`, accessToken, {
    method: "PATCH",
    body: JSON.stringify(update),
  });
}

export function getJobsProfile(accessToken: string) {
  return apiRequest<JobsProfile>("/v1/jobs/profile", accessToken);
}

export function updateJobsProfile(accessToken: string, profile: Record<string, unknown>) {
  return apiRequest<JobsProfile>("/v1/jobs/profile", accessToken, {
    method: "PATCH",
    body: JSON.stringify(profile),
  });
}

export function getDocuments(accessToken: string) {
  return apiRequest<JobDocument[]>("/v1/jobs/documents", accessToken);
}

export function approveDocument(accessToken: string, documentId: string) {
  return apiRequest<JobDocument>(`/v1/jobs/documents/${documentId}/approve`, accessToken, {
    method: "POST",
  });
}

export function getAnswers(accessToken: string) {
  return apiRequest<AnswerLibraryItem[]>("/v1/jobs/answers", accessToken);
}

export function saveAnswer(
  accessToken: string,
  item: Omit<AnswerLibraryItem, "id" | "updated_at">,
) {
  return apiRequest<AnswerLibraryItem>(`/v1/jobs/answers/${encodeURIComponent(item.key)}`, accessToken, {
    method: "PUT",
    body: JSON.stringify(item),
  });
}

export function getWorkerCredentials(accessToken: string) {
  return apiRequest<WorkerCredential[]>("/v1/jobs/worker-credentials", accessToken);
}

export function createWorkerCredential(accessToken: string, name: string) {
  return apiRequest<WorkerCredentialIssued>("/v1/jobs/worker-credentials", accessToken, {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export function revokeWorkerCredential(accessToken: string, id: string) {
  return apiRequest<void>(`/v1/jobs/worker-credentials/${id}`, accessToken, {
    method: "DELETE",
  });
}
