import type { WSMessage } from "./types";

const BACKOFF_MIN_MS = 1_000;
const BACKOFF_MAX_MS = 30_000;

export type WSHandlers = {
  onMessage: (msg: WSMessage) => void;
  onConnectionChange: (connected: boolean) => void;
  /** Fired after every (re)connect so callers can re-fetch REST state (API_SPEC §3). */
  onReconnect?: () => void;
};

/**
 * Subscribes to the orchestrator /ws stream with auto-reconnect and
 * exponential backoff (1s → 30s cap). Returns an unsubscribe function.
 * Reconnect + backoff live here so components stay display-only.
 */
export function subscribe(baseUrl: string, handlers: WSHandlers): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let attempt = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const connect = () => {
    if (closed) return;
    ws = new WebSocket(baseUrl);

    ws.onopen = () => {
      attempt = 0;
      handlers.onConnectionChange(true);
      handlers.onReconnect?.();
    };

    ws.onmessage = (event) => {
      try {
        handlers.onMessage(JSON.parse(event.data) as WSMessage);
      } catch {
        // malformed frame: drop, connection stays up
      }
    };

    ws.onclose = () => {
      handlers.onConnectionChange(false);
      if (closed) return;
      const delay = Math.min(BACKOFF_MIN_MS * 2 ** attempt, BACKOFF_MAX_MS);
      attempt += 1;
      timer = setTimeout(connect, delay);
    };

    ws.onerror = () => {
      ws?.close();
    };
  };

  connect();

  return () => {
    closed = true;
    if (timer) clearTimeout(timer);
    handlers.onConnectionChange(false);
    ws?.close();
  };
}
