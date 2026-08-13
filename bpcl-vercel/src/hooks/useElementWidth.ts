import { useEffect, useState, type RefObject } from 'react';

/**
 * The rendered width of an element, tracked through resizes.
 *
 * The mimic diagram draws in SVG user units and needs a real pixel width to lay
 * out against: scaling a fixed viewBox instead would stretch the station labels
 * and axis text along with the geometry, which is exactly the thing that must
 * not scale.
 */
export function useElementWidth<T extends HTMLElement>(
  ref: RefObject<T | null>,
  fallback: number,
): number {
  const [width, setWidth] = useState(fallback);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    const observer = new ResizeObserver(([entry]) => {
      const next = entry.contentRect.width;
      // Sub-pixel resize noise would re-render the whole diagram for nothing.
      setWidth((current) => (Math.abs(current - next) > 0.5 ? next : current));
    });
    observer.observe(element);
    setWidth(element.getBoundingClientRect().width || fallback);

    return () => observer.disconnect();
  }, [ref, fallback]);

  return width;
}
