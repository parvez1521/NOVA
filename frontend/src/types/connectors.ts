export type ConnectorStatus = "CONNECTED" | "AVAILABLE" | "NEEDS_SETUP" | "EXPIRED" | "UNAVAILABLE" | "REQUIRES_REAUTH" | "PERMISSION_DENIED" | "PARTIAL" | "ERROR";
export type ConnectorAuth = "none" | "oauth2" | "api_key" | "bot_token" | "mcp";

export interface ConnectorHealth {
  ok: boolean;
  detail: string;
}

export interface ConnectorSnapshot {
  name: string;
  icon: string;
  description: string;
  auth: ConnectorAuth;
  permissions: string[];
  capabilities: string[];
  implemented: boolean;
  status: ConnectorStatus;
  connected: boolean;
  detail: string;
  health: ConnectorHealth;
}

export interface ConnectorState {
  enabled: boolean;
  available: boolean;
  connected: number;
  connectors: ConnectorSnapshot[];
}

export interface ConnectorActionResult {
  name: string;
  status: ConnectorStatus;
  connected: boolean;
  detail: string;
  authorization_url?: string | null;
}
