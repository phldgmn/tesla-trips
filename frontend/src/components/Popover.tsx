/** Simple popover/tooltip component for hover display.
 *
 * Shows content positioned relative to a trigger element.
 * Closes on click outside, Escape, or when trigger is unmounted.
 */

import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

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

interface Coords {
  top: number;
  left: number;
}

export function Popover({
  content,
  children,
  position = "top",
  offset = 8,
  maxWidth = 300,
}: PopoverProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [coords, setCoords] = useState<Coords | null>(null);
  const wrapperRef = useRef<HTMLSpanElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  // Measure trigger position only after it is known to be open and mounted;
  // never reads `.current` during the render phase itself.
  useLayoutEffect(() => {
    if (!isOpen || !wrapperRef.current) {
      setCoords(null);
      return;
    }
    const rect = wrapperRef.current.getBoundingClientRect();
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;

    let top = 0;
    let left = 0;
    switch (position) {
      case "top":
        top = rect.top + scrollY - offset;
        left = rect.left + scrollX + rect.width / 2;
        break;
      case "bottom":
        top = rect.bottom + scrollY + offset;
        left = rect.left + scrollX + rect.width / 2;
        break;
      case "left":
        top = rect.top + scrollY + rect.height / 2;
        left = rect.left + scrollX - offset;
        break;
      case "right":
        top = rect.top + scrollY + rect.height / 2;
        left = rect.right + scrollX + offset;
        break;
    }
    setCoords({ top, left });
  }, [isOpen, position, offset]);

  // Close on click outside
  useEffect(() => {
    if (!isOpen) return;
    const handleClickOutside = (event: MouseEvent) => {
      if (
        wrapperRef.current &&
        !wrapperRef.current.contains(event.target as Node) &&
        popoverRef.current &&
        !popoverRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
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
    top: coords?.top ?? 0,
    left: coords?.left ?? 0,
    transform:
      position === "left" || position === "right"
        ? "translateY(-50%)"
        : "translateX(-50%)",
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
    pointerEvents: "auto",
  };

  // Arrow positioning
  const arrowStyle: React.CSSProperties = {
    position: "absolute",
    width: 0,
    height: 0,
    border: "6px solid transparent",
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
      {isOpen && coords && (
        <div ref={popoverRef} style={popoverStyle} role="tooltip">
          <div style={arrowStyle} />
          {content}
        </div>
      )}
    </>
  );
}

export default Popover;
