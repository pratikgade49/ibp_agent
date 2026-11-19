/** Record browser-rendered exchanges for agents whose UI constructs their replies. */
export async function saveConversationTurn(agent: 'ewm' | 'migration', conversationId: string, message: string, reply: string): Promise<boolean> {
  try {
    const response = await fetch('/memory/turn', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent, conversation_id: conversationId, message, reply }),
    });
    if (!response.ok) return false;
    return (await response.json()).saved === true;
  } catch { return false; }
}
