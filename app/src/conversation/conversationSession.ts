export const CONVERSATION_ID_PREFERENCE = "javis.app.conversation_id";

export type ConversationStorage = Pick<Storage, "getItem" | "setItem">;

const SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

export function getOrCreateConversationId(
  storage: ConversationStorage,
  randomId: () => string = () => globalThis.crypto.randomUUID(),
): string {
  const stored = storage.getItem(CONVERSATION_ID_PREFERENCE)?.trim() ?? "";
  if (SESSION_ID_PATTERN.test(stored)) return stored;

  const generated = randomId().trim();
  if (!SESSION_ID_PATTERN.test(generated)) {
    throw new Error("Conversation id generator returned an invalid id");
  }
  storage.setItem(CONVERSATION_ID_PREFERENCE, generated);
  return generated;
}
