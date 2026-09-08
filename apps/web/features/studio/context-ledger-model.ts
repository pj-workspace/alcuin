import type { Rule, Skill } from "@alcuin/contracts";

export function isSkillManuallySelectable(skill: Skill, alwaysSkillVersionIds: ReadonlySet<string>): boolean {
  return skill.enabled
    && skill.definition.user_invocable
    && !alwaysSkillVersionIds.has(skill.current_version_id);
}

export function isManualRuleAvailable(
  rule: Rule,
  boundRuleVersionIds: ReadonlySet<string>,
  threadId: string | null,
): boolean {
  if (!rule.enabled || rule.definition.activation !== "manual") return false;
  return rule.scope === "workspace"
    || boundRuleVersionIds.has(rule.current_version_id)
    || (rule.scope === "thread" && rule.thread_id === threadId);
}
