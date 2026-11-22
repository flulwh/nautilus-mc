import { animate, useInView, useReducedMotion } from "motion/react";
import { createContext, useContext, useEffect, useRef, useState } from "react";

/**
 * Nautilus motion system — "instrument panel" language.
 *
 * The panel is a rack-mount controller: it should feel like a machine powering
 * on, not like a landing page fading in. So the vocabulary is deliberately
 * narrow:
 *
 *   - relay snap    : cubic-bezier(.2, .9, .25, 1) — decisive, no overshoot
 *   - instrument    : cubic-bezier(.4, 0, .2, 1)   — for things that measure
 *   - settle        : spring, low bounce            — for things that arrive
 *
 * Everything animates transform/opacity only, so it stays on the compositor
 * and never triggers layout on a page that also polls the server list every
 * 4 seconds.
 */

export const EASE = {
  /** Relay snap: a switch closing. Used for entrances and state flips. */
  snap: [0.2, 0.9, 0.25, 1] as const,
  /** Instrument settle: for measured, continuous values. */
  measure: [0.4, 0, 0.2, 1] as const,
  /** Exit: leave faster than you arrive. */
  exit: [0.4, 0, 1, 1] as const,
};

export const DUR = {
  fast: 0.16,
  base: 0.28,
  slow: 0.44,
  /** Stagger step between siblings. */
  step: 0.035,
};

/* ------------------------------------------------------------------ variants */

/** Full-page enter/exit. Subtle rise + fade, never a slide across the screen. */
export const pageVariants = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -4 },
  transition: { duration: DUR.base, ease: EASE.snap },
};

/** Parent that releases its children one after another — "panel powering up". */
export const staggerContainer = (step = DUR.step, delayChildren = 0) => ({
  hidden: {},
  show: {
    transition: { staggerChildren: step, delayChildren },
  },
});

/** Child of a stagger container. */
export const staggerItem = {
  hidden: { opacity: 0, y: 10 },
  show: {
    opacity: 1,
    y: 0,
    transition: { duration: DUR.base, ease: EASE.snap },
  },
};

/** A card / panel that settles in. */
export const cardVariants = {
  hidden: { opacity: 0, y: 12, scale: 0.985 },
  show: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { duration: DUR.slow, ease: EASE.snap },
  },
};

/** A row in a table or list. */
export const rowVariants = {
  hidden: { opacity: 0, x: -8 },
  show: { opacity: 1, x: 0, transition: { duration: DUR.base, ease: EASE.snap } },
};

/** Scale-in for popovers and modals that Mantine renders itself. */
export const popVariants = {
  hidden: { opacity: 0, scale: 0.96, y: -4 },
  show: { opacity: 1, scale: 1, y: 0, transition: { duration: DUR.fast, ease: EASE.snap } },
  exit: { opacity: 0, scale: 0.97, transition: { duration: DUR.fast, ease: EASE.exit } },
};

/* -------------------------------------------------------------------- context */

/**
 * Global motion switch. When the OS asks for reduced motion we keep the app
 * usable but strip the choreography — elements appear instantly instead of
 * travelling. Components read this instead of calling useReducedMotion
 * individually so the fallback is consistent.
 */
const MotionCtx = createContext<{ reduced: boolean }>({ reduced: false });

export function MotionProvider({ children }: { children: React.ReactNode }) {
  const reduced = !!useReducedMotion();
  return <MotionCtx.Provider value={{ reduced }}>{children}</MotionCtx.Provider>;
}

export function useMotionPref() {
  return useContext(MotionCtx).reduced;
}

/* ------------------------------------------------------------------ counting */

/**
 * Roll a number up like a mechanical counter instead of snapping it to the
 * new value. Used for the dashboard stat strip, where the number changes on
 * every 4-second poll and a hard swap is the only thing you actually notice.
 */
export function useCountUp(value: number, duration = 0.6) {
  const reduced = useMotionPref();
  const [display, setDisplay] = useState(value);
  const prev = useRef(value);

  useEffect(() => {
    if (reduced) {
      setDisplay(value);
      prev.current = value;
      return;
    }
    const from = prev.current;
    prev.current = value;
    if (from === value) return;

    const controls = animate(from, value, {
      duration,
      ease: EASE.measure,
      onUpdate: (v) => setDisplay(v),
    });
    return () => controls.stop();
  }, [value, duration, reduced]);

  return display;
}

/**
 * Reveal-on-scroll for long lists. Returns a ref plus a boolean; attach the
 * ref to the container and conditionally render the children.
 */
export function useRevealOnScroll<T extends HTMLElement>(amount = 0.15) {
  const reduced = useMotionPref();
  const ref = useRef<T>(null);
  const seen = useInView(ref, { once: true, amount });
  return { ref, shown: reduced ? true : seen };
}
