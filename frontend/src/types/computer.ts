export type TaskStatus = "QUEUED" | "PLANNING" | "OBSERVING" | "ACTING" | "VERIFYING" | "WAITING_FOR_CONFIRMATION" | "PAUSED" | "COMPLETED" | "FAILED" | "CANCELLED" | "INTERRUPTED";
export interface ComputerSettings {
  enabled: boolean; mode: "ASSISTED" | "SEMI_AUTONOMOUS" | "AUTONOMOUS"; simulation: boolean;
  screen_access: boolean; accessibility_access: boolean; browser_access: boolean; filesystem_access: boolean;
  terminal_access: boolean; vision_enabled: boolean; require_confirmation: boolean;
  task_timeout_seconds: number; max_task_steps: number; local_vision_model: string; allow_cloud_planning: boolean;
  action_visibility_mode: "FAST" | "NORMAL" | "HUMAN_VISIBLE"; watch_nova: boolean;
}
export interface SystemPermissions {
  accessibility: boolean; screen_recording: boolean; automation: string; helper_available?: boolean; helper_path?: string;
  settings?: ComputerSettings;
  permission_state?: { accessibility?: string; screen_recording?: string; browser_access?: string; filesystem_access?: string };
}
export interface TaskProgress {
  id: string; goal: string; status: TaskStatus; mode: string; currentStep: string;
  stepIndex: number; totalSteps: number; completed: string[]; simulation: boolean;
  plan?: string[];
  confirmation?: { tool: string; arguments: Record<string, unknown>; scope_extension?: boolean };
  timeline?: { type: string; stepId: string; timestamp: string; summary: string }[];
}
