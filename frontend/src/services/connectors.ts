import { request } from "./persistence";
import type { ConnectorActionResult, ConnectorState } from "../types/connectors";

export const connectorsApi = {
  state: () => request<ConnectorState>("/connectors"),
  connector: (name: string) => request<ConnectorState["connectors"][number]>(`/connectors/${encodeURIComponent(name)}`),
  connect: (name: string) => request<ConnectorActionResult>(`/connectors/${encodeURIComponent(name)}/connect`, "POST", {}),
  disconnect: (name: string) => request<ConnectorActionResult>(`/connectors/${encodeURIComponent(name)}/disconnect`, "POST"),
  refresh: (name: string) => request<ConnectorState["connectors"][number]>(`/connectors/${encodeURIComponent(name)}/refresh`, "POST"),
};
