import type { ReactNode } from "react";

/** Tiny 16-unit line icons, drawn for NOVA. They inherit the text colour. */
function Icon({ size = 14, children }: { size?: number; children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="flex-none"
    >
      {children}
    </svg>
  );
}

type IconProps = { size?: number };

export const WindowIcon = (props: IconProps) => (
  <Icon {...props}>
    <rect x="2" y="3" width="12" height="10" rx="2.2" />
    <path d="M2 6.3h12" />
  </Icon>
);

export const WindowCloseIcon = (props: IconProps) => (
  <Icon {...props}>
    <rect x="2" y="3" width="12" height="10" rx="2.2" />
    <path d="M6.4 6.6l3.2 3.2M9.6 6.6l-3.2 3.2" />
  </Icon>
);

export const SearchIcon = (props: IconProps) => (
  <Icon {...props}>
    <circle cx="7" cy="7" r="4.2" />
    <path d="M10.2 10.2l3.3 3.3" />
  </Icon>
);

export const FolderIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M2 5.1c0-.9.7-1.6 1.6-1.6h2.5l1.5 1.6h4.8c.9 0 1.6.7 1.6 1.6v4.7c0 .9-.7 1.6-1.6 1.6H3.6c-.9 0-1.6-.7-1.6-1.6z" />
  </Icon>
);

export const SparkIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M8 2c.4 3 1.6 4.6 5 6-3.4 1.4-4.6 3-5 6-.4-3-1.6-4.6-5-6 3.4-1.4 4.6-3 5-6z" />
  </Icon>
);

export const ShieldIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M8 2l5 1.8v3.6c0 3-2 5.4-5 6.6-3-1.2-5-3.6-5-6.6V3.8z" />
  </Icon>
);

export const AlertIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M8 2.6l5.8 10.2H2.2z" />
    <path d="M8 6.6v2.8M8 11.3v.1" />
  </Icon>
);

export const ChipIcon = (props: IconProps) => (
  <Icon {...props}>
    <rect x="4.2" y="4.2" width="7.6" height="7.6" rx="1.6" />
    <path d="M6.6 2v2.2M9.4 2v2.2M6.6 11.8V14M9.4 11.8V14M2 6.6h2.2M2 9.4h2.2M11.8 6.6H14M11.8 9.4H14" />
  </Icon>
);

export const CheckIcon = (props: IconProps) => (
  <Icon {...props}>
    <path className="icon-draw" pathLength={1} d="M3.4 8.6l3 3 6.2-6.8" />
  </Icon>
);

export const CrossIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M4.2 4.2l7.6 7.6M11.8 4.2l-7.6 7.6" />
  </Icon>
);

export const MicIcon = (props: IconProps) => (
  <Icon {...props}>
    <rect x="5.6" y="1.8" width="4.8" height="8" rx="2.4" />
    <path d="M3.2 7.6a4.8 4.8 0 0 0 9.6 0M8 12.4v2" />
  </Icon>
);

export const ScreenIcon = (props: IconProps) => (
  <Icon {...props}>
    <rect x="1.8" y="2.6" width="12.4" height="8.6" rx="1.8" />
    <path d="M5.6 14h4.8M8 11.2V14" />
  </Icon>
);

export const StopIcon = (props: IconProps) => (
  <Icon {...props}>
    <rect x="4" y="4" width="8" height="8" rx="1.6" />
  </Icon>
);

export const ReturnIcon = (props: IconProps) => (
  <Icon {...props}>
    <path d="M13 3.5v4.1c0 1-.8 1.9-1.9 1.9H3.6" />
    <path d="M6.1 7L3.6 9.5l2.5 2.5" />
  </Icon>
);

const TOOL_ICONS: Record<string, (props: IconProps) => ReactNode> = {
  open_application: WindowIcon,
  close_application: WindowCloseIcon,
  search_files: SearchIcon,
  open_path: FolderIcon,
};

/** The icon for a tool, by its backend name. Unknown tools get the generic spark. */
export function ToolIcon({ name, size }: { name: string; size?: number }) {
  const Match = TOOL_ICONS[name] ?? SparkIcon;
  return <Match size={size} />;
}
