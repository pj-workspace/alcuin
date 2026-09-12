"use client";

import type { Agent, ContextAssembly, ExecutionEvent, Extension, Rule, Skill, Thread, ThreadConfiguration } from "@alcuin/contracts";
import type { ProviderModelProfile, ProviderStatus, ReasoningEffort } from "@alcuin/sdk";
import {
  ArrowUp,
  Brain,
  ChevronDown,
  Check,
  Clock3,
  Cpu,
  PanelRightClose,
  PanelRightOpen,
  Paperclip,
  ListChecks,
  MessageSquare,
  Save,
  Sparkles,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { clsx } from "clsx";

import { alcuinApi } from "@/shared/lib/api";
import { ConversationTimeline } from "@/features/studio/conversation-timeline";
import { StudioWelcome } from "@/features/studio/studio-welcome";
import { useTaskProposal } from "@/features/studio/tasks/use-task-proposal";
import { TaskProposalPanel } from "@/features/studio/tasks/task-proposal-panel";
import { ATTACHMENT_ACCEPT, ComposerAttachmentTray, useComposerAttachments, type AttachmentValidationIssue } from "@/features/studio/attachments";
import { ArtifactCanvas, isArtifactResource } from "@/features/studio/artifacts";
import { taskArtifactRefreshKey } from "@/features/studio/artifacts/artifact-workspace-model";
import { ExtensionUIBlocks } from "@/features/studio/extension-ui-blocks";
import { Toast } from "@/shared/components/ui";
import { skillDisplayName } from "@/shared/lib/customization";
import { isManualRuleAvailable, isSkillManuallySelectable } from "@/features/studio/context-ledger-model";
import { modelsForAgent, runProfileFromEvents, tierLabelKey } from "@/features/studio/run-profile-model";
import { usePinnedTurnScroll } from "@/features/studio/use-pinned-turn-scroll";
import { useThreadSession } from "@/features/studio/use-thread-session";
import { TaskCanvas, TaskInlineStatus, interventionCommandForStatus, useTaskSession } from "@/features/studio/tasks";
import { resolveExtensionUIBlocks, type ResolvedExtensionUIBlock } from "@/features/extensions";
import { useI18n } from "@/shared/lib/i18n";

type CanvasTab = "artifact" | "task" | "trace" | "extensions" | "context";
type MobileSurface = "conversation" | "canvas";

export function StudioView({
  workspace,
  agent,
  extensions,
  skills,
  rules,
  providers = [],
  providerCatalogError = null,
  customizationError,
  activeThreadId,
  activeThreadTitle,
  onThreadActivity,
  onThreadCreated,
  onRunCreated,
}: {
  workspace: { id: string; name: string };
  agent?: Agent;
  extensions: Extension[];
  skills: Skill[];
  rules: Rule[];
  providers?: ProviderStatus[];
  providerCatalogError?: string | null;
  customizationError: string | null;
  activeThreadId: string | null;
  activeThreadTitle?: string;
  onThreadActivity?: (threadId: string) => void;
  onThreadCreated: (thread: Thread) => void;
  onRunCreated: (runId: string) => Promise<void>;
}) {
  const { t, locale } = useI18n();
  const [canvasTab, setCanvasTab] = useState<CanvasTab>("artifact");
  const [canvasOpen, setCanvasOpen] = useState(false);
  const [mobileSurface, setMobileSurface] = useState<MobileSurface>("conversation");
  const [prompt, setPrompt] = useState("");
  const [composerMode, setComposerMode] = useState<"chat" | "plan">("chat");
  const [confirmingPlan, setConfirmingPlan] = useState(false);
  const confirmationRef = useRef(false);
  const viewScopeRef = useRef({ agentId: agent?.id, threadId: activeThreadId });
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [composerFocused, setComposerFocused] = useState(false);
  const [modelOverride, setModelOverride] = useState<string | null>(null);
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [approvalBusy, setApprovalBusy] = useState(false);
  const userSelectedCanvasRef = useRef(false);
  const attachmentInputRef = useRef<HTMLInputElement>(null);
  const dragDepthRef = useRef(0);
  const attachmentController = useComposerAttachments();
  const hostContext = useMemo(() => ({}), []);
  const session = useThreadSession({
    agent,
    threadId: activeThreadId,
    hostContext,
    onThreadCreated,
    onRunCreated,
  });
  const { state } = session;
  const refreshThread = session.refresh;
  const proposal = useTaskProposal({ agentId: agent?.id, threadId: state.thread?.id ?? activeThreadId, ensureThread: session.ensureThread });
  const hasProposal = proposal.phase !== "idle"
    && proposal.scope?.agentId === agent?.id
    && proposal.scope?.threadId === activeThreadId;
  const planning = proposal.phase === "generating";
  const taskSession = useTaskSession({
    threadId: state.thread?.id ?? activeThreadId,
    ensureThread: session.ensureThread,
  });
  const activeTask = taskSession.activeTask;
  const threadWithInput = state.turns.length > 0 ? state.thread?.id : undefined;
  const taskThreadId = taskSession.task?.thread_id;
  const taskId = taskSession.task?.id;
  useEffect(() => {
    const changedThreadId = taskThreadId ?? threadWithInput;
    if (changedThreadId) onThreadActivity?.(changedThreadId);
  }, [threadWithInput, state.activeRunId, taskId, taskThreadId, onThreadActivity]);
  const taskCanAcceptGuidance = Boolean(activeTask && interventionCommandForStatus(activeTask.status));
  const running = session.running;
  const blocked = session.blocked;
  const taskHydrating = taskSession.state.phase === "loading";
  const composerBlocked = (activeTask ? taskSession.busy : blocked || taskHydrating) || planning || confirmingPlan;
  const taskAttachmentConflict = Boolean(activeTask && attachmentController.items.length > 0);
  const taskAttachmentMessage = locale === "zh"
    ? "任务引导暂不支持附件；附件仍保留在输入框，请移除后发送引导，或先停止任务再作为普通消息发送。"
    : "Task guidance cannot include attachments yet. They remain in the composer; remove them to guide the Task, or stop the Task and send a normal message.";
  const latestTurn = state.turns.at(-1);
  const sendButtonLabel = activeTask
    ? (locale === "zh" ? "引导当前任务" : "Guide current Task")
    : state.phase === "waiting_for_approval"
    ? t("Resolve approval before sending another message")
    : composerMode === "plan"
    ? (locale === "zh" ? "生成任务计划" : "Generate task plan")
    : t(blocked ? "Agent is running" : "Send message");
  const activeEvents = latestTurn?.events ?? [];
  const effectiveRunProfile = runProfileFromEvents(activeEvents);
  const modelProfiles = useMemo(
    () => agent ? modelsForAgent(providers, agent.definition.model.provider) : [],
    [agent, providers],
  );
  const displayedModelValue = activeTask
    ? activeTask.model_override ?? ""
    : running
    ? effectiveRunProfile.model ?? modelOverride ?? agent?.definition.model.model ?? ""
    : modelOverride ?? "";
  const displayedReasoningValue = activeTask
    ? activeTask.reasoning_effort ?? ""
    : running
    ? effectiveRunProfile.reasoningEffort ?? reasoningEffort ?? ""
    : reasoningEffort ?? "";
  const selectedModel = modelProfiles.find((profile) => profile.id === ((activeTask ? activeTask.model_override : modelOverride) ?? agent?.definition.model.model));
  const supportedReasoningEfforts = selectedModel?.reasoning_efforts;
  const hasImageAttachment = attachmentController.items.some((item) => item.kind === "image");
  const imageModelMismatch = hasImageAttachment && Boolean(selectedModel && !selectedModel.input_modalities.includes("image"));
  const attachmentBlockReason = taskAttachmentConflict
    ? taskAttachmentMessage
    : attachmentController.busy
    ? t("Wait for attachments to finish")
    : attachmentController.hasErrors
      ? t("Retry or remove failed attachments")
      : imageModelMismatch
        ? t("The selected model does not support image input")
        : null;
  const modelControlOptions = [
    { value: "", label: t("Agent default"), shortLabel: t("Default") },
    ...modelProfiles.map((profile) => ({ value: profile.id, label: modelProfileLabel(profile, t) })),
  ];
  if ((running || activeTask) && displayedModelValue && !modelControlOptions.some((option) => option.value === displayedModelValue)) {
    modelControlOptions.push({ value: displayedModelValue, label: displayedModelValue });
  }
  const reasoningControlOptions = [
    { value: "", label: t("Agent default"), shortLabel: t("Default") },
    ...(["none", "low", "medium", "high"] as const).map((effort) => ({
      value: effort,
      label: t(reasoningLabelKey(effort)),
      disabled: !running && Boolean(supportedReasoningEfforts && !supportedReasoningEfforts.includes(effort)),
    })),
  ];
  const turnKey = `${state.thread?.id ?? "new"}:${latestTurn?.id ?? "empty"}`;
  const { anchorRef, endRef, scrollRef: timelineRef, spacerPx } = usePinnedTurnScroll(
    turnKey,
    activeEvents.length + (running ? 1 : 0),
  );

  const artifactEvent = useMemo(() => {
    for (const turn of [...state.turns].reverse()) {
      const event = [...turn.events].reverse().find((item) => item.type === "artifact.updated");
      if (event) return event;
    }
    return undefined;
  }, [state.turns]);
  const eventArtifacts = useMemo(() => {
    const resources = new Map<string, unknown>();
    for (const turn of state.turns) for (const event of turn.events) {
      const artifact = event.type === "artifact.updated" ? event.payload.artifact : undefined;
      if (isArtifactResource(artifact)) { resources.delete(artifact.id); resources.set(artifact.id, artifact); }
    }
    return [...resources.values()];
  }, [state.turns]);
  const artifactBelongsToLatestTurn = Boolean(latestTurn?.runId && artifactEvent?.run_id === latestTurn.runId);
  const artifactCitationEvents = useMemo(() => state.turns.flatMap((turn) => turn.events.filter((event) => event.type === "citation.created")), [state.turns]);
  const artifactEnabled = String(agent?.definition.output_schema.type ?? "artifact") === "artifact";

  useEffect(() => {
    viewScopeRef.current = { agentId: agent?.id, threadId: activeThreadId };
    confirmationRef.current = false;
    setConfirmingPlan(false);
  }, [agent?.id, activeThreadId]);

  useEffect(() => {
    userSelectedCanvasRef.current = false;
    setCanvasOpen(false);
    setCanvasTab("artifact");
    setMobileSurface("conversation");
  // Reset when navigation changes, not when the requested thread finishes hydrating.
  // A late response must not undo the user's panel selection made while loading.
  }, [activeThreadId]);

  useEffect(() => {
    setModelOverride(null);
    setReasoningEffort(null);
  }, [agent?.id]);

  useEffect(() => {
    if (!artifactEnabled || !artifactBelongsToLatestTurn || userSelectedCanvasRef.current) return;
    setCanvasTab("artifact");
    setCanvasOpen(true);
  }, [artifactEnabled, artifactBelongsToLatestTurn]);

  useEffect(() => {
    if (taskSession.task || hasProposal || canvasTab !== "task") return;
    setCanvasTab("artifact");
  }, [canvasTab, taskSession.task, hasProposal]);

  useEffect(() => {
    if (!hasProposal) return;
    setCanvasTab("task");
    setCanvasOpen(true);
    setMobileSurface("canvas");
  }, [hasProposal, activeThreadId]);

  useEffect(() => {
    const input = promptRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
  }, [prompt]);
  const extensionBlocks = useMemo(
    () => agent ? resolveExtensionUIBlocks(agent, extensions) : [],
    [agent, extensions],
  );
  const contextSkillCount = useMemo(() => {
    const enabledVersions = new Set(skills.filter((skill) => skill.enabled).map((skill) => skill.current_version_id));
    return (agent?.definition.skills ?? []).filter((binding) => enabledVersions.has(binding.skill_version_id)).length;
  }, [agent, skills]);
  const contextRuleCount = useMemo(() => {
    const boundVersions = new Set((agent?.definition.rules ?? []).map((binding) => binding.rule_version_id));
    return new Set(rules.filter((rule) => rule.enabled && (rule.scope === "workspace" || boundVersions.has(rule.current_version_id))).map((rule) => rule.current_version_id)).size;
  }, [agent, rules]);
  const openContextLedger = () => {
    userSelectedCanvasRef.current = true;
    setCanvasTab("context");
    setCanvasOpen(true);
    setMobileSurface("canvas");
  };

  const selectCanvasTab = (tab: CanvasTab) => {
    userSelectedCanvasRef.current = true;
    setCanvasTab(tab);
  };

  const openTaskCanvas = () => {
    selectCanvasTab("task");
    setCanvasOpen(true);
    setMobileSurface("canvas");
  };

  const openCanvas = () => {
    setCanvasOpen(true);
    setMobileSurface("canvas");
  };

  const closeCanvas = () => {
    setCanvasOpen(false);
    setMobileSurface("conversation");
  };

  const showToast = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(null), 2200);
  }, []);
  const copyResponse = useCallback(async (text: string) => { await navigator.clipboard.writeText(text); showToast(t("Response copied")); }, [showToast, t]);

  async function submit(input = prompt) {
    const value = input.trim();
    if ((!value && attachmentController.items.length === 0) || !agent) return;
    if (planning || confirmingPlan) return;
    if (!activeTask && composerMode === "plan") { await createTaskFromPrompt(); return; }
    if (activeTask) {
      if (!value || taskSession.busy) return;
      if (attachmentController.items.length > 0) {
        showToast(taskAttachmentMessage);
        return;
      }
      if (!taskCanAcceptGuidance) {
        showToast(locale === "zh" ? "任务正在切换执行边界，请从任务控制条开始或继续后再发送引导。" : "The Task is changing execution boundaries. Start or resume it before sending guidance.");
        return;
      }
      try {
        await taskSession.intervene(value);
        setPrompt("");
        openTaskCanvas();
      } catch (error) {
        showToast(error instanceof Error ? error.message : (locale === "zh" ? "任务引导失败" : "Unable to guide Task"));
      }
      return;
    }
    if (blocked || taskHydrating) return;
    if (attachmentBlockReason) {
      showToast(attachmentBlockReason);
      return;
    }
    try {
      await session.send(value, attachmentController.readyResources, {
        runOptions: { modelOverride, reasoningEffort },
        onAccepted: () => {
          setPrompt("");
          attachmentController.clearAccepted();
        },
      });
    } catch (error) {
      showToast(error instanceof Error ? error.message : t("Run failed"));
    }
  }

  async function createTaskFromPrompt() {
    const value = prompt.trim();
    if (!value || activeTask || taskSession.busy || blocked || planning || confirmingPlan) return;
    if (attachmentController.items.length > 0) {
      showToast(locale === "zh" ? "创建任务前请先移除附件；附件仍保留在输入框中。" : "Remove attachments before creating a Task. They remain in the composer.");
      return;
    }
    try {
      openTaskCanvas();
      await proposal.generate(value, {
        model_override: modelOverride,
        reasoning_effort: reasoningEffort,
      });
    } catch (error) {
      showToast(error instanceof Error ? error.message : (locale === "zh" ? "创建任务失败" : "Unable to create Task"));
    }
  }

  async function confirmPlan() {
    if (!proposal.draft || confirmationRef.current || activeTask || blocked || taskSession.busy) return;
    const viewScope = viewScopeRef.current;
    confirmationRef.current = true;
    setConfirmingPlan(true);
    try {
      const created = await taskSession.createTask(proposal.draft.goal, false, proposal.profile, proposal.draft.steps.map(({ title, description }) => ({ title, description })));
      if (viewScopeRef.current !== viewScope) return;
      proposal.cancel();
      setPrompt("");
      setComposerMode("chat");
      openTaskCanvas();
      await taskSession.sendCommand("start", undefined, undefined, created.id);
    } catch (error) {
      if (viewScopeRef.current !== viewScope || (error instanceof DOMException && error.name === "AbortError")) return;
      showToast(error instanceof Error ? error.message : (locale === "zh" ? "启动任务失败，计划已保留" : "Couldn’t start the Task. Your plan is preserved."));
    } finally { if (viewScopeRef.current === viewScope) { confirmationRef.current = false; setConfirmingPlan(false); } }
  }

  function controlTask(command: "start" | "pause" | "resume" | "cancel" | "retry", stepId?: string) {
    void taskSession.sendCommand(command, stepId).catch((error) => {
      showToast(error instanceof Error ? error.message : (locale === "zh" ? "任务操作失败" : "Task action failed"));
    });
  }

  async function submitExtensionAction(
    resolved: ResolvedExtensionUIBlock,
    tool: string,
    arguments_: Record<string, unknown>,
  ) {
    if (!agent || blocked) return;
    setMobileSurface("conversation");
    try {
      await session.runTool({
        label: `${resolved.block.title} · declarative extension action`,
        tool,
        arguments: arguments_,
        extensionManifestId: resolved.manifestId,
        uiBlockId: resolved.block.id,
      });
    } catch (error) {
      showToast(error instanceof Error ? error.message : t("Extension action failed"));
    }
  }

  function addFiles(files: FileList | File[] | null) {
    if (!files) return;
    if (activeTask) {
      showToast(taskAttachmentMessage);
      return;
    }
    if (composerBlocked) return;
    const issues = attachmentController.addFiles(files);
    if (issues.length > 0) showToast(attachmentIssueMessage(issues[0]!, t));
    if (attachmentInputRef.current) attachmentInputRef.current.value = "";
  }

  const handleDragEnter = (event: React.DragEvent<HTMLDivElement>) => {
    if (composerBlocked || activeTask || !event.dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    dragDepthRef.current += 1;
    setDragActive(true);
  };
  const handleDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
    if (!dragActive) return;
    event.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setDragActive(false);
  };
  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    if (composerBlocked || activeTask) return;
    event.preventDefault();
    dragDepthRef.current = 0;
    setDragActive(false);
    addFiles(event.dataTransfer.files);
  };

  const decide = useCallback(async (runId: string, approvalId: string, decision: "approved" | "denied") => {
    setApprovalBusy(true);
    try {
      await alcuinApi.decideApproval(runId, approvalId, decision);
      await onRunCreated(runId);
      await refreshThread();
      showToast(t(decision === "approved" ? "Operation approved and completed" : "Operation denied — no changes made"));
    } catch (error) {
      showToast(error instanceof Error ? error.message : t("Approval failed"));
    } finally {
      setApprovalBusy(false);
    }
  }, [onRunCreated, refreshThread, showToast, t]);
  const onDecision = useCallback((runId: string, approvalId: string, decision: "approved" | "denied") => { void decide(runId, approvalId, decision); }, [decide]);

  if (!agent) return null;

  return (
    <div className={clsx("studio-layout", !canvasOpen && "canvas-closed")}>
      <div className="mobile-studio-switch" role="tablist" aria-label={t("Studio panel")}>
        <button id="mobile-conversation-tab" role="tab" aria-controls="studio-conversation-panel" aria-selected={mobileSurface === "conversation"} className={clsx(mobileSurface === "conversation" && "active")} onClick={() => setMobileSurface("conversation")}>{t("Chat")}</button>
        <button id="mobile-canvas-tab" role="tab" aria-controls="studio-canvas-panel" aria-selected={mobileSurface === "canvas"} className={clsx(mobileSurface === "canvas" && "active")} onClick={() => { setCanvasOpen(true); setMobileSurface("canvas"); }}>{t("Canvas")}<span>{activeEvents.length + extensionBlocks.length + (taskSession.task ? 1 : 0)}</span></button>
      </div>
      <section id="studio-conversation-panel" role="tabpanel" aria-labelledby="mobile-conversation-tab" className={clsx("conversation-pane", mobileSurface !== "conversation" && "mobile-surface-hidden")}>
        <header className="surface-header conversation-header">
          <div><div className="eyebrow"><span className="live-dot" />{t(agent.status === "published" ? "Published agent" : "Draft agent")}</div><h1>{agent.name}</h1></div>
          <div className="header-actions">
            <button className="button secondary canvas-toggle" onClick={() => canvasOpen ? closeCanvas() : openCanvas()}>
              {canvasOpen ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
              {t(canvasOpen ? "Close canvas" : "Open canvas")}
            </button>
            <button className="button secondary" onClick={openContextLedger}>{t("Context ledger")}<ChevronDown size={13} /></button>
          </div>
        </header>

        <div className="conversation-scroll" ref={timelineRef}>
          <ConversationTimeline
            turns={state.turns}
            workspaceName={workspace.name}
            threadTitle={activeThreadTitle ?? state.thread?.title ?? t("New thread")}
            activeRunId={state.activeRunId}
            running={running}
            busy={approvalBusy}
            anchorRef={anchorRef}
            endRef={endRef}
            spacerPx={spacerPx}
            onCopy={copyResponse}
            onDecision={onDecision}
            emptyState={<StudioWelcome starterPrompts={agent.definition.starter_prompts} disabled={composerBlocked} onSelectPrompt={(value) => { setPrompt(value); promptRef.current?.focus(); }} />}
          />
        </div>

        <div className="composer-wrap">
          <TaskInlineStatus
            projection={taskSession.state.projection}
            phase={taskSession.state.phase}
            pendingCommands={taskSession.state.pendingCommands}
            onOpen={openTaskCanvas}
            onCommand={controlTask}
          />
          <div className={clsx("composer", composerFocused && "focused", running && "running", activeTask && "task-guidance", dragActive && "drag-active")} onDragEnter={handleDragEnter} onDragOver={(event) => { if (!composerBlocked && event.dataTransfer.types.includes("Files")) event.preventDefault(); }} onDragLeave={handleDragLeave} onDrop={handleDrop}>
            <ComposerAttachmentTray items={attachmentController.items} onRetry={attachmentController.retry} onRemove={attachmentController.remove} />
            <textarea ref={promptRef} rows={2} value={prompt} aria-label={locale === "zh" ? "消息输入" : "Message input"} onChange={(event) => setPrompt(event.target.value)} onFocus={() => setComposerFocused(true)} onBlur={() => setComposerFocused(false)} onPaste={(event) => { const files = Array.from(event.clipboardData.files); if (files.length > 0) { event.preventDefault(); addFiles(files); } }} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); void submit(); } }} placeholder={activeTask ? (locale === "zh" ? "引导当前任务…" : "Guide the current Task…") : composerMode === "plan" ? (locale === "zh" ? "描述目标，先生成计划再执行…" : "Describe a goal. Review a plan before it runs…") : t("Message {name}…", { name: agent.name })} />
            {attachmentBlockReason && <div className="composer-inline-notice" role="status">{attachmentBlockReason}</div>}
            <div className="composer-footer">
              <div className="composer-tools">
                <button className="composer-tool" aria-label={activeTask ? taskAttachmentMessage : t("Attach files")} disabled={composerBlocked || Boolean(activeTask)} onClick={() => attachmentInputRef.current?.click()}><Paperclip size={15} /></button>
                <input ref={attachmentInputRef} className="visually-hidden" type="file" accept={ATTACHMENT_ACCEPT} multiple onChange={(event) => addFiles(event.target.files)} />
                {!activeTask && <div className="composer-mode-switch" role="group" aria-label={locale === "zh" ? "工作模式" : "Work mode"}><button type="button" className={clsx(composerMode === "chat" && "active")} aria-pressed={composerMode === "chat"} disabled={composerBlocked} onClick={() => setComposerMode("chat")}><MessageSquare size={12} />{locale === "zh" ? "对话" : "Chat"}</button><button type="button" className={clsx(composerMode === "plan" && "active")} aria-pressed={composerMode === "plan"} disabled={composerBlocked || attachmentController.items.length > 0} onClick={() => setComposerMode("plan")}><ListChecks size={12} />{locale === "zh" ? "计划" : "Plan"}</button></div>}
                <button className={clsx("context-chip", running && "live")} aria-label={running ? t("Agent is working") : t("Context · {skills} skills · {rules} rules", { skills: contextSkillCount, rules: contextRuleCount })} onClick={openContextLedger}><span className="context-dot" /><span className="context-chip-label">{running ? t("Agent is working") : t("Context · {skills} skills · {rules} rules", { skills: contextSkillCount, rules: contextRuleCount })}</span><ChevronDown size={11} /></button>
                <span className="composer-control-divider" aria-hidden="true" />
                <div className={clsx("run-profile-controls", running && "locked")} aria-label={t("Run profile")} title={providerCatalogError ?? undefined}>
                  <span className="run-profile-indicator" aria-hidden="true" />
                  <RunProfileMenu ariaLabel={t("Model profile")} icon={<Cpu size={12} />} value={displayedModelValue} options={modelControlOptions} disabled={composerBlocked || Boolean(activeTask)} onChange={(value) => {
                    const next = value || null;
                    setModelOverride(next);
                    const profile = modelProfiles.find((candidate) => candidate.id === (next ?? agent.definition.model.model));
                    if (reasoningEffort && profile && !profile.reasoning_efforts.includes(reasoningEffort)) setReasoningEffort(null);
                  }} />
                  <RunProfileMenu ariaLabel={t("Thinking effort")} icon={<Brain size={12} />} value={displayedReasoningValue} options={reasoningControlOptions} disabled={composerBlocked || Boolean(activeTask)} onChange={(value) => setReasoningEffort((value || null) as ReasoningEffort | null)} align="right" />
                </div>
              </div>
              <div className="composer-send-group"><span>↵</span><button className="send-button" aria-label={attachmentBlockReason ?? sendButtonLabel} disabled={activeTask ? !prompt.trim() || composerBlocked || !taskCanAcceptGuidance || Boolean(attachmentBlockReason) : ((!prompt.trim() && attachmentController.items.length === 0) || composerBlocked || Boolean(attachmentBlockReason))} onClick={() => void submit()}>{planning || confirmingPlan || (activeTask && taskSession.busy) || (!activeTask && running) ? <span className="send-spinner" /> : composerMode === "plan" ? <ListChecks size={17} /> : <ArrowUp size={17} />}</button></div>
            </div>
            {dragActive && <div className="composer-drop-overlay"><Paperclip size={18} /><span>{t("Drop files to attach")}</span></div>}
          </div>
          <p className="composer-hint">{composerMode === "plan" && !activeTask ? (locale === "zh" ? "生成计划不会执行操作；确认后才开始。" : "Planning takes no action. You decide when to start.") : (locale === "zh" ? "Enter 发送 · Shift + Enter 换行" : "Enter to send · Shift + Enter for a new line")}</p>
        </div>
      </section>

      <aside id="studio-canvas-panel" role="tabpanel" aria-labelledby="mobile-canvas-tab" aria-hidden={!canvasOpen} inert={!canvasOpen ? true : undefined} className={clsx("context-canvas", mobileSurface !== "canvas" && "mobile-surface-hidden")}>
        <header className="canvas-header">
          <div className="canvas-tabs" role="tablist" aria-label={t("Canvas")}>
            <button id="canvas-tab-artifact" role="tab" aria-controls="canvas-panel-artifact" aria-selected={canvasTab === "artifact"} className={clsx(canvasTab === "artifact" && "active")} onClick={() => selectCanvasTab("artifact")}>{t("Artifact")}</button>
            {(taskSession.task || hasProposal) && <button id="canvas-tab-task" role="tab" aria-controls="canvas-panel-task" aria-selected={canvasTab === "task"} className={clsx(canvasTab === "task" && "active")} onClick={() => selectCanvasTab("task")}>{locale === "zh" ? "任务" : "Task"} {!hasProposal && <span>{taskSession.state.projection.progress.completed}/{taskSession.state.projection.progress.total}</span>}</button>}
            <button id="canvas-tab-trace" role="tab" aria-controls="canvas-panel-trace" aria-selected={canvasTab === "trace"} className={clsx(canvasTab === "trace" && "active")} onClick={() => selectCanvasTab("trace")}>{t("Trace")} <span>{activeEvents.length}</span></button>
            <button id="canvas-tab-extensions" role="tab" aria-controls="canvas-panel-extensions" aria-selected={canvasTab === "extensions"} className={clsx(canvasTab === "extensions" && "active")} onClick={() => selectCanvasTab("extensions")}>{t("Blocks")} <span>{extensionBlocks.length}</span></button>
            <button id="canvas-tab-context" role="tab" aria-controls="canvas-panel-context" aria-selected={canvasTab === "context"} className={clsx(canvasTab === "context" && "active")} onClick={() => selectCanvasTab("context")}>{t("Context")}</button>
          </div>
          <div className="canvas-actions"><button className="icon-button quiet canvas-close" title={t("Close canvas")} aria-label={t("Close canvas")} onClick={closeCanvas}><PanelRightClose size={14} /></button></div>
        </header>
        <div className="canvas-content">
          <div id="canvas-panel-artifact" role="tabpanel" aria-labelledby="canvas-tab-artifact" aria-hidden={canvasTab !== "artifact"} className={clsx("canvas-panel", canvasTab === "artifact" && "active")}>
            <ArtifactCanvas key={state.thread?.id ?? activeThreadId ?? "new-thread"} enabled={artifactEnabled} threadId={state.thread?.id ?? activeThreadId} refreshKey={taskArtifactRefreshKey(taskSession.task, state.thread?.id ?? activeThreadId)} eventArtifacts={eventArtifacts} citationEvents={artifactCitationEvents} activeRunId={state.activeRunId} running={running && artifactBelongsToLatestTurn && artifactEvent?.payload.streaming !== false} onNotify={showToast} />
          </div>
          {(taskSession.task || hasProposal) && <div id="canvas-panel-task" role="tabpanel" aria-labelledby="canvas-tab-task" aria-hidden={canvasTab !== "task"} className={clsx("canvas-panel", "task-canvas-panel", canvasTab === "task" && "active")}>
            {hasProposal ? <TaskProposalPanel proposal={proposal} confirming={confirmingPlan} onConfirm={() => void confirmPlan()} /> : <TaskCanvas
              session={taskSession.state}
              onSelectStep={taskSession.selectStep}
              onBeginPlanEdit={taskSession.beginPlanEdit}
              onPlanDraftChange={taskSession.changePlanDraft}
              onSavePlan={() => { void taskSession.savePlan().catch((error) => showToast(error instanceof Error ? error.message : (locale === "zh" ? "保存任务计划失败" : "Unable to save Task plan"))); }}
              onCancelPlanEdit={taskSession.cancelPlanEdit}
              onCommand={controlTask}
              onDismissError={taskSession.clearError}
            />}
          </div>}
          <div id="canvas-panel-trace" role="tabpanel" aria-labelledby="canvas-tab-trace" aria-hidden={canvasTab !== "trace"} className={clsx("canvas-panel", canvasTab === "trace" && "active")}>
            <TraceTimeline events={activeEvents} />
          </div>
          <div id="canvas-panel-extensions" role="tabpanel" aria-labelledby="canvas-tab-extensions" aria-hidden={canvasTab !== "extensions"} className={clsx("canvas-panel", canvasTab === "extensions" && "active")}>
            <ExtensionUIBlocks blocks={extensionBlocks} events={activeEvents} context={hostContext} busy={blocked} onSubmit={submitExtensionAction} />
          </div>
          <div id="canvas-panel-context" role="tabpanel" aria-labelledby="canvas-tab-context" aria-hidden={canvasTab !== "context"} className={clsx("canvas-panel", canvasTab === "context" && "active")}>
            <ContextLedger agent={agent} threadId={state.thread?.id ?? null} skills={skills} rules={rules} catalogError={customizationError} assembly={session.contextAssembly} />
          </div>
        </div>
        <footer className="canvas-footer"><span><Clock3 size={12} />{t("Updated just now")}</span><span>{canvasTab === "extensions" ? t("Declarative UI · {count} blocks", { count: extensionBlocks.length }) : canvasTab === "task" ? (locale === "zh" ? "可恢复任务" : "Durable Task") : t(canvasTab === "artifact" ? "Artifact" : canvasTab === "trace" ? "Trace" : "Context")}</span></footer>
      </aside>
      {toast && <Toast message={toast} />}
    </div>
  );
}

function attachmentIssueMessage(issue: AttachmentValidationIssue, t: ReturnType<typeof useI18n>["t"]): string {
  if (issue === "unsupported_type") return t("Use PNG, JPEG, WebP, TXT, MD, PDF, or DOCX files");
  if (issue === "too_large") return t("Images may be up to 5 MiB and documents up to 8 MiB");
  if (issue === "too_many") return t("Attach up to 4 files per message");
  return t("Attachments may total up to 20 MiB per message");
}

function modelProfileLabel(profile: ProviderModelProfile, t: ReturnType<typeof useI18n>["t"]): string {
  const tierKey = tierLabelKey(profile.tier);
  return tierKey ? t(tierKey) : profile.label;
}

function reasoningLabelKey(effort: ReasoningEffort): "Off" | "Low" | "Medium" | "High" {
  if (effort === "none") return "Off";
  if (effort === "low") return "Low";
  if (effort === "medium") return "Medium";
  return "High";
}

function RunProfileMenu({
  ariaLabel,
  icon,
  value,
  options,
  disabled,
  align = "left",
  onChange,
}: {
  ariaLabel: string;
  icon: ReactNode;
  value: string;
  options: Array<{ value: string; label: string; shortLabel?: string; disabled?: boolean }>;
  disabled: boolean;
  align?: "left" | "right";
  onChange: (value: string) => void;
}) {
  const detailsRef = useRef<HTMLDetailsElement>(null);
  const [open, setOpen] = useState(false);
  const selected = options.find((option) => option.value === value) ?? options[0];

  useEffect(() => {
    if (disabled) detailsRef.current?.removeAttribute("open");
  }, [disabled]);

  useEffect(() => {
    const closeOutside = (event: PointerEvent) => {
      const node = detailsRef.current;
      if (node?.open && event.target instanceof Node && !node.contains(event.target)) node.removeAttribute("open");
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !detailsRef.current?.open) return;
      detailsRef.current.removeAttribute("open");
      detailsRef.current.querySelector("summary")?.focus();
    };
    window.addEventListener("pointerdown", closeOutside);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("pointerdown", closeOutside);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, []);

  return (
    <details ref={detailsRef} className={clsx("run-profile-menu", align === "right" && "align-right", disabled && "disabled")} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary role="button" aria-haspopup="listbox" aria-expanded={open} aria-label={ariaLabel} aria-disabled={disabled} title={selected?.label ?? ariaLabel} onClick={(event) => { if (disabled) event.preventDefault(); }}>
        {icon}<span key={value || "agent-default"}>{selected?.shortLabel ?? selected?.label ?? ariaLabel}</span><ChevronDown size={10} aria-hidden="true" />
      </summary>
      <div className="run-profile-popover" role="listbox" aria-label={ariaLabel}>
        <div className="run-profile-popover-label">{ariaLabel}</div>
        {options.map((option) => (
          <button
            type="button"
            role="option"
            aria-selected={option.value === value}
            disabled={option.disabled}
            key={option.value || "agent-default"}
            onClick={() => {
              onChange(option.value);
              detailsRef.current?.removeAttribute("open");
              detailsRef.current?.querySelector("summary")?.focus();
            }}
          >
            <span>{option.label}</span>{option.value === value && <Check size={12} />}
          </button>
        ))}
      </div>
    </details>
  );
}

function TraceTimeline({ events }: { events: ExecutionEvent[] }) {
  const { t } = useI18n();
  return <div className="trace-timeline">{summarizeTraceEvents(events).map((event) => (
    <div className="trace-item" key={event.id}><span className={clsx("trace-node", event.type === "run.completed" && "done")} /><div><strong>{event.type}</strong><p>{event.payload.summary ?? event.payload.result_summary ?? event.payload.label ?? event.payload.status ?? t("Event recorded")}</p><small>#{event.sequence} · {new Date(event.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</small></div></div>
  ))}</div>;
}

function summarizeTraceEvents(events: ExecutionEvent[]): ExecutionEvent[] {
  const visible: ExecutionEvent[] = [];
  for (const event of events) {
    if ((event.type === "message.delta" || event.type === "reasoning.delta") && visible.at(-1)?.type === event.type) continue;
    visible.push(event);
  }
  return visible;
}

function ContextLedger({
  agent,
  threadId,
  skills,
  rules,
  catalogError,
  assembly,
}: {
  agent: Agent;
  threadId: string | null;
  skills: Skill[];
  rules: Rule[];
  catalogError: string | null;
  assembly: ContextAssembly | null;
}) {
  const { t } = useI18n();
  const [view, setView] = useState<"next" | "last">("next");
  const [configuration, setConfiguration] = useState<ThreadConfiguration | null>(null);
  const [activeSkills, setActiveSkills] = useState<string[]>([]);
  const [manualRules, setManualRules] = useState<string[]>([]);
  const [state, setState] = useState<"loading" | "saved" | "unsaved" | "saving" | "error">("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    if (!threadId) {
      setConfiguration(null); setActiveSkills([]); setManualRules([]); setState("saved"); setError(null);
      return () => { active = false; };
    }
    setState("loading"); setError(null);
    void alcuinApi.getThreadConfiguration(threadId).then((next) => {
      if (!active) return;
      setConfiguration(next);
      setActiveSkills(next.active_skill_version_ids);
      setManualRules(next.manual_rule_version_ids);
      setState("saved");
    }).catch((reason) => {
      if (!active) return;
      setState("error");
      setError(reason instanceof Error ? reason.message : t("Unable to load next-turn context"));
    });
    return () => { active = false; };
  }, [threadId, t]);

  const boundSkills = skills.filter((skill) => skill.enabled && (agent.definition.skills ?? []).some((binding) => binding.skill_version_id === skill.current_version_id));
  const alwaysSkills = new Set((agent.definition.skills ?? []).filter((binding) => binding.mode === "always").map((binding) => binding.skill_version_id));
  const selectableSkills = new Set(boundSkills.filter((skill) => isSkillManuallySelectable(skill, alwaysSkills)).map((skill) => skill.current_version_id));
  const boundRuleVersionIds = new Set((agent.definition.rules ?? []).map((binding) => binding.rule_version_id));
  const boundManualRules = rules.filter((rule) => isManualRuleAvailable(rule, boundRuleVersionIds, threadId));
  const automaticRules = rules.filter((rule) => rule.enabled && rule.definition.activation !== "manual" && (rule.scope === "workspace" || (agent.definition.rules ?? []).some((binding) => binding.rule_version_id === rule.current_version_id) || (rule.scope === "thread" && rule.thread_id === threadId)));
  const setSkill = (id: string) => { if (!selectableSkills.has(id)) return; setActiveSkills((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]); setState("unsaved"); };
  const setRule = (id: string) => { setManualRules((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]); setState("unsaved"); };
  async function saveNextTurn() {
    if (!threadId || !configuration || state === "saving") return;
    setState("saving"); setError(null);
    try {
      const next = await alcuinApi.updateThreadConfiguration(threadId, { expected_revision: configuration.revision, active_skill_version_ids: activeSkills.filter((id) => selectableSkills.has(id)), manual_rule_version_ids: manualRules });
      setConfiguration(next);
      setActiveSkills(next.active_skill_version_ids);
      setManualRules(next.manual_rule_version_ids);
      setState("saved");
    } catch (reason) {
      setState("error");
      setError(reason instanceof Error ? reason.message : t("Unable to save next-turn context"));
    }
  }

  return <div className="context-inspector context-ledger">
    <div className="ledger-switch"><button className={clsx(view === "next" && "active")} onClick={() => setView("next")}>{t("Next turn")}</button><button className={clsx(view === "last" && "active")} onClick={() => setView("last")}>{t("Last run")}</button></div>
    {view === "next" ? <>
      <div className="context-ledger-note"><strong>{t("Changes apply from the next message until you change them")}</strong><p>{t("Selections stay with this thread until you change them. They never rewrite the Agent definition or past runs.")}</p></div>
      {!threadId ? <div className="context-empty"><Sparkles size={18} /><strong>{t("Start this thread first")}</strong><span>{t("Send the first message, then choose sticky Skills and Manual Rules for following turns.")}</span></div> : state === "loading" ? <div className="panel-loading"><span className="micro-loader" />{t("Loading context…")}</div> : <>
        {catalogError && <div className="wizard-error">{catalogError}</div>}
        <div className="ledger-section"><div className="ledger-heading"><div><label>{t("Skills")}</label><span>{t("Load extra operating knowledge now")}</span></div><small>{activeSkills.filter((id) => selectableSkills.has(id)).length + alwaysSkills.size}</small></div><div className="ledger-options">{boundSkills.map((skill) => { const forced = alwaysSkills.has(skill.current_version_id); const selectable = isSkillManuallySelectable(skill, alwaysSkills); const selected = forced || (selectable && activeSkills.includes(skill.current_version_id)); return <button className={clsx("ledger-option", selected && "selected")} key={skill.id} onClick={() => setSkill(skill.current_version_id)} disabled={!selectable}><span className="ledger-check">{selected && <Check size={11} />}</span><div><strong>{skillDisplayName(skill)}</strong><small>{forced ? t("Always loaded by Agent") : !skill.definition.user_invocable ? t("Model only; not manually selectable") : skill.definition.description}</small></div></button>; })}{!boundSkills.length && <div className="ledger-empty">{t("No enabled Skills are bound to this Agent.")}</div>}</div></div>
        <div className="ledger-section"><div className="ledger-heading"><div><label>{t("Manual Rules")}</label><span>{t("Explicit boundaries for the next turn")}</span></div><small>{manualRules.length}</small></div><div className="ledger-options">{boundManualRules.map((rule) => { const selected = manualRules.includes(rule.current_version_id); return <button className={clsx("ledger-option", selected && "selected")} key={rule.id} onClick={() => setRule(rule.current_version_id)}><span className="ledger-check">{selected && <Check size={11} />}</span><div><strong>{rule.definition.name}</strong><small>{rule.definition.description || rule.definition.content.slice(0, 90)}</small></div></button>; })}{!boundManualRules.length && <div className="ledger-empty">{t("No Manual Rules are available for this thread.")}</div>}</div></div>
        {automaticRules.length > 0 && <div className="ledger-section passive"><div className="ledger-heading"><div><label>{t("Automatic Rules")}</label><span>{t("Shown for awareness; activation is deterministic")}</span></div><small>{automaticRules.length}</small></div>{automaticRules.map((rule) => <div className="automatic-rule" key={rule.id}><span className={`activation-badge ${rule.definition.activation}`}>{t(rule.definition.activation === "always" ? "Always" : "Conditional")}</span><strong>{rule.definition.name}</strong></div>)}</div>}
        {error && <div className="wizard-error">{error}</div>}
        <div className="ledger-save"><span className={`save-indicator ${state}`}>{t(state === "saved" ? "Saved" : state === "unsaved" ? "Unsaved changes" : state === "saving" ? "Saving…" : "Save failed")}</span><button className="button dark" disabled={state === "saved" || state === "saving"} onClick={() => void saveNextTurn()}><Save size={13} />{t("Save for next turn")}</button></div>
      </>}
    </> : assembly ? <>
      <div className="context-ledger-note immutable"><strong>{t("Immutable safe trace")}</strong><p>{t("This is what the last run received, without message bodies, secrets, or raw Rule and Skill content.")}</p></div>
      <div className="context-metrics"><div><span>{t("Estimated input")}</span><strong>{assembly.estimated_input_tokens.toLocaleString()}</strong><small>tokens</small></div><div><span>{t("Effective budget")}</span><strong>{assembly.effective_budget_tokens.toLocaleString()}</strong><small>tokens</small></div></div>
      <div className="context-group"><label>{t("Assembled context")}</label><div className="context-entry-list">{assembly.entries.map((entry, index) => <div className={clsx("context-entry", !entry.included && "excluded")} key={`${entry.kind}-${entry.label}-${index}`}><span>{entry.kind}</span><strong>{entry.label}</strong><small>{entry.token_estimate == null ? t("Token estimate unavailable") : t("{count} tokens", { count: entry.token_estimate })}</small><span className={clsx("context-entry-status", entry.digest && "verified")}>{entry.digest && <Check size={10} />}{t(entry.digest ? "Verified source" : entry.included ? "Included" : "Not included")}</span></div>)}</div></div>
      <div className="context-group context-provenance"><label>{t("Context provenance")}</label><span>{t("Messages through #{sequence}", { sequence: assembly.message_sequence_through })}</span><span>{assembly.active_compaction_id ? t("Compacted conversation history") : t("Full conversation history")}</span></div>
    </> : <div className="context-empty"><Sparkles size={18} /><strong>{t("Context snapshot unavailable")}</strong><span>{t("Run this thread to inspect the operator-safe assembled context.")}</span></div>}
  </div>;
}
