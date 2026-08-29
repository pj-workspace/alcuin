import type {
  ExecutionEvent,
  ImageAttachment,
  MessagePart,
  Run,
  RunStatus,
  Thread,
  ThreadDetail,
  ThreadMessage,
} from "@alcuin/contracts";

export type ThreadMessagePartShape = MessagePart;
export type ThreadMessageShape = ThreadMessage;
export type ThreadDetailShape = ThreadDetail;

export type ConversationAttachment = {
  id: string;
  name: string;
  mediaType: string;
  dataUrl?: string;
};

export type ConversationTurn = {
  id: string;
  runId: string | null;
  input: string;
  attachments: ConversationAttachment[];
  assistantText: string;
  events: ExecutionEvent[];
  status: RunStatus | "submitting";
  createdAt: string;
  optimistic: boolean;
};

export type ThreadSessionPhase = "idle" | "loading" | "submitting" | "streaming" | "waiting_for_approval" | "error";

export type ThreadSessionState = {
  thread: Thread | null;
  turns: ConversationTurn[];
  phase: ThreadSessionPhase;
  activeRunId: string | null;
  error: string | null;
};

export const initialThreadSessionState: ThreadSessionState = {
  thread: null,
  turns: [],
  phase: "idle",
  activeRunId: null,
  error: null,
};

export type ThreadSessionAction =
  | { type: "reset" }
  | { type: "hydrate.started"; threadId: string }
  | { type: "hydrate.completed"; detail: ThreadDetailShape; latestRunEvents?: ExecutionEvent[]; latestRunStatus?: RunStatus; latestRunId?: string }
  | { type: "hydrate.failed"; message: string }
  | { type: "turn.submitted"; turn: ConversationTurn }
  | { type: "thread.created"; thread: Thread }
  | { type: "run.created"; optimisticId: string; run: Run }
  | { type: "events.appended"; runId: string; events: ExecutionEvent[] }
  | { type: "run.settled"; runId: string; status: RunStatus }
  | { type: "run.connection_failed"; runId: string; message: string }
  | { type: "turn.failed"; optimisticId: string; message: string };

export function threadSessionReducer(
  state: ThreadSessionState,
  action: ThreadSessionAction,
): ThreadSessionState {
  switch (action.type) {
    case "reset":
      return initialThreadSessionState;
    case "hydrate.started":
      if (state.thread?.id !== action.threadId) {
        return {
          ...initialThreadSessionState,
          phase: "loading",
        };
      }
      return {
        ...state,
        phase: "loading",
        activeRunId: null,
        error: null,
      };
    case "hydrate.completed":
      return {
        thread: action.detail.thread,
        turns: turnsFromThreadDetail(action.detail, action.latestRunEvents),
        phase: action.latestRunStatus === "queued" || action.latestRunStatus === "running"
          ? "streaming"
          : action.latestRunStatus === "waiting_for_approval"
            ? "waiting_for_approval"
            : "idle",
        activeRunId: action.latestRunStatus === "queued"
          || action.latestRunStatus === "running"
          || action.latestRunStatus === "waiting_for_approval"
          ? action.latestRunId ?? null
          : null,
        error: null,
      };
    case "hydrate.failed":
      return { ...state, phase: "error", error: action.message };
    case "turn.submitted":
      return {
        ...state,
        turns: [...state.turns, action.turn],
        phase: "submitting",
        activeRunId: null,
        error: null,
      };
    case "thread.created":
      return { ...state, thread: action.thread };
    case "run.created":
      return {
        ...state,
        turns: state.turns.map((turn) => turn.id === action.optimisticId ? {
          ...turn,
          id: action.run.id,
          runId: action.run.id,
          status: action.run.status,
        } : turn),
        phase: "streaming",
        activeRunId: action.run.id,
      };
    case "events.appended":
      return {
        ...state,
        turns: state.turns.map((turn) => turn.runId === action.runId ? appendEvents(turn, action.events) : turn),
      };
    case "run.settled": {
      const isActiveRun = state.activeRunId === action.runId;
      return {
        ...state,
        turns: state.turns.map((turn) => turn.runId === action.runId ? {
          ...turn,
          status: action.status,
          optimistic: false,
        } : turn),
        phase: isActiveRun
          ? action.status === "failed"
            ? "error"
            : action.status === "waiting_for_approval"
              ? "waiting_for_approval"
              : action.status === "queued" || action.status === "running"
                ? "streaming"
                : "idle"
          : state.phase,
        activeRunId: isActiveRun
          ? action.status === "queued"
            || action.status === "running"
            || action.status === "waiting_for_approval"
            ? action.runId
            : null
          : state.activeRunId,
        error: isActiveRun && action.status !== "failed" ? null : state.error,
      };
    }
    case "run.connection_failed":
      if (state.activeRunId !== action.runId) return state;
      return {
        ...state,
        phase: "streaming",
        error: action.message,
      };
    case "turn.failed":
      return {
        ...state,
        turns: state.turns.filter((turn) => turn.id !== action.optimisticId),
        phase: "error",
        activeRunId: null,
        error: action.message,
      };
  }
}

export function optimisticTurn(
  id: string,
  input: string,
  attachments: ImageAttachment[],
): ConversationTurn {
  return {
    id,
    runId: null,
    input,
    attachments: attachments.map((attachment, index) => ({
      id: `${id}-attachment-${index}`,
      name: attachment.name,
      mediaType: attachment.media_type,
      dataUrl: attachment.data_url,
    })),
    assistantText: "",
    events: [],
    status: "submitting",
    createdAt: new Date().toISOString(),
    optimistic: true,
  };
}

export function turnsFromThreadDetail(
  detail: ThreadDetailShape,
  latestRunEvents: ExecutionEvent[] = [],
): ConversationTurn[] {
  const messages = [...detail.messages].sort((left, right) => left.sequence - right.sequence);
  const messageById = new Map(messages.map((message) => [message.id, message]));
  const messagesByRun = new Map<string, ThreadMessageShape[]>();
  for (const message of messages) {
    if (!message.run_id) continue;
    const current = messagesByRun.get(message.run_id) ?? [];
    current.push(message);
    messagesByRun.set(message.run_id, current);
  }

  const orderedRuns = [...detail.runs].sort((left, right) =>
    Date.parse(left.created_at) - Date.parse(right.created_at));
  const latestRunId = orderedRuns.at(-1)?.id;
  const turns = orderedRuns.map((run) => {
    const runMessages = messagesByRun.get(run.id) ?? [];
    const inputMessageId = (run as Run & { input_message_id?: string | null }).input_message_id;
    const outputMessageId = (run as Run & { output_message_id?: string | null }).output_message_id;
    const userMessage = (inputMessageId ? messageById.get(inputMessageId) : undefined)
      ?? runMessages.find((message) => message.role === "user");
    const assistantMessage = (outputMessageId ? messageById.get(outputMessageId) : undefined)
      ?? [...runMessages].reverse().find((message) => message.role === "assistant");
    return turnFromMessages(
      run,
      userMessage,
      assistantMessage,
      run.id === latestRunId ? latestRunEvents : [],
    );
  });

  const knownRunIds = new Set(orderedRuns.map((run) => run.id));
  for (const [runId, runMessages] of messagesByRun) {
    if (knownRunIds.has(runId)) continue;
    const userMessage = runMessages.find((message) => message.role === "user");
    const assistantMessage = [...runMessages].reverse().find((message) => message.role === "assistant");
    turns.push({
      id: runId,
      runId,
      input: textFromMessage(userMessage),
      attachments: attachmentsFromMessage(userMessage),
      assistantText: textFromMessage(assistantMessage),
      events: [],
      status: assistantMessage?.status === "failed" ? "failed" : "completed",
      createdAt: userMessage?.created_at ?? assistantMessage?.created_at ?? detail.thread.created_at,
      optimistic: false,
    });
  }
  return turns.sort((left, right) => Date.parse(left.createdAt) - Date.parse(right.createdAt));
}

function turnFromMessages(
  run: Run,
  userMessage: ThreadMessageShape | undefined,
  assistantMessage: ThreadMessageShape | undefined,
  events: ExecutionEvent[],
): ConversationTurn {
  return {
    id: run.id,
    runId: run.id,
    input: textFromMessage(userMessage) || run.input,
    attachments: attachmentsFromMessage(userMessage),
    assistantText: assistantTextFromEvents(events) || textFromMessage(assistantMessage),
    events,
    status: run.status,
    createdAt: userMessage?.created_at ?? run.created_at,
    optimistic: false,
  };
}

function textFromMessage(message: ThreadMessageShape | undefined): string {
  if (!message) return "";
  return message.parts
    .filter((part): part is Extract<ThreadMessagePartShape, { type: "text" }> => part.type === "text")
    .map((part) => part.text)
    .join("");
}

function attachmentsFromMessage(message: ThreadMessageShape | undefined): ConversationAttachment[] {
  if (!message) return [];
  return message.parts
    .filter((part): part is Extract<ThreadMessagePartShape, { type: "attachment" }> => part.type === "attachment")
    .map((part) => ({
      id: part.attachment_id,
      name: part.name,
      mediaType: part.media_type,
    }));
}

function appendEvents(turn: ConversationTurn, nextEvents: ExecutionEvent[]): ConversationTurn {
  if (nextEvents.length === 0) return turn;
  const bySequence = new Map(turn.events.map((event) => [event.sequence, event]));
  for (const event of nextEvents) bySequence.set(event.sequence, event);
  const events = [...bySequence.values()].sort((left, right) => left.sequence - right.sequence);
  return {
    ...turn,
    events,
    assistantText: assistantTextFromEvents(events) || turn.assistantText,
  };
}

function assistantTextFromEvents(events: ExecutionEvent[]): string {
  return events
    .filter((event) => event.type === "message.delta")
    .map((event) => String(event.payload.delta ?? ""))
    .join("");
}
