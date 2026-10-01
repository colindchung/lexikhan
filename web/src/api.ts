export interface Card {
  cardId: string;
  prompt: string;
  state: string;
  version: number;
  dueAt: string;
}
export interface Answer {
  cardId: string;
  answer: string;
  explanation?: string;
  examples: string[];
  audioUrl?: string;
}
export type Rating = "AGAIN" | "HARD" | "GOOD" | "EASY";
export interface Review {
  reviewId: string;
  cardId: string;
  version: number;
  rating: Rating;
  typedAnswer?: string;
}
export interface SessionResponse {
  cards: Card[];
  nextReviewAt?: string | null;
}
export interface ReviewResult extends Card {
  nextDueAt: string;
}
export interface ProfileSettings {
  deckId: string;
  learningLanguage: string;
  baseLanguage: string;
  timezone: string;
  dailyGoal: number;
}
export interface Profile extends ProfileSettings {
  createdAt: string;
}
export interface Deck {
  id: string;
  name: string;
  learningLanguage: string;
  baseLanguage: string;
  cardCount: number;
}
export interface ProfileResponse {
  profile: Profile | null;
  decks: Deck[];
}
export interface OnboardingApi {
  profile(): Promise<ProfileResponse>;
  enroll(settings: ProfileSettings): Promise<{ profile: Profile }>;
}
export interface ReminderInput {
  enabled: boolean;
  time: string;
  timezone: string;
  version: number;
}
export interface ReminderSettings extends ReminderInput {
  phone: string | null;
  available: boolean;
  nextReminderAt: string | null;
}
export interface ReminderApi {
  reminders(): Promise<ReminderSettings>;
  saveReminders(settings: ReminderInput): Promise<ReminderSettings>;
}
export interface LearningApi {
  session(): Promise<SessionResponse>;
  answer(cardId: string): Promise<Answer>;
  review(request: Review): Promise<ReviewResult>;
}
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export function createApi(
  base: string,
  token: () => Promise<string>,
): LearningApi & OnboardingApi & ReminderApi {
  async function request<T>(
    path: string,
    body?: Review | ProfileSettings | ReminderInput,
  ): Promise<T> {
    let response: Response;
    try {
      const accessToken = await token();
      response = await fetch(`${base}${path}`, {
        method: body ? "POST" : "GET",
        headers: {
          Authorization: `Bearer ${accessToken}`,
          ...(body ? { "Content-Type": "application/json" } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
        cache: "no-store",
        signal: AbortSignal.timeout(20000),
      });
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError(
        0,
        "We couldn’t connect. Your answer is saved. Check your connection and try again.",
      );
    }
    if (!response.ok) {
      throw new ApiError(
        response.status,
        response.status === 401
          ? "Your sign-in has expired. Sign in again to continue where you left off."
          : response.status === 409
            ? "This card was updated in another session. Refresh your cards to continue. Your answer is saved."
            : "We couldn’t finish that request. Please try again.",
      );
    }
    return response.json() as Promise<T>;
  }
  return {
    reminders: () => request("/reminders"),
    saveReminders: (body) => request("/reminders", body),
    profile: () => request("/profile"),
    enroll: (body) => request("/onboarding", body),
    session: () => request("/session"),
    answer: (id) => request(`/cards/${encodeURIComponent(id)}/answer`),
    review: (body) => request("/reviews", body),
  };
}
