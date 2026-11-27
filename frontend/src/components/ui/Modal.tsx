"use client";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

/** A dialog on top of everything (rendered into <body>, so no page, header or sidebar can cover it). Escape or a
 *  click on the backdrop closes it. */
export default function Modal({ onClose, children, width = 600 }: { onClose: () => void; children: React.ReactNode; width?: number }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", onKey); document.body.style.overflow = prev; };
  }, [onClose]);
  if (!mounted) return null;
  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-start justify-center overflow-auto bg-black/40 p-4 sm:p-6" onClick={onClose} role="dialog" aria-modal="true">
      <div className="my-auto w-full rounded-2xl border border-line bg-panel p-5 shadow-xl sm:p-6" style={{ maxWidth: width }} onClick={(e) => e.stopPropagation()}>
        {children}
      </div>
    </div>,
    document.body,
  );
}
