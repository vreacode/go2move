import type { PropsWithChildren, ReactNode } from "react";
import { classNames } from "../lib";

type PanelProps = PropsWithChildren<{
  title: string;
  subtitle?: string;
  className?: string;
  right?: ReactNode;
}>;

export function Panel({ title, subtitle, className, right, children }: PanelProps) {
  return (
    <section
      className={classNames(
        "rounded-lg border border-base/70 bg-panel p-6",
        className,
      )}
    >
      <div className="mb-6 flex items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-primary">{title}</h2>
          {subtitle ? <p className="mt-1 text-sm text-muted">{subtitle}</p> : null}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}
