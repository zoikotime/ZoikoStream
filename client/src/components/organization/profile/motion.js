// Framer Motion variants shared by the Organization & Workspaces page.
//
// The page wraps itself in <MotionConfig reducedMotion="user">, so every transform and
// opacity animation below is automatically neutered for visitors who ask for reduced
// motion — no per-component guard needed.

// Standard easing across the page: a soft decelerate, nothing bouncy.
const EASE = [0.22, 0.61, 0.36, 1];

export const fadeUp = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { duration: 0.42, ease: EASE } },
};

export const fadeIn = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { duration: 0.35, ease: EASE } },
};

// Container that reveals its children one after another. `delayChildren` lets a section
// start after the block above it has landed.
export const stagger = (staggerChildren = 0.06, delayChildren = 0) => ({
  hidden: {},
  show: { transition: { staggerChildren, delayChildren } },
});

// Grid/list items: the same fadeUp, but driven by the parent's stagger.
export const item = {
  hidden: { opacity: 0, y: 14 },
  show: { opacity: 1, y: 0, transition: { duration: 0.38, ease: EASE } },
};

// Interactive cards — a small lift on hover, a press on tap. Shared so every clickable
// surface on the page responds identically.
export const liftHover = { y: -3, transition: { duration: 0.18, ease: EASE } };
export const liftTap = { y: -1, scale: 0.995 };

// One reveal spec for sections below the fold.
export const inView = { once: true, amount: 0.15, margin: "0px 0px -40px 0px" };
