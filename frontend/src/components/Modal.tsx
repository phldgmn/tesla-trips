/** Generische Modal-Komponente (Overlay + Panel), ohne Portal.
 *
 * Wird für alle "versteckten" Bereiche verwendet, die per Button geöffnet
 * werden: Zeitplan (Vollbild), Fahrzeug & Ladestand, Ignorierte Fähren.
 * Kein `createPortal` nötig – `position: fixed` legt sich unabhängig von
 * der DOM-Verschachtelung über den gesamten Viewport, solange kein
 * Vorfahre `transform`/`filter`/`will-change` setzt (im gesamten Frontend
 * nicht der Fall, siehe `App.tsx`/`TripSummary.tsx`).
 */

import { useEffect, type ReactNode } from "react";

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  /** "fullscreen" nutzt fast die gesamte Viewport-Fläche (z. B. für breite
   *  Tabellen wie den Zeitplan), "default" ist ein zentriertes, begrenztes
   *  Panel für Formulare. */
  size?: "default" | "fullscreen";
  children: ReactNode;
}

/** Zeigt `children` in einem zentrierten Overlay-Panel, sobald `open` true
 *  ist. Schließt bei Klick auf das Overlay, den ✕-Button oder Escape. */
export function Modal({
  open,
  onClose,
  title,
  size = "default",
  children,
}: ModalProps) {
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="presentation"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        background: "rgba(15, 23, 42, 0.45)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: size === "fullscreen" ? "1.5rem" : "2rem",
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "white",
          borderRadius: "8px",
          boxShadow: "0 8px 32px rgba(0, 0, 0, 0.28)",
          display: "flex",
          flexDirection: "column",
          fontFamily: "system-ui, -apple-system, sans-serif",
          width: size === "fullscreen" ? "100%" : "420px",
          height: size === "fullscreen" ? "100%" : "auto",
          maxWidth: size === "fullscreen" ? "1400px" : "90vw",
          maxHeight: "90vh",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "1rem 1.25rem",
            borderBottom: "1px solid #e5e7eb",
            flexShrink: 0,
          }}
        >
          <h2 style={{ margin: 0, fontSize: "1.1rem" }}>{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Schließen"
            style={{
              padding: "0.25rem 0.6rem",
              background: "#f3f4f6",
              border: "1px solid #d1d5db",
              borderRadius: "4px",
              cursor: "pointer",
              fontSize: "0.9rem",
              lineHeight: 1,
            }}
          >
            ✕
          </button>
        </div>
        <div
          style={{
            padding: "1.25rem",
            overflowY: "auto",
            fontSize: "0.9rem",
            lineHeight: 1.5,
          }}
        >
          {children}
        </div>
      </div>
    </div>
  );
}

export default Modal;
