import {
  COMPLETABLE_TASK_STEP_STATUSES,
  TASK_EVENT_TYPES,
  TASK_STATUSES,
  TASK_STATUS_TRANSITIONS,
  TASK_STEP_STATUSES,
  TERMINAL_TASK_STATUSES,
  canCompleteTask,
  canTransitionTask,
  type Task,
  type TaskAttempt,
  type TaskAttemptStatus,
  type TaskCheckpoint,
  type TaskCommand,
  type TaskCommandName,
  type TaskCreate,
  type TaskEvent,
  type TaskEventType,
  type TaskEvidence,
  type TaskEvidenceKind,
  type TaskIntervention,
  type TaskInterventionKind,
  type TaskInterventionStatus,
  type TaskPlan,
  type TaskPlanUpdate,
  type TaskStatus,
  type TaskStep,
  type TaskStepDraft,
  type TaskStepStatus,
} from "./index";

const create = {
  goal: "Investigate the checkout incident",
  steps: [{ title: "Collect evidence", description: "Read the current logs." }],
} satisfies TaskCreate;

const planUpdate = {
  expected_revision: 2,
  steps: [{ title: "Summarize evidence" }],
} satisfies TaskPlanUpdate;

const steer = {
  command: "steer",
  idempotency_key: "steer:tsk_1:1",
  expected_revision: 2,
  expected_status: "running",
  message: "Check the new incident window before continuing.",
} satisfies TaskCommand;

const task = {
  id: "tsk_1",
  workspace_id: "ws_1",
  thread_id: "thr_1",
  goal: create.goal,
  status: "running",
  plan: {
    id: "plan_1",
    task_id: "tsk_1",
    steps: [
      {
        id: "stp_1",
        task_id: "tsk_1",
        ordinal: 0,
        title: "Collect evidence",
        description: "Read the current logs.",
        status: "running",
        attempts: [],
        evidence: [],
      },
    ],
    updated_at: "2026-08-30T00:00:00Z",
  },
  current_step_id: "stp_1",
  result: null,
  latest_checkpoint: null,
  pending_intervention: null,
  revision: 2,
  created_at: "2026-08-30T00:00:00Z",
  updated_at: "2026-08-30T00:00:00Z",
} satisfies Task;

const event = {
  id: "tevt_1",
  workspace_id: "ws_1",
  task_id: "tsk_1",
  sequence: 1,
  type: "task.step.started",
  timestamp: "2026-08-30T00:00:00Z",
  step_id: "stp_1",
  payload: { title: "Collect evidence" },
} satisfies TaskEvent;

const explicitControlEvents = [
  "task.step.retry_requested",
  "task.intervention.steered",
  "task.intervention.interrupt_requested",
] satisfies TaskEventType[];

type ForbiddenReasoningKey = Extract<
  keyof Task | keyof Task["plan"]["steps"][number] | keyof TaskEvent,
  "reasoning" | "raw_reasoning" | "chain_of_thought" | "thought"
>;
const publicContractsHaveNoPrivateReasoning: ForbiddenReasoningKey extends never ? true : false = true;

const completeStatuses = ["completed", "skipped"] satisfies TaskStepStatus[];
const result = { summary: "Incident contained" };
void canCompleteTask(completeStatuses, result);
void canTransitionTask("running", "completed", completeStatuses, result);
void planUpdate;
void steer;
void task;
void event;
void explicitControlEvents;
void publicContractsHaveNoPrivateReasoning;

type ExportedTaskContracts = [
  Task,
  TaskAttempt,
  TaskAttemptStatus,
  TaskCheckpoint,
  TaskCommand,
  TaskCommandName,
  TaskCreate,
  TaskEvent,
  TaskEventType,
  TaskEvidence,
  TaskEvidenceKind,
  TaskIntervention,
  TaskInterventionKind,
  TaskInterventionStatus,
  TaskPlan,
  TaskPlanUpdate,
  TaskStatus,
  TaskStep,
  TaskStepDraft,
  TaskStepStatus,
];

declare const exportedTaskContracts: ExportedTaskContracts;
void exportedTaskContracts;
void COMPLETABLE_TASK_STEP_STATUSES;
void TASK_EVENT_TYPES;
void TASK_STATUSES;
void TASK_STATUS_TRANSITIONS;
void TASK_STEP_STATUSES;
void TERMINAL_TASK_STATUSES;
