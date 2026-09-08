import type { Skill } from "@alcuin/contracts";

export function skillDisplayName(skill: Skill): string {
  const displayName = skill.definition.metadata.display_name;
  return typeof displayName === "string" && displayName.trim()
    ? displayName.trim()
    : skill.definition.name;
}
