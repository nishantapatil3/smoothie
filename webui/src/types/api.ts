// Type definitions for the Smoothie API
export interface User {
  id: string;
  username: string;
  created_at: string;
  tier: "free" | "standard" | "pro";
  usage_minutes: number;
  tier_limit_minutes: number | null;
  tier_remaining_minutes: number | null;
  is_admin: boolean;
}

export interface Recording {
  id: string;
  user_id: string;
  title: string | null;
  filename: string;
  content_type: string | null;
  language: string | null;
  duration_sec: number | null;
  created_at: string;
  status: JobStage;
}

export type JobStage = "pending" | "transcribing" | "summarizing" | "completed" | "failed" | "cancelled";

export interface RecordingListItem {
  id: string;
  title: string | null;
  status: JobStage;
  language: string | null;
  duration_sec: number | null;
  created_at: string;
  progress: number;
}

export interface RecordingDetail extends Recording {
  full_text: string | null;
  segments: Segment[] | null;
  summary: string | null;
  summary_provider: string | null;
  progress: number;
  error: string | null;
}

export interface Segment {
  start: number;
  end: number;
  text: string;
}

export interface HealthResponse {
  status: string;
  version: string;
  transcription_model: string;
  summary_url: string;
  summary_model: string;
}

export interface Settings {
  host: string;
  port: number;
  transcription_model: string;
  transcription_language: string | null;
  vad_enabled: boolean;
  vad_threshold: number;
  vad_min_speech_ms: number;
  vad_min_silence_ms: number;
  vad_speech_pad_ms: number;
  vad_chunk_target_sec: number;
  vad_chunk_max_sec: number;
  summary_url: string;
  summary_key: string;
  summary_model: string;
  job_poll_interval: number;
  auth_access_ttl_minutes: number;
  auth_refresh_ttl_days: number;
  auth_min_password_len: number;
  admin_usernames: string[];
  trim_enabled: boolean;
  trim_threshold: number;
  trim_min_silence: number;
  tune_enabled: boolean;
  tune_beam_size: number | null;
  tune_condition_on_previous_text: boolean | null;
  tune_no_speech_threshold: number | null;
  tune_hallucination_silence_threshold: number | null;
}

export type SettingsUpdate = Partial<Settings>;

export interface AdminUser {
  id: string;
  username: string;
  tier: string;
  usage_minutes: number;
}

export interface TierUpdate {
  tier: "free" | "standard" | "pro";
}

export interface AuthTokens {
  token: string;
  refresh_token: string;
}

export interface RefreshRequest {
  refresh_token: string;
}

export interface LogoutRequest {
  refresh_token: string;
}

export interface RegisterRequest {
  username: string;
  password: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface UploadRequest {
  audio: File;
  title?: string;
  language?: string;
}

export interface SSEEvent {
  stage: JobStage | null;
  progress: number;
  error: string | null;
}