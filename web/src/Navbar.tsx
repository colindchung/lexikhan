import type { MouseEvent } from "react";
import { navigate, useRoute } from "./routes";
function follow(event: MouseEvent<HTMLAnchorElement>, disabled = false) {
  if (disabled) {
    event.preventDefault();
    return;
  }
  if (
    event.button === 0 &&
    !event.metaKey &&
    !event.ctrlKey &&
    !event.shiftKey &&
    !event.altKey
  ) {
    event.preventDefault();
    navigate(event.currentTarget.pathname);
  }
}
export function Brand() {
  return (
    <a
      className="brand"
      href="/practice"
      aria-label="Lexikhan home"
      onClick={follow}
    >
      <span className="brand-mark" aria-hidden="true">
        L<span>•</span>
      </span>
      lexikhan
    </a>
  );
}
export function Navbar({
  signOut,
  disabled = false,
}: {
  signOut?: () => void;
  disabled?: boolean;
}) {
  const route = useRoute();
  return (
    <header className="site-header">
      <Brand />
      <nav aria-label="Main navigation">
        {(
          [
            ["practice", "Practice"],
            ["chats", "Language chat"],
            ["reminders", "Reminders"],
          ] as const
        ).map(([page, label]) => (
          <a
            key={page}
            href={`/${page}`}
            aria-current={route.page === page ? "page" : undefined}
            aria-disabled={disabled || undefined}
            onClick={(event) => follow(event, disabled)}
          >
            {label}
          </a>
        ))}
      </nav>
      {signOut && (
        <button
          className="text-button nav-signout"
          onClick={signOut}
          disabled={disabled}
        >
          Sign out
        </button>
      )}
    </header>
  );
}
