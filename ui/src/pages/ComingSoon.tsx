export function ComingSoon({ title, step, description }: { title: string; step: number; description: string }) {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-2 p-4 lg:p-6">
      <h1 className="text-2xl font-semibold">{title}</h1>
      <p className="text-sm text-muted">{description}</p>
      <p className="mt-2 rounded-lg border border-dashed border-line bg-surface p-4 text-sm text-muted">
        This page arrives in build step {step}.
      </p>
    </div>
  );
}
