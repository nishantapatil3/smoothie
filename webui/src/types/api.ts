// Type definitions for the Smoothie API
export type JobStage = "pending" | "transcribing" | "summarizing" | "completed" | "failed" | "cancelled";

export interface RecordingListItem {
  id: string;
  title: string | null;
  generated_title: string | null;
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

export interface Recording {
  id: string;
  title: string | null;
  generated_title: string | null;
  filename: string;
  original_filename: string | null;
  output_path: string | null;
  content_type: string | null;
  language: string | null;
  duration_sec: number | null;
  created_at: string;
  status: JobStage;
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
  summary_provider: "api" | "codex";
}

export interface Settings {
  host: string;
  port: number;
  transcription_model: string;
  transcription_device: "auto" | "cpu";
  transcription_gpu_device: number;
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
  summary_codex_model: string;
  summary_codex_timeout: number;
  summary_model: string;
  summary_provider: "api" | "codex";
  job_poll_interval: number;
  watch_dir: string;
  output_dir: string;
  watch_enabled: boolean;
  watch_poll_interval: number;
  watch_stable_seconds: number;
  trim_enabled: boolean;
  trim_threshold: number;
  trim_min_silence: number;
  tune_enabled: boolean;
  tune_beam_size: number | null;
  tune_condition_on_previous_text: boolean | null;
  tune_no_speech_threshold: number | null;
}

export type SettingsUpdate = Partial<Settings>;

export interface UploadRequest {
  audio: File;
  title?: string;
  language?: string;
}

export interface SSEEvent {
  stage: JobStage | null;
  progress: number;
  error: string | null;
  generated_title: string | null;
}