import type { CSSProperties } from "react";

type AlcuinMarkProps = {
  className?: string;
  size?: number;
  title?: string;
};

export function AlcuinMark({ className, size = 28, title }: AlcuinMarkProps) {
  const style = { "--alcuin-mark-size": `${size}px` } as CSSProperties;

  return (
    <svg
      aria-hidden={title ? undefined : true}
      aria-label={title}
      className={className ? `alcuin-mark ${className}` : "alcuin-mark"}
      role={title ? "img" : undefined}
      style={style}
      viewBox="0 0 64 64"
      xmlns="http://www.w3.org/2000/svg"
    >
      {title && <title>{title}</title>}
      <path
        className="alcuin-mark__ink"
        d="M21.5 25.5 34 32.75 22.5 51.5V58.5H6.5Z"
      />
      <path
        className="alcuin-mark__blue"
        d="M27.5 5.5 57.5 35.25V57.5L45.75 52.5V37.5L23.25 24.5Z"
      />
      <circle className="alcuin-mark__blue" cx="34.25" cy="43.25" r="3.75" />
    </svg>
  );
}
