/** API-Client für den `POST /trips`-Endpunkt.
 *
 * Ruft den Endpunkt über den Vite-Dev-Proxy `/api` auf (siehe
 * `vite.config.ts`), der auf das lokale FastAPI-Backend
 * (`http://localhost:8000`, siehe `README.md`) weiterleitet. So bleibt das
 * Frontend frei von einer fest kodierten Backend-Basis-URL und es ist kein
 * CORS-Setup im Backend nötig.
 */

import type { TripSimulationResult } from "../types";
import type { TripRequestPayload } from "../types/trip-request";

/** Fehler, der bei einer fehlgeschlagenen `/trips`-Anfrage geworfen wird. */
export class TripApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "TripApiError";
  }
}

/** Sendet eine Reiseanfrage an das Backend und liefert das Simulationsergebnis. */
export async function submitTripRequest(
  payload: TripRequestPayload,
  signal?: AbortSignal,
): Promise<TripSimulationResult> {
  let response: Response;
  try {
    response = await fetch("/api/trips", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new TripApiError(
      "Backend nicht erreichbar. Läuft der Server unter http://localhost:8000?",
    );
  }

  if (!response.ok) {
    let detail = `Anfrage fehlgeschlagen (Status ${response.status})`;
    try {
      const body: unknown = await response.json();
      if (body && typeof body === "object" && "detail" in body) {
        const rawDetail = body.detail;
        if (typeof rawDetail === "string") {
          detail = rawDetail;
        }
      }
    } catch {
      // Antwort war kein JSON – leeren body auf Backend-Connectivity prüfen
      if (response.status === 500) {
        detail =
          "Backend nicht erreichbar oder fehlerhaft. " +
          "Stelle sicher, dass der Server unter http://localhost:8000 läuft " +
          "(siehe README.md für Start-Kommando).";
      }
    }
    throw new TripApiError(detail, response.status);
  }

  // Server validiert die Response bereits gegen `TripSimulationResultAPI`
  // (Pydantic, 2xx-Status garantiert Schema-Konformität) – kein Zod im Projekt,
  // daher hier ein einmaliger, benannter Cast statt inline.
  const result = (await response.json()) as TripSimulationResult;
  return result;
}
