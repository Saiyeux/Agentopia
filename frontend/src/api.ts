export type AttributeValue = {
  label: string;
  category: string;
  value: unknown;
};

export type TraitValue = {
  label: string;
  category: string;
  score: number;
  locked: boolean;
  scale_min: number;
  scale_max: number;
};

export type Character = {
  id: string;
  name: string;
  summary: string;
  attributes: Record<string, AttributeValue>;
  traits: Record<string, TraitValue>;
  relationships: Relationship[];
};

export type Relationship = {
  to_id: string;
  name: string;
  affection: number;
  trust: number;
  respect: number;
  familiarity: number;
  updated_tick: number;
};

export type WorldState = {
  sim_tick: number;
  day: number;
  chapter: number;
  period: string;
  weather: string;
  tension: number;
  economy_index: number;
  log_count: number;
  active_threads: Array<Record<string, unknown>>;
  worldview: string;
  current_scene: {
    id: number;
    location_id: string;
    title: string;
    purpose: string;
    participants: string[];
    turn_budget: number;
    turn_count: number;
    status: string;
    state: Record<string, unknown>;
  } | null;
};

export type SceneLog = {
  id: number;
  scene_id: number | null;
  tick: number;
  actor_id: string | null;
  type: string;
  content: string;
  data: Record<string, unknown>;
  visibility: string;
  created_at: string;
};

export type AppliedDelta = {
  status: string;
  target: string;
  ref: string;
  field: string;
  op: string;
  requested: unknown;
  old?: unknown;
  new?: unknown;
  reason?: string;
  message?: string;
  source?: string | null;
};

export type LlmProvider = "lmstudio" | "ollama" | "api";

export type LlmSettings = {
  provider: LlmProvider;
  base_url: string;
  model: string;
  api_key_set: boolean;
};

export type LlmSettingsInput = {
  provider: LlmProvider;
  base_url: string;
  model: string;
  api_key: string;
};

export type LlmModel = {
  id: string;
  name?: string;
  modified_at?: string | null;
  size?: number | null;
};

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

async function postJson<T>(path: string, body: Record<string, unknown> = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${message}`);
  }
  return response.json() as Promise<T>;
}

async function putJson<T>(path: string, body: Record<string, unknown> = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${message}`);
  }
  return response.json() as Promise<T>;
}

export function fetchWorld(): Promise<WorldState> {
  return getJson<WorldState>("/api/world");
}

export function fetchCharacters(): Promise<Character[]> {
  return getJson<Character[]>("/api/characters");
}

export function fetchSceneLog(): Promise<SceneLog[]> {
  return getJson<SceneLog[]>("/api/scene-log?limit=300");
}

export function runOneStep(): Promise<Record<string, unknown>> {
  return postJson<Record<string, unknown>>("/api/engine/step");
}

export function clearSceneLog(): Promise<Record<string, unknown>> {
  return postJson<Record<string, unknown>>("/api/scene-log/clear");
}

export function startOpening(): Promise<Record<string, unknown>> {
  return postJson<Record<string, unknown>>("/api/engine/opening");
}

export function resetCharacters(): Promise<Record<string, unknown>> {
  return postJson<Record<string, unknown>>("/api/dev/reset-characters");
}

export function fetchLlmSettings(): Promise<LlmSettings> {
  return getJson<LlmSettings>("/api/llm/settings");
}

export function saveLlmSettings(settings: LlmSettingsInput): Promise<{ status: string; settings: LlmSettings }> {
  return putJson<{ status: string; settings: LlmSettings }>("/api/llm/settings", settings);
}

export function fetchLlmModels(): Promise<{ status: string; settings: LlmSettings; models: LlmModel[] }> {
  return getJson<{ status: string; settings: LlmSettings; models: LlmModel[] }>("/api/llm/models");
}

export function testLlmConnection(): Promise<Record<string, unknown>> {
  return postJson<Record<string, unknown>>("/api/llm/test");
}
