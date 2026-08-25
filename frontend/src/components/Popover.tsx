/** Simple popover/tooltip component for hover display.
 *
 * Shows content positioned relative to a trigger element. Renders into a
 * portal on `document.body` so it is never clipped by an ancestor's
 * `overflow`, and is measured/clamped after mount so it never renders
 * partially off-screen.
 */

import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

export interface PopoverProps {
  /** Content to show in the popover */
  content: ReactNode;
  /** Children - the trigger element (wrapped in a span) */
  children: ReactNode;
  /** Position of popover relative to trigger */
  position?: "top" | "bottom" | "left" | "right";
  /** Offset from trigger in pixels */
  offset?: number;
  /** Maximum width of popover */
  maxWidth?: number;
}

/** Anchor point (in viewport coordinates) the popover box is transformed
 *  around - NOT its final top-left corner (see `transformFor`). */
interface Anchor {
  top: number;
  left: number;
}

/** Minimum distance the popover must keep from the viewport edge. */
const VIEWPORT_MARGIN = 8;

/** CSS `transform` that shifts a box anchored at the trigger's edge/center
 *  fully clear of the trigger itself, on both axes. Only translating the
 *  perpendicular axis (as a prior version of this component did) leaves the
 *  box overlapping the trigger, which makes the browser fire `mouseleave`
 *  on the trigger the instant the popover renders on top of the cursor -
 *  producing an open/close flicker loop. */
function transformFor(position: PopoverProps["position"]): string {
  switch (position) {
    case "top":
      return "translate(-50%, -100%)";
    case "bottom":
      return "translate(-50%, 0)";
    case "left":
      return "translate(-100%, -50%)";
    case "right":
    default:
      return "translate(0, -50%)";
  }
}

function anchorFor(
  rect: DOMRect,
  position: PopoverProps["position"],
  offset: number,
): Anchor {
  const scrollX = window.scrollX;
  const scrollY = window.scrollY;
  switch (position) {
    case "top":
      return {
        top: rect.top + scrollY - offset,
        left: rect.left + scrollX + rect.width / 2,
      };
    case "bottom":
      return {
        top: rect.bottom + scrollY + offset,
        left: rect.left + scrollX + rect.width / 2,
      };
    case "left":
      return {
        top: rect.top + scrollY + rect.height / 2,
        left: rect.left + scrollX - offset,
      };
    case "right":
    default:
      return {
        top: rect.top + scrollY + rect.height / 2,
        left: rect.right + scrollX + offset,
      };
  }
}

export function Popover({
  content,
  children,
  position = "top",
  offset = 8,
  maxWidth = 300,
}: PopoverProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [anchor, setAnchor] = useState<Anchor | null>(null);
  // Extra pixel correction applied on top of `anchor` once the popover's
  // own size is known, to keep it fully inside the viewport.
  const [clamp, setClamp] = useState({ x: 0, y: 0 });
  const wrapperRef = useRef<HTMLSpanElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  const recomputeAnchor = () => {
    if (!wrapperRef.current) return;
    setAnchor(
      anchorFor(wrapperRef.current.getBoundingClientRect(), position, offset),
    );
  };

  // Measure trigger position only after it is known to be open and mounted;
  // never reads `.current` during the render phase itself. While closed,
  // `anchor`/`clamp` are simply left stale - harmless, since rendering is
  // gated on `isOpen && anchor` below.
  useLayoutEffect(() => {
    if (!isOpen) return;
    recomputeAnchor();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, position, offset]);

  // Once the popover itself has rendered at `anchor`, measure its actual
  // on-screen box and nudge it back inside the viewport if it overflows.
  useLayoutEffect(() => {
    if (!isOpen || !anchor || !popoverRef.current) return;
    const rect = popoverRef.current.getBoundingClientRect();
    let dx = 0;
    let dy = 0;
    if (rect.left < VIEWPORT_MARGIN) {
      dx = VIEWPORT_MARGIN - rect.left;
    } else if (rect.right > window.innerWidth - VIEWPORT_MARGIN) {
      dx = window.innerWidth - VIEWPORT_MARGIN - rect.right;
    }
    if (rect.top < VIEWPORT_MARGIN) {
      dy = VIEWPORT_MARGIN - rect.top;
    } else if (rect.bottom > window.innerHeight - VIEWPORT_MARGIN) {
      dy = window.innerHeight - VIEWPORT_MARGIN - rect.bottom;
    }
    setClamp((prev) =>
      prev.x === dx && prev.y === dy ? prev : { x: dx, y: dy },
    );
  }, [isOpen, anchor]);

  // Keep the popover glued to the trigger while open (scroll/resize),
  // instead of drifting to a stale position.
  useEffect(() => {
    if (!isOpen) return;
    const handle = () => recomputeAnchor();
    window.addEventListener("scroll", handle, { capture: true, passive: true });
    window.addEventListener("resize", handle);
    return () => {
      window.removeEventListener("scroll", handle, { capture: true });
      window.removeEventListener("resize", handle);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  // Close on Escape
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsOpen(false);
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen]);

  const popoverStyle: React.CSSProperties = {
    position: "fixed",
    top: anchor?.top ?? 0,
    left: anchor?.left ?? 0,
    transform: `${transformFor(position)} translate(${clamp.x}px, ${clamp.y}px)`,
    maxWidth,
    background: "#1e1e1e",
    color: "#e0e0e0",
    border: "1px solid #333",
    borderRadius: "6px",
    padding: "0.5rem 0.75rem",
    fontSize: "0.8rem",
    lineHeight: "1.5",
    zIndex: 1000,
    boxShadow: "0 4px 12px rgba(0,0,0,0.4)",
    whiteSpace: "nowrap",
    // Purely informational - never intercept pointer events. This also
    // guarantees the popover can never itself trigger the trigger's
    // mouseleave (which caused the open/close flicker before the
    // transform fix above), regardless of any future positioning change.
    pointerEvents: "none",
  };

  // Arrow positioning - only meaningful while unclamped; hide it once the
  // box has been nudged away from its anchor so it doesn't point at empty
  // space.
  const arrowStyle: React.CSSProperties = {
    position: "absolute",
    width: 0,
    height: 0,
    border: "6px solid transparent",
    visibility: clamp.x === 0 && clamp.y === 0 ? "visible" : "hidden",
  };

  if (position === "top") {
    arrowStyle.bottom = "-12px";
    arrowStyle.left = "50%";
    arrowStyle.transform = "translateX(-50%)";
    arrowStyle.borderTopColor = "#1e1e1e";
  } else if (position === "bottom") {
    arrowStyle.top = "-12px";
    arrowStyle.left = "50%";
    arrowStyle.transform = "translateX(-50%)";
    arrowStyle.borderBottomColor = "#1e1e1e";
  } else if (position === "left") {
    arrowStyle.right = "-12px";
    arrowStyle.top = "50%";
    arrowStyle.transform = "translateY(-50%)";
    arrowStyle.borderLeftColor = "#1e1e1e";
  } else if (position === "right") {
    arrowStyle.left = "-12px";
    arrowStyle.top = "50%";
    arrowStyle.transform = "translateY(-50%)";
    arrowStyle.borderRightColor = "#1e1e1e";
  }

  return (
    <>
      <span
        ref={wrapperRef}
        onMouseEnter={() => setIsOpen(true)}
        onMouseLeave={() => setIsOpen(false)}
      >
        {children}
      </span>
      {isOpen &&
        anchor &&
        createPortal(
          <div ref={popoverRef} style={popoverStyle} role="tooltip">
            <div style={arrowStyle} />
            {content}
          </div>,
          document.body,
        )}
    </>
  );
}

export default Popover;
