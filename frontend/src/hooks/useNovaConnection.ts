import { useCallback, useEffect, useRef, useState } from "react";

import { apiBaseUrl, fetchHealth, websocketUrl } from "../services/api";
import type { ConnectionDiagnostics, ConnectionState, HealthPayload, NovaEvent } from "../types/nova";

export function useNovaConnection(onEvent?: (event: NovaEvent) => void) {
  const [connection, setConnection] = useState<ConnectionState>("checking");
  const [health, setHealth] = useState<HealthPayload | null>(null);
  const [lastEvent, setLastEvent] = useState<NovaEvent | null>(null);
  const [diagnostics, setDiagnostics] = useState<ConnectionDiagnostics>(() => ({
    backend_url: apiBaseUrl(), http: { state: "checking" }, websocket: { state: "checking" }, retry_count: 0,
  }));
  const socketRef = useRef<WebSocket | null>(null);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    const controller = new AbortController();
    let socket: WebSocket | null = null;
    let retryTimer: number | undefined;
    let retryDelay = 750;
    let retryCount = 0;
    let generation = 0;
    let httpReady = false;
    let websocketReady = false;
    let httpFailed = false;
    let websocketFailed = false;
    let authenticationRequired = false;
    const updateConnection = () => {
      if (httpReady && websocketReady) setConnection("connected");
      else if (authenticationRequired) setConnection("auth_required");
      else if (httpFailed && websocketFailed) setConnection("offline");
      else setConnection("checking");
    };
    const scheduleRetry = () => {
      if (controller.signal.aborted) return;
      if (authenticationRequired) return;
      window.clearTimeout(retryTimer);
      setConnection("checking");
      retryTimer = window.setTimeout(connect, retryDelay);
      retryDelay = Math.min(8000, retryDelay * 2);
      retryCount += 1;
      setDiagnostics((current) => ({ ...current, retry_count: retryCount }));
    };

    const connect = () => {
      if (controller.signal.aborted) return;
      const currentGeneration = ++generation;
      authenticationRequired = false;
      httpReady = false; websocketReady = false; httpFailed = false; websocketFailed = false;
      setConnection("checking");
      setDiagnostics((current) => ({ ...current, backend_url: apiBaseUrl(), http: { state: "checking" }, websocket: { state: "checking" } }));
      fetchHealth(controller.signal)
        .then((payload) => {
          if (!controller.signal.aborted) {
            setHealth(payload);
            httpReady = true; httpFailed = false;
            setDiagnostics((current) => ({ ...current, http: { state: "connected", status: 200 } }));
            updateConnection();
          }
        })
        .catch((error: unknown) => {
          if (!controller.signal.aborted) {
            const reason = error instanceof Error ? error.message : "HTTP health request failed";
            if (reason.includes("(401)")) {
              authenticationRequired = true;
              setDiagnostics((current) => ({ ...current, http: { state: "failed", status: 401, reason: "NOVA browser session required" }, websocket: { state: "failed", close_code: 1008, reason: "NOVA browser session required" } }));
              setConnection("auth_required");
              return;
            }
            httpReady = false; httpFailed = true;
            setDiagnostics((current) => ({ ...current, http: { state: "failed", reason } }));
            updateConnection();
          }
        });

      try {
        socket = new WebSocket(websocketUrl());
        socketRef.current = socket;
        socket.onopen = () => {
          if (!controller.signal.aborted && currentGeneration === generation) {
            websocketFailed = false;
            setDiagnostics((current) => ({ ...current, websocket: { state: "checking" } }));
          }
        };
        socket.onmessage = (message) => {
          if (controller.signal.aborted) return;
          try {
            const event = JSON.parse(message.data) as NovaEvent;
            setLastEvent(event);
            if (event.type === "system.ready" && currentGeneration === generation) {
              websocketReady = true; websocketFailed = false; retryDelay = 750; retryCount = 0;
              setDiagnostics((current) => ({ ...current, websocket: { state: "connected" }, retry_count: 0 }));
              updateConnection();
            }
            // Deliver every frame. React can batch lastEvent updates, losing tokens.
            onEventRef.current?.(event);
          } catch {
            setLastEvent(null);
          }
        };
        socket.onclose = (event) => {
          if (!controller.signal.aborted && currentGeneration === generation) {
            if (event.code === 1008) {
              authenticationRequired = true;
              setConnection("auth_required");
              setDiagnostics((current) => ({ ...current, websocket: { state: "failed", close_code: event.code, reason: "NOVA browser session required" } }));
              return;
            }
            websocketReady = false; websocketFailed = true;
            setDiagnostics((current) => ({ ...current, websocket: { state: "failed", close_code: event.code, reason: event.reason || "WebSocket closed" } }));
            updateConnection();
            scheduleRetry();
          }
        };
        socket.onerror = () => {
          if (!controller.signal.aborted && currentGeneration === generation) {
            websocketFailed = true;
            setDiagnostics((current) => ({ ...current, websocket: { state: "failed", reason: "WebSocket connection failed" } }));
            updateConnection();
          }
        };
      } catch {
        socket = null;
        websocketFailed = true;
        setDiagnostics((current) => ({ ...current, websocket: { state: "failed", reason: "WebSocket construction failed" } }));
        updateConnection();
        scheduleRetry();
      }
    };
    connect();

    return () => {
      controller.abort();
      window.clearTimeout(retryTimer);
      socket?.close();
      socketRef.current = null;
    };
  }, []);

  const send = useCallback((type: string, payload: Record<string, unknown> = {}) => {
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ type, ...payload }));
      return true;
    }
    return false;
  }, []);

  return { connection, health, lastEvent, diagnostics, send };
}
