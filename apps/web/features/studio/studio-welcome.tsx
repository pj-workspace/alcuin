"use client";

import { useRef } from "react";
import { ArrowUpLeft, FileText, Lightbulb, ListChecks } from "lucide-react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";

import { AgentPresenceOrb } from "@/features/studio/agent-presence-orb";
import { useI18n } from "@/shared/lib/i18n";

gsap.registerPlugin(useGSAP);

export function StudioWelcome({
  starterPrompts = [],
  onSelectPrompt,
  disabled = false,
}: {
  starterPrompts?: string[];
  onSelectPrompt: (prompt: string) => void;
  disabled?: boolean;
}) {
  const { locale } = useI18n();
  const container = useRef<HTMLDivElement>(null);
  const zh = locale === "zh";
  const suggestions = [
    { icon: Lightbulb, title: zh ? "理清一个想法" : "Explore an idea", detail: zh ? "从问题出发，找到方向" : "Find a useful starting point", prompt: zh ? "我有一个想法，想和你一起梳理。请先问我最关键的问题，帮助我明确目标和限制：" : "Help me think through an idea. Start by asking the key questions to clarify my goal and constraints: " },
    { icon: FileText, title: zh ? "打磨一份内容" : "Shape a draft", detail: zh ? "让表达清楚，也更有说服力" : "Make the message clear and precise", prompt: zh ? "请帮我修改下面的内容，让结构更清晰、表达更自然，并保留原意：\n\n" : "Help me revise the following draft for clarity and natural language, while preserving its meaning:\n\n" },
    { icon: ListChecks, title: zh ? "拆解一个目标" : "Plan the next steps", detail: zh ? "把目标变成可执行的步骤" : "Turn an outcome into concrete steps", prompt: zh ? "请帮我把这个目标拆解成可执行的步骤，明确每一步的产出和需要确认的问题：" : "Help me break this goal into actionable steps, with an outcome and open questions for each step: " },
  ];

  useGSAP(() => {
    const media = gsap.matchMedia();
    media.add("(prefers-reduced-motion: no-preference)", () => {
      gsap.from("[data-welcome-enter]", { y: 12, autoAlpha: 0, duration: 0.55, stagger: 0.065, ease: "power3.out", clearProps: "transform,opacity,visibility" });
    }, container);
    return () => media.revert();
  }, { scope: container });

  return (
    <div className="studio-welcome" ref={container}>
      <div className="studio-welcome-presence" data-welcome-enter aria-hidden="true"><AgentPresenceOrb state="breathing" active size={68} /></div>
      <h2 data-welcome-enter>{zh ? "让想法，从这里展开。" : "A little room for your next idea."}</h2>
      <p className="studio-welcome-intro" data-welcome-enter>{zh ? "一起思考、打磨内容，或规划下一步。" : "Think it through, shape something useful, or plan what comes next."}</p>
      <div className="studio-welcome-prompts" aria-label={zh ? "选择一个起点" : "Choose a starting point"}>
        {suggestions.map(({ icon: Icon, title, detail, prompt }) => (
          <button type="button" key={title} disabled={disabled} onClick={() => onSelectPrompt(prompt)} data-welcome-enter>
            <Icon size={18} strokeWidth={1.5} aria-hidden="true" />
            <span><strong>{title}</strong><small>{detail}</small></span>
            <ArrowUpLeft className="welcome-prompt-arrow" size={14} aria-hidden="true" />
          </button>
        ))}
      </div>
      {starterPrompts.length > 0 && <div className="studio-welcome-custom" data-welcome-enter>
        <span>{zh ? "也可以从这里开始" : "Or start here"}</span>
        {starterPrompts.slice(0, 2).map((prompt) => <button key={prompt} type="button" disabled={disabled} onClick={() => onSelectPrompt(prompt)}>{prompt}<ArrowUpLeft size={12} aria-hidden="true" /></button>)}
      </div>}
    </div>
  );
}
