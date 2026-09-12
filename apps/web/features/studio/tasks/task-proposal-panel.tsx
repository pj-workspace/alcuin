"use client";

import { useRef } from "react";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ArrowRight, FilePenLine, ListChecks, RotateCcw, X } from "lucide-react";

import { useI18n } from "@/shared/lib/i18n";
import { AgentPresenceOrb } from "@/features/studio/agent-presence-orb";
import { TaskPlanEditor } from "./task-plan-editor";
import type { TaskProposalController } from "./use-task-proposal";
import "./task-proposal.css";

gsap.registerPlugin(useGSAP);

export function TaskProposalPanel({ proposal, confirming, onConfirm }: {
  proposal: TaskProposalController;
  confirming: boolean;
  onConfirm: () => void;
}) {
  const { locale } = useI18n();
  const zh = locale === "zh";
  const root = useRef<HTMLElement>(null);
  useGSAP(() => {
    const media = gsap.matchMedia();
    media.add("(prefers-reduced-motion: no-preference)", () => {
      gsap.fromTo(root.current, { opacity: 0, y: 8 }, { opacity: 1, y: 0, duration: 0.22, ease: "power2.out", clearProps: "opacity,transform" });
    });
    return () => media.revert();
  }, { scope: root, dependencies: [proposal.phase], revertOnUpdate: true });

  return (
    <section className="task-proposal" ref={root} aria-label={zh ? "任务计划预览" : "Task plan preview"}>
      <header className="proposal-heading">
        <span className="proposal-symbol"><ListChecks size={18} /></span>
        <div><small>{zh ? "从目标到行动" : "FROM INTENT TO ACTION"}</small><h2>{zh ? "先把计划想清楚" : "A plan before action"}</h2></div>
        <button className="icon-button quiet" disabled={confirming} onClick={proposal.cancel} aria-label={zh ? "关闭计划" : "Close plan"}><X size={16} /></button>
      </header>
      {proposal.phase === "generating" ? (
        <div className="proposal-generating" role="status">
          <AgentPresenceOrb state="breathing" active size={38} />
          <h3>{zh ? "正在拆解你的目标…" : "Working out the steps…"}</h3>
          <p>{zh ? "结合当前 Agent 的能力，准备可调整的执行计划。" : "Preparing an editable plan around this Agent’s capabilities."}</p>
          <blockquote>{proposal.goal}</blockquote>
          <div className="proposal-skeleton" aria-hidden="true"><i /><i /><i /></div>
          <button className="button secondary" onClick={proposal.cancel}>{zh ? "取消生成" : "Cancel generation"}</button>
        </div>
      ) : proposal.phase === "error" ? (
        <div className="proposal-error" role="alert">
          <h3>{zh ? "这次没能生成计划" : "Couldn’t prepare this plan"}</h3>
          <p>{zh ? "可以重试，或自行写下步骤后执行。" : "Try again, or write the steps yourself."}</p>
          <details><summary>{zh ? "查看原因" : "Details"}</summary><p>{proposal.error}</p></details>
          <div><button className="button secondary" onClick={() => void proposal.generate(proposal.goal, proposal.profile)}><RotateCcw size={14} />{zh ? "重试" : "Retry"}</button><button className="button secondary" onClick={proposal.writeManually}><FilePenLine size={14} />{zh ? "手动写计划" : "Write a plan"}</button></div>
        </div>
      ) : proposal.draft ? (
        <>
          <div className="proposal-note"><span>{zh ? "尚未执行 · 确认后逐步开始" : "Not started · Runs after your confirmation"}</span><p>{zh ? "可以修改步骤、调整顺序，或删去不需要的部分。" : "Edit, reorder, or remove steps to make the plan yours."}</p></div>
          <TaskPlanEditor proposal draft={proposal.draft} maxSteps={8} busy={confirming} onChange={proposal.changeDraft} onSave={onConfirm} onCancel={proposal.cancel} submitLabel={zh ? "确认并执行" : "Confirm & run"} />
          <footer className="proposal-footnote"><ArrowRight size={12} /><span>{proposal.model ? (zh ? "AI 生成草案，执行沿用此次模型设置" : "AI draft · Uses the same model settings to execute") : (zh ? "手动计划" : "Manual plan")}</span></footer>
        </>
      ) : null}
    </section>
  );
}
