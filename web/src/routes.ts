import { useSyncExternalStore } from "react";
export type Route = {
  page: "practice" | "reminders" | "onboarding" | "chats" | "not-found";
  chatId?: string;
};
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function parseRoute(path: string): Route {
  if (path === "/" || path === "/practice") return { page: "practice" };
  if (path === "/reminders") return { page: "reminders" };
  if (path === "/onboarding") return { page: "onboarding" };
  if (path === "/chats") return { page: "chats" };
  const match = /^\/chats\/([^/]+)$/.exec(path);
  if (match && uuid.test(match[1]))
    return { page: "chats", chatId: match[1].toLowerCase() };
  return { page: "not-found" };
}
export function returnPath(value: unknown): string {
  if (
    typeof value !== "string" ||
    !value.startsWith("/") ||
    value.startsWith("//")
  )
    return "/practice";
  const url = new URL(value, "https://lexikhan.invalid");
  if (
    url.origin !== "https://lexikhan.invalid" ||
    parseRoute(url.pathname).page === "not-found"
  )
    return "/practice";
  return url.pathname + url.search;
}
export function navigate(path: string, replace = false) {
  if (location.pathname + location.search === path) return;
  history[replace ? "replaceState" : "pushState"]({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}
function subscribe(callback: () => void) {
  window.addEventListener("popstate", callback);
  return () => window.removeEventListener("popstate", callback);
}
export function useRoute() {
  const path = useSyncExternalStore(
    subscribe,
    () => location.pathname + location.search,
  );
  return parseRoute(path.split("?")[0]);
}
