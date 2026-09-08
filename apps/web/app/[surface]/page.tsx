import { notFound } from "next/navigation";

import { Workbench } from "@/features/shell";

const surfaces = ["studio", "agents", "extensions", "runs", "embed"] as const;
type Surface = (typeof surfaces)[number];

export function generateStaticParams() {
  return surfaces.map((surface) => ({ surface }));
}

export default async function SurfacePage({ params }: { params: Promise<{ surface: string }> }) {
  const { surface } = await params;
  if (!surfaces.includes(surface as Surface)) notFound();
  return <Workbench surface={surface as Surface} />;
}
