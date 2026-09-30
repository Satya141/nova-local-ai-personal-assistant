import { useId } from "react";

/**
 * NOVA's cast: small plush characters that show what the agent is doing.
 * Nova is the assistant itself; the others each own a kind of action and
 * appear as tiny icons beside it.
 */
export type CharacterKind = "nova" | "ember" | "fern" | "plum" | "chime" | "iris";

/** Each state has its own face and motion (see character.css). */
export type CharacterState =
  | "asleep" // backend or model not ready yet
  | "idle"
  | "listening" // the user is typing
  | "thinking" // waiting for the model
  | "working" // a tool is running
  | "speaking" // reply is streaming
  | "confirm" // waiting for the user to approve an action
  | "done"
  | "error"
  | "ringing" // a reminder has come due
  | "hearing"; // listening to the user's voice

const STATE_LABELS: Record<CharacterState, string> = {
  ringing: "ringing with a reminder",
  hearing: "listening to you",
  asleep: "starting up",
  idle: "ready",
  listening: "listening",
  thinking: "thinking",
  working: "working",
  speaking: "replying",
  confirm: "waiting for your approval",
  done: "done",
  error: "reporting a problem",
};

const NAMES: Record<CharacterKind, string> = {
  nova: "Nova",
  ember: "Ember",
  fern: "Fern",
  plum: "Plum",
  chime: "Chime",
  iris: "Iris",
};

/** Silhouettes, in a 64-unit box with the face centred near (32, 35). */
const BODIES: Record<CharacterKind, string> = {
  // A round, slightly bottom-heavy blob.
  nova: "M32 12.5c12.4 0 21.5 8.6 21.5 21.6S44.6 56.5 32 56.5 10.5 47.1 10.5 34.1 19.6 12.5 32 12.5z",
  // A gumdrop: domed top, flat base.
  ember: "M13 49.5V33c0-11.3 8.3-19.5 19-19.5S51 21.7 51 33v16.5c0 3.9-3.1 7-7 7H20c-3.9 0-7-3.1-7-7z",
  // A raindrop with a soft point.
  fern: "M32 9.5c1.2 0 2.3.7 3 1.9C39.500 19 51.5 27.200 51.5 39.200c0 10.2-8.5 17.300-19.5 17.300s-19.5-7.100-19.5-17.300C12.500 27.200 24.500 19 29 11.400c.7-1.200 1.800-1.900 3-1.900z",
  // A wide bean that leans to one side.
  plum: "M21.500 17.500c9.500-5.500 23.500-2.500 29 8.500 5.500 11.500-.5 26-13.500 29.500-12 3.200-24-3.800-26.500-15.500-1.900-9 2.400-17.500 11-22.500z",
  // A soft rounded square, like a little camera.
  iris: "M19.500 15h25c5.800 0 10.500 4.700 10.500 10.500v20c0 5.800-4.700 10.500-10.500 10.500h-25C13.700 56 9 51.300 9 45.500v-20C9 19.700 13.700 15 19.500 15z",
  // A bell: domed crown, flared rim.
  chime: "M32 11c9.500 0 15.500 7.800 15.500 17.500v8.500c0 4 2.200 6.900 5 9.200 1.600 1.300.7 3.800-1.400 3.800H12.900c-2.100 0-3-2.500-1.400-3.800 2.800-2.300 5-5.200 5-9.200v-8.500C16.500 18.800 22.500 11 32 11z",
};

/** Small details that make each silhouette a character. Drawn with the body, so they share its fuzz. */
function Accessory({ kind }: { kind: CharacterKind }) {
  switch (kind) {
    case "ember": // a tuft of hair
      return <path className="character-trim" d="M25.500 15.500c-.5-4.500.5-8 2.200-10.500 1.300 2 2.100 4 2.400 6.200.9-3 2.300-5.400 4.200-7.200.900 3.200 1.300 6.500 1 10 1.200-1.800 2.600-3.200 4.200-4.200.2 2.400-.2 4.500-1.200 6.200z" />;
    case "fern": // a leaf
      return (
        <g className="character-leaf">
          <path className="character-trim" d="M33.500 11.500c.2-5 3.800-8.500 9.800-8.800.3 5.800-3.600 9.700-9.800 8.800z" />
        </g>
      );
    case "plum": // two round ears
      return (
        <>
          <circle className="character-trim" cx="20.500" cy="18.500" r="6" />
          <circle className="character-trim" cx="45.500" cy="19.500" r="6" />
        </>
      );
    case "iris": // a camera bump on top
      return <rect className="character-trim" x="24.500" y="9" width="15" height="9" rx="3.500" />;
    case "chime": // a hanging loop on top and a clapper below the rim
      return (
        <>
          <circle className="character-trim" cx="32" cy="9" r="3.600" />
          <circle className="character-trim character-clapper" cx="32" cy="54" r="4.200" />
        </>
      );
    default:
      return null;
  }
}

export function Character({
  kind = "nova",
  state,
  size = 40,
}: {
  kind?: CharacterKind;
  state: CharacterState;
  size?: number;
}) {
  const id = useId();
  const gradient = `${id}-fill`;
  const fuzz = `${id}-fuzz`;
  // Below this size the felt texture is smaller than a pixel and only costs time.
  const plush = size >= 28;

  return (
    <span
      className="character"
      data-kind={kind}
      data-state={state}
      style={{ width: size, height: size }}
      role="img"
      aria-label={`${NAMES[kind]} is ${STATE_LABELS[state]}`}
    >
      <svg viewBox="0 0 64 64" aria-hidden="true">
        <defs>
          <radialGradient id={gradient} cx="36%" cy="24%" r="88%">
            <stop offset="0%" className="character-stop-hi" />
            <stop offset="48%" className="character-stop-mid" />
            <stop offset="100%" className="character-stop-lo" />
          </radialGradient>
          {plush && (
            <filter id={fuzz} x="-20%" y="-25%" width="140%" height="150%">
              {/* Fine noise frays the outline into fuzz... */}
              <feTurbulence type="fractalNoise" baseFrequency="1.15" numOctaves="2" seed="11" result="noise" />
              <feDisplacementMap in="SourceGraphic" in2="noise" scale="3.4" xChannelSelector="R" yChannelSelector="G" result="frayed" />
              {/* ...and the same noise, as pale flecks, gives the surface its felt grain. */}
              <feColorMatrix in="noise" type="matrix" values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 1.5 -0.62" result="flecks" />
              <feComposite in="flecks" in2="frayed" operator="in" result="grain" />
              <feMerge>
                <feMergeNode in="frayed" />
                <feMergeNode in="grain" />
              </feMerge>
            </filter>
          )}
        </defs>

        {/* thinking: three moons in orbit */}
        <g className="character-orbit">
          <circle cx="32" cy="3.500" r="2.800" />
          <circle cx="58" cy="48" r="2.200" />
          <circle cx="6" cy="48" r="1.700" />
        </g>

        {/* working: a spinning progress ring */}
        <circle className="character-ring" cx="32" cy="34" r="29" />

        {/* Iris at work: a viewfinder's focus brackets */}
        {kind === "iris" && (
          <g className="character-brackets">
            <path d="M5 16V8h8M51 8h8v8M59 52v8h-8M13 60H5v-8" />
          </g>
        )}

        {/* hearing: sound rippling in */}
        <g className="character-sonar">
          <circle cx="32" cy="34" r="27" />
          <circle cx="32" cy="34" r="27" />
        </g>

        <g className="character-float">
          <g className="character-body">
            <g filter={plush ? `url(#${fuzz})` : undefined}>
              <Accessory kind={kind} />
              <path d={BODIES[kind]} fill={`url(#${gradient})`} />
            </g>
            <g className="character-face">
              <g className="character-eyes character-eyes-open">
                <ellipse cx="24.500" cy="35" rx="3.400" ry="4.700" />
                <ellipse cx="39.500" cy="35" rx="3.400" ry="4.700" />
                <circle className="character-glint" cx="25.700" cy="33" r="1.150" />
                <circle className="character-glint" cx="40.700" cy="33" r="1.150" />
              </g>
              <g className="character-eyes character-eyes-happy">
                <path d="M20.400 36.800Q24.500 29.800 28.600 36.800" />
                <path d="M35.400 36.800Q39.500 29.800 43.600 36.800" />
              </g>
              <g className="character-eyes character-eyes-closed">
                <path d="M20.600 34.600Q24.500 38.200 28.400 34.600" />
                <path d="M35.600 34.600Q39.500 38.200 43.400 34.600" />
              </g>
              <g className="character-eyes character-eyes-cross">
                <path d="M21.500 32l6 6m0-6l-6 6" />
                <path d="M36.500 32l6 6m0-6l-6 6" />
              </g>
              <ellipse className="character-mouth" cx="32" cy="44.500" rx="2.800" ry="2.100" />
              <circle className="character-blush" cx="18.500" cy="41" r="3.200" />
              <circle className="character-blush" cx="45.500" cy="41" r="3.200" />
            </g>
          </g>
        </g>

        {/* Nova's spark: a small star that rides beside it */}
        {kind === "nova" && (
          <path className="character-star" d="M51 3c.700 4 2 5.300 6 6-4 .700-5.300 2-6 6-.700-4-2-5.300-6-6 4-.700 5.300-2 6-6z" />
        )}

        {/* done: a burst of sparks */}
        <g className="character-sparks">
          <path d="M8 8c.500 2.600 1.400 3.500 4 4-2.600.500-3.500 1.400-4 4-.500-2.600-1.400-3.500-4-4 2.600-.500 3.500-1.400 4-4z" />
          <path d="M58 22c.400 2 1.100 2.700 3.100 3.100-2 .400-2.700 1.100-3.100 3.100-.400-2-1.100-2.700-3.100-3.100 2-.400 2.700-1.100 3.100-3.100z" />
          <path d="M6 44c.400 2 1.100 2.700 3.100 3.100-2 .400-2.700 1.100-3.100 3.100-.400-2-1.100-2.700-3.100-3.100 2-.400 2.700-1.100 3.100-3.100z" />
        </g>

        {/* ringing: sound waves either side */}
        <g className="character-waves">
          <path d="M7 24c-3 4-3 10 0 14" />
          <path d="M2.500 20c-4.500 6.500-4.500 15.500 0 22" />
          <path d="M57 24c3 4 3 10 0 14" />
          <path d="M61.500 20c4.500 6.500 4.500 15.500 0 22" />
        </g>

        {/* confirm: a question mark. asleep: a drifting z. */}
        <text className="character-mark character-mark-question" x="54" y="17">?</text>
        <text className="character-mark character-mark-sleep" x="53" y="16">z</text>
      </svg>
    </span>
  );
}
